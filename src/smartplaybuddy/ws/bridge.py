"""
本地 WebSocket 桥。

在 client 内启动一个只监听 127.0.0.1 的 WebSocket 服务，供内嵌 Web 控制台连接，
从而让服务端只看到 client 这一条连接。职责：
  - 用一次性会话 token(经子协议协商) + Origin 校验把守本地端口；
  - 复刻 text+binary 双帧协议与 Web SDK 通信；
  - 网页在桥模式下不 claim(见 wsClient.claim)：设备身份始终由桌面 client 那条唯一连接持有；
  - 按目标地址分流(仅上行)：
      * to 指向本机 client 设备的命令 → 就地交给 Client 执行，结果直接回给网页；
      * 其余(含 claim、to 留空、远端地址) → 透传到服务端那条唯一连接；
  - 回程镜像：服务端下发的所有消息无条件转发回内嵌窗口，无论本地是否处理过。
"""
import asyncio
import fnmatch
import json
import secrets
from dataclasses import dataclass

import websockets

from .. import i18n
from .. import logger
from ..config import Config
from . import logic
from . import message
from .message.message import Message

logger = logger.logger.getChild("LocalBridge")

#: 本地端口鉴权失败时使用的关闭码(4000-4999 属 WebSocket 应用私有区间)。
CLOSE_CODE_UNAUTHORIZED = 4401
CLOSE_CODE_BAD_ORIGIN = 4403

#: 本地记账地址前缀，形如 local:{conn}。网页在桥模式下不 claim、无服务端身份，
#: 桥为每条网页连接分配一个本地记账地址，仅用作就地执行时的 msg.From(流记账/回流键)。
#: 刻意避开服务端合法设备类型(client/mod/web)，因此永不可能是服务端可路由地址，也永不发往服务端。
INTERNAL_ADDRESS_PREFIX = "local"


def _resolve_uid() -> str:
    """从登录令牌解析出的 Config.user 里取用户 id(键名固定为 uid)。
    必须与服务端 devicekey.Address 使用的 userInfo.Id(即 IAM claims.UID) 一致。"""
    user = getattr(Config, "user", None) or {}
    val = user.get("uid")
    return "" if val is None else str(val)


class _WebPongSender:
    """轻量 wrapper：把 logic.system 的 send 调用重定向到 web 回复通道。"""
    __slots__ = ("_reply",)

    def __init__(self, reply: "_WebReply"):
        self._reply = reply

    async def send(self, payload: str):
        await self._reply.send_json(payload)


@dataclass
class _StreamTarget:
    """一条由网页发起、转发到远端设备的流登记项。"""
    reply: "_WebReply"
    to: str
    action: str


class _WebReply:
    """面向单个网页连接的回复通道，接口与 client._ServerReply 一致，
    供 Client 执行本机命令时把结果写回网页。"""

    def __init__(self, connection):
        self._conn = connection
        self._lock = asyncio.Lock()
        self.address: str = f"{INTERNAL_ADDRESS_PREFIX}:{id(self):x}"

    async def send_json(self, payload):
        if isinstance(payload, message.Message):
            payload = payload.to_json()
        async with self._lock:
            await self._conn.send(payload)

    async def send_pair(self, meta: Message, binary: bytes):
        async with self._lock:
            await self._conn.send(meta.to_json())
            await self._conn.send(binary)

    async def error(self, data, To: str | None = None, RequestID: str | None = None):
        await self.send_json(message.error.error(data, To=To, RequestID=RequestID))


