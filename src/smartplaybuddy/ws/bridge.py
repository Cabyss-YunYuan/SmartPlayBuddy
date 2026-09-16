"""
本地 WebSocket 桥。

在 client 内启动一个只监听 127.0.0.1 的 WebSocket 服务，供内嵌 Web 控制台连接，
从而让服务端只看到 client 这一条连接。职责：
  - 用一次性会话 token(经子协议协商) + Origin 校验把守本地端口；
  - 复刻 text+binary 双帧协议与 Web SDK 通信；
  - 按目标地址分流：
      * to 明确指向本机 client 设备的命令 → 就地交给 Client 执行，结果直接回给网页；
      * 其它(含 to 留空) → 透传到服务端那条唯一连接；
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
from . import message
from .message.message import Message

logger = logger.logger.getChild("LocalBridge")

#: 本地端口鉴权失败时使用的关闭码(4000-4999 属 WebSocket 应用私有区间)。
CLOSE_CODE_UNAUTHORIZED = 4401
CLOSE_CODE_BAD_ORIGIN = 4403

#: 网页控制本机时统一使用的 From 标识。仅用于本机 _active_streams 记账，不经服务端。
LOCAL_WEB_ADDRESS = "web:local"


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

    async def send_json(self, payload: str):
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
        logger.info(i18n.translate("bridge.started", port=self._port))

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
        logger.info(i18n.translate("bridge.web_connected", origin=origin or "-"))
        try:
            await self._recv_loop(connection, reply)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(i18n.translate("bridge.handler_error", error=e), exc_info=True)
        finally:
            self._drop_conn(reply)
            logger.info(i18n.translate("bridge.web_disconnected"))

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

    def _is_local_target(self, to: str | None) -> bool:
        # 仅当 to 明确指向本机 client 设备(同 type + 同 deviceName)才本地执行；
        # to 留空或指向其它设备一律转发到服务端。
        # deviceName 已被清洗为无冒号字符，按 ":" 三段切分可靠。
        if not to:
            return False
        parts = to.split(":")
        if len(parts) != 3:
            return False
        dev_type, _uid, device_name = parts
        return dev_type == "client" and device_name == self._client.device_name

    async def _dispatch(self, msg: Message, reply: _WebReply):
        if self._is_local_target(msg.To):
            # 本机控制：强制统一 From，保证 start/stop 流的记账一致
            msg.From = LOCAL_WEB_ADDRESS
            asyncio.create_task(self._client.execute_local(msg, reply))
            return

        operate = msg.Data.get("operate") if isinstance(msg.Data, dict) else None
        payload = msg.to_json()  # to_json 会在缺失时补全并回填 RequestID
        rid = msg.RequestID

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
                await self._client.send(payload)
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
                await reply.send_json(msg.to_json())
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
        try:
            stop = Message(
                Type="command",
                Action=st.action,
                To=st.to,
                Data={"operate": "stop_stream", "stream_id": stream_id},
            )
            await self._client.send(stop.to_json())
            logger.info(i18n.translate("bridge.orphan_stream_stop", stream_id=stream_id, to=st.to))
        except Exception as e:
            logger.debug(i18n.translate("bridge.orphan_stream_stop_failed", error=e))