class LocalBridge:
    """本地 WebSocket 服务：把内嵌网页的控制流量汇聚到 client 的唯一服务端连接。"""

    def __init__(self, client, allowed_origins: list[str] | None = None):
        self._client = client
        self._allowed_origins = allowed_origins or []
        self._token = secrets.token_urlsafe(32)
        self._port: int | None = None
        self._server = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        #: 所有活跃的内嵌窗口连接，用于回程消息的无条件镜像
        self._conns: set["_WebReply"] = set()
        #: stream_id -> _StreamTarget，仅用于网页断开时停掉其发起的远端流
        self._streams: dict[str, _StreamTarget] = {}

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self._port}"

    async def start(self):
        """选定空闲端口并启动本地服务，直到端口就绪才返回。"""
        from ..user.login import _find_free_port, _is_port_available

        self._port = _find_free_port()
        if not _is_port_available(self._port):
            raise RuntimeError(i18n.translate("bridge.no_free_port"))
        self._task = asyncio.create_task(self._serve())
        await self._ready.wait()
        if self._server is None:
            raise RuntimeError(i18n.translate("bridge.serve_failed", error=i18n.translate("bridge.server_not_ready")))
        logger.debug(i18n.translate("bridge.started", port=self._port))

    async def _serve(self):
        try:
            async with websockets.serve(
                self._handler,
                "127.0.0.1",
                self._port,
                subprotocols=[self._token],
                max_size=None,
                compression=None,
            ) as server:
                self._server = server
                self._ready.set()
                await server.serve_forever()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(i18n.translate("bridge.serve_failed", error=e), exc_info=True)
        finally:
            self._server = None
            self._ready.set()

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._server = None
        self._conns.clear()
        self._streams.clear()
        logger.debug(i18n.translate("bridge.stopped"))

    def _origin_allowed(self, origin: str | None) -> bool:
        # 非浏览器客户端一般不带 Origin，放行；带 Origin 时须命中白名单(支持 * 通配)。
        if not origin or not self._allowed_origins:
            return True
        origin = origin.rstrip("/")
        return any(fnmatch.fnmatch(origin, p.rstrip("/")) for p in self._allowed_origins)

    async def _handler(self, connection):
        # 子协议即会话 token：协商不上说明不是被注入 token 的可信内嵌页面。
        if connection.subprotocol != self._token:
            logger.warning(i18n.translate("bridge.unauthorized"))
            await connection.close(code=CLOSE_CODE_UNAUTHORIZED, reason="unauthorized")
            return

        origin = None
        try:
            origin = connection.request.headers.get("Origin")
        except Exception:
            pass
        if not self._origin_allowed(origin):
            logger.warning(i18n.translate("bridge.bad_origin", origin=origin))
            await connection.close(code=CLOSE_CODE_BAD_ORIGIN, reason="origin not allowed")
            return

        reply = _WebReply(connection)
        self._conns.add(reply)
        logger.info(i18n.translate("bridge.console_connected", origin=origin or "-"))
        try:
            await self._recv_loop(connection, reply)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(i18n.translate("bridge.handler_error", error=e), exc_info=True)
        finally:
            self._drop_conn(reply)
            logger.debug(i18n.translate("bridge.console_disconnected"))

    async def _recv_loop(self, connection, reply: _WebReply):
        pending: Message | None = None
        while True:
            raw = await connection.recv()

            if isinstance(raw, bytes):
                if pending is None:
                    logger.warning(i18n.translate("bridge.binary_without_text"))
                    continue
                pending.BinaryData = raw
                await self._dispatch(pending, reply)
                pending = None
                continue

            try:
                d = json.loads(raw)
                msg = Message.from_json(d)
            except json.JSONDecodeError:
                logger.error(i18n.translate("bridge.msg_parse_failed", msg=raw))
                continue
            except KeyError as e:
                logger.error(i18n.translate("bridge.msg_field_missing", field=e.args[0], msg=raw))
                await reply.error(i18n.translate("bridge.missing_field", field=e.args[0]), RequestID=d.get("requestId"))
                continue

            self._normalize_data(msg)

            wants_binary = bool(d.get("binary")) or (
                isinstance(msg.Data, dict) and msg.Data.get("__binary__")
            )
            if wants_binary:
                if isinstance(msg.Data, dict):
                    msg.Data.pop("__binary__", None)
                pending = msg
                continue

            await self._dispatch(msg, reply)

    @staticmethod
    def _normalize_data(msg: Message):
        # 与 Connector.loop 一致：Data 可能是二次 Base64 编码的 JSON 串。
        if isinstance(msg.Data, str):
            import base64 as _b64
            try:
                msg.Data = json.loads(_b64.b64decode(msg.Data).decode("utf-8"))
            except Exception:
                try:
                    msg.Data = json.loads(msg.Data)
                except (json.JSONDecodeError, ValueError):
                    pass

    def _client_address_prefix(self) -> str:
        """本机 client 在服务端的地址前缀：client:{uid}:{clientDeviceName}。
        也是 mod 扩展地址 client:{uid}:{clientDeviceName}:{mod} 的前三段。
        uid 未就绪时返回空串。"""
        uid = _resolve_uid()
        if not uid:
            return ""
        return f"client:{uid}:{self._client.device_name}"

    def _is_local_target(self, to: str | None) -> bool:
        # 上行只拦"to 精确指向本机设备本身"的消息就地执行；命中以下任一即本地：
        #   1) 本地记账地址 local[:{conn}]——桥内部标识，永不发往服务端；
        #   2) 本机 client 地址 client:{uid}:{deviceName}(精确三段)。
        # 带 :{mod} 子通道的地址客户端不解释——透传服务端按 base 回环、再由 web 分流，
        # 客户端不参与 mod 管理。
        if not to:
            return False
        if to == INTERNAL_ADDRESS_PREFIX or to.startswith(INTERNAL_ADDRESS_PREFIX + ":"):
            return True
        prefix = self._client_address_prefix()
        if prefix:
            return to == prefix
        # uid 未就绪时兜底：仅按精确三段 type+uid+deviceName 判定本机设备本身。
        parts = to.split(":")
        return len(parts) == 3 and parts[0] == "client" and parts[2] == self._client.device_name

    async def _dispatch(self, msg: Message, reply: _WebReply):
        local = self._is_local_target(msg.To)

        # 本机网页的 system/* 消息全部交给全局 logic.system 处理（ping→pong、pong→延迟统计等），
        # 通过 _WebPongSender 把回复重定向回 web（而非服务端）。
        if local and msg.Type == "system":
            logic.system(_WebPongSender(reply), msg)
            return

        if local:
            # 保留网页原始 From 用于响应/帧路由(平台按 To 匹配 mod iframe)；
            # reply.address 仅作为流记账 key，不写入 msg.From。
            if not msg.From:
                msg.From = reply.address
            asyncio.create_task(self._client.execute_local(msg, reply, stream_key=reply.address))
            return

        operate = msg.Data.get("operate") if isinstance(msg.Data, dict) else None
        rid = msg.RequestID

        # 客户端尚未连上服务端(启动竞态 / 断线重连中)：无法透传。明确回执网页稍后重试，
        # 并按 DEBUG 记录——这是可自愈的瞬态，不该当转发失败刷 ERROR。
        if not self._client.connected:
            logger.debug(i18n.translate("bridge.forward_not_connected"))
            await reply.error(i18n.translate("bridge.forward_not_connected"), RequestID=rid)
            return

        # 仅登记流(用于网页断开时停掉远端流)；一次性命令的回程由 broadcast 无条件镜像
        if operate == "start_stream":
            self._streams[rid] = _StreamTarget(reply=reply, to=msg.To, action=msg.Action)
        elif operate == "stop_stream":
            sid = msg.Data.get("stream_id") if isinstance(msg.Data, dict) else None
            if sid:
                self._streams.pop(sid, None)
            else:
                for stream_id, st in list(self._streams.items()):
                    if st.reply is reply:
                        self._streams.pop(stream_id, None)

        try:
            if msg.BinaryData is not None:
                await self._client.send_pair(msg, msg.BinaryData)
            else:
                await self._client.send(msg)
        except Exception as e:
            self._streams.pop(rid, None)
            logger.error(i18n.translate("bridge.forward_failed", error=e))
            await reply.error(i18n.translate("bridge.forward_failed", error=e), RequestID=rid)

    def broadcast_to_web(self, msg: Message) -> None:
        """把服务端下发的任意消息镜像回所有活跃的内嵌窗口连接(无论本地是否处理过)。"""
        if not self._conns:
            return
        for reply in list(self._conns):
            asyncio.create_task(self._relay(msg, reply))

    async def _relay(self, msg: Message, reply: _WebReply):
        try:
            if msg.BinaryData is not None:
                import copy
                out = copy.copy(msg)
                if isinstance(out.Data, dict):
                    out.Data = {**out.Data, "__binary__": True}
                out.Binary = True
                await reply.send_pair(out, msg.BinaryData)
            else:
                await reply.send_json(msg)
        except Exception as e:
            logger.error(i18n.translate("bridge.relay_failed", error=e))

    def _drop_conn(self, reply: _WebReply):
        """网页断开：移出镜像集合，并主动停掉它发起的远端流，避免空转。"""
        self._conns.discard(reply)
        for stream_id, st in list(self._streams.items()):
            if st.reply is reply:
                self._streams.pop(stream_id, None)
                asyncio.create_task(self._stop_remote_stream(st, stream_id))

    async def _stop_remote_stream(self, st: _StreamTarget, stream_id: str):
        # 客户端已断线：远端流随那条连接一起失效，无需(也无法)再发 stop。
        if not self._client.connected:
            return
        try:
            stop = Message(
                Type="command",
                Action=st.action,
                To=st.to,
                Data={"operate": "stop_stream", "stream_id": stream_id},
            )
            await self._client.send(stop)
            logger.info(i18n.translate("bridge.orphan_stream_stop", stream_id=stream_id, to=st.to))
        except Exception as e:
            logger.debug(i18n.translate("bridge.orphan_stream_stop_failed", error=e))
