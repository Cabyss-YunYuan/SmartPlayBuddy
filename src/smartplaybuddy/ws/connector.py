"""
WebSocket 连接器基类。
处理 text + binary 双帧协议：当 text 帧标记 __binary__=true 时，
等待紧随其后的 binary 帧完成配对，再派发到 main()。
连接断开后由监督循环 run() 按指数退避自动重连。
"""
import asyncio
import random
import time
import websockets
import json
from abc import ABC, abstractmethod
from .. import i18n
from .. import logger
from . import logic
from . import message

try:  # websockets >= 14 抛 InvalidStatus，旧版本抛 InvalidStatusCode
    from websockets.exceptions import InvalidStatus as _InvalidStatus
except ImportError:  # pragma: no cover
    from websockets.exceptions import InvalidStatusCode as _InvalidStatus

logger = logger.logger.getChild("Connector")

# 服务端在 access token 被吊销(他处登出)时使用的关闭码，见 claimlogic.go closeCodeTokenRevoked。
# 此时 refresh token 通常一并被吊销，只能重新走浏览器登录。
CLOSE_CODE_TOKEN_REVOKED = 4001


class Connector(ABC):
    """WebSocket 连接器抽象基类，子类需实现 main() 处理业务消息。"""
    conn: websockets.ClientConnection

    System: "SystemCls"
    Session: "SessionCls"
    Error: "ErrorCls"

    #: 是否在每次连接前自动确保 access token 可用(过期刷新、被吊销则重新登录)。
    #: 依赖 user 模块与系统凭据管理器；自行管理 Authorization 头时置为 False。
    auto_auth = False

    #: 重连退避参数(秒)。RECONNECT_MAX_ATTEMPTS 为 0 表示无限重试。
    RECONNECT_BASE_DELAY = 1.0
    RECONNECT_MAX_DELAY = 30.0
    RECONNECT_MAX_ATTEMPTS = 0

    #: claim 生效观察窗口(秒)。窗口内收到"无 from 的 error"即判定 claim 被拒。
    CLAIM_WINDOW = 10.0

    def __init__(self, **config):
        self.url = config.get("url", "ws://smtplay.cabyss.cn:2508/ws")
        self.config = config
        self.close_code: int | None = None
        self.reconnect_attempts = 0
        self._stop_event = asyncio.Event()
        self._claim_pending = False
        self._connected_at = 0.0
        # text(binary=true) 与其后的 binary 帧必须成对写出：
        # 服务端 ReadLoop 用 lastText* 缓存做配对，中间插入任何其他文本帧都会错配。
        self._send_lock = asyncio.Lock()
        try:
            self.connection = asyncio.create_task(self.run())
        except Exception as e:
            self.connection = None
            logger.error(i18n.translate("connector.task_create_failed", error=e))

    @property
    def stopped(self) -> bool:
        """监督循环是否已被要求停止。"""
        return self._stop_event.is_set()

    async def send(self, payload: str | bytes):
        async with self._send_lock:
            await self.conn.send(payload)

    async def send_pair(self, meta: "message.Message", binary: bytes):
        """原子发送元数据帧 + 二进制帧。"""
        async with self._send_lock:
            await self.conn.send(meta.to_json())
            await self.conn.send(binary)

    async def close(self):
        """主动停止：不再重连，并关闭当前连接。"""
        self._stop_event.set()
        await self._drop_connection()

    async def _drop_connection(self):
        conn = getattr(self, "conn", None)
        if conn is None:
            return
        try:
            await conn.close()
        except Exception:
            pass

    async def run(self):
        """监督循环：准备令牌 → 连接 → 断开 → 退避重连，直到 close() 或放弃。"""
        while not self._stop_event.is_set():
            if not await self.prepare_connection():
                logger.error(i18n.translate("connector.stopped"))
                break

            self.close_code = None
            await self.connect(self.config)
            if self._stop_event.is_set():
                break

            self.reconnect_attempts += 1
            if self.RECONNECT_MAX_ATTEMPTS and self.reconnect_attempts >= self.RECONNECT_MAX_ATTEMPTS:
                logger.error(i18n.translate("connector.reconnect_gave_up", attempts=self.reconnect_attempts))
                break

            if not await self._wait_backoff(self.reconnect_attempts):
                break
        logger.debug(i18n.translate("connector.loop_exited"))

    async def prepare_connection(self) -> bool:
        """连接前准备。返回 False 表示放弃重连。"""
        if not self.auto_auth:
            return True

        from .. import user
        from ..user.login import ACCESS_COOKIE_NAME

        revoked = self.close_code == CLOSE_CODE_TOKEN_REVOKED
        if revoked:
            logger.warning(i18n.translate("connector.token_revoked"))

        try:
            tokens = await user.ensure_tokens(None, revoked)
        except Exception as e:
            logger.error(i18n.translate("connector.auth_failed", error=e))
            return False

        if not isinstance(self.config.get("headers"), dict):
            self.config["headers"] = {}
        self.config["headers"]["Cookie"] = f"{ACCESS_COOKIE_NAME}={tokens.access_token}"
        return True

    def _backoff_delay(self, attempt: int) -> float:
        delay = min(self.RECONNECT_MAX_DELAY, self.RECONNECT_BASE_DELAY * 2 ** min(attempt - 1, 16))
        # 抖动：避免同一用户的多台设备在服务端重启后同时重连
        return delay + random.uniform(0, delay * 0.2)

    async def _wait_backoff(self, attempt: int) -> bool:
        """等待退避时长；期间被 close() 打断返回 False。"""
        delay = self._backoff_delay(attempt)
        logger.warning(i18n.translate("connector.reconnect_in",
                                      attempt=attempt, delay=f"{delay:.1f}", code=self.close_code))
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            return False
        except asyncio.TimeoutError:
            return True

    async def connect(self, config):
        """建立一次连接并处理消息直到连接结束。异常不外抛，由 run() 决定是否重连。"""
        logger.debug(i18n.translate("message.connecting"))
        try:
            self.conn = await websockets.connect(
                self.url,
                additional_headers=config.get("headers"),
                max_size=None,
                compression=None,
            )
            if self.reconnect_attempts:
                logger.info(i18n.translate("connector.reconnected", attempt=self.reconnect_attempts))
            self.reconnect_attempts = 0
            self._connected_at = time.monotonic()
            logger.debug(i18n.translate("message.connect_success"))

            self.System = self.SystemCls(self.conn)
            self.Session = self.SessionCls(self.conn)
            self.Error = self.ErrorCls(self.conn)

            # 向服务端声明设备状态
            self._claim_pending = True
            await self.Session.claims(config.get("status"))

            await self.loop()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.error(i18n.translate("message.connect_timeout"))
        except _InvalidStatus as e:
            # 握手被拒：missing / invalid / revoked token，刷新令牌后重连
            status = getattr(getattr(e, "response", None), "status_code", None)
            self.close_code = status
            logger.warning(i18n.translate("connector.handshake_rejected", code=status, error=e))
        except websockets.exceptions.ConnectionClosed as e:
            if e.rcvd is not None:
                self.close_code = e.rcvd.code
                if e.rcvd.code != 1000:
                    logger.error(i18n.translate("connector.connect_closed_error", code=e.rcvd.code, reason=e.rcvd.reason))
            logger.debug(i18n.translate("message.connect_closed"))
        except OSError as e:
            # 涵盖 ConnectionRefusedError / socket.gaierror / 网络不可达
            logger.warning(i18n.translate("message.connect_server_failed"))
            logger.debug(str(e))
        except Exception as e:
            logger.error(i18n.translate("connector.connect_failed", error=e), exc_info=True)
        finally:
            self._claim_pending = False
            self.on_close()

    def on_close(self):
        pass

    async def loop(self):
        """消息主循环：接收 text/binary 帧，配对后派发到 main()。"""
        pending = None
        while True:
            try:
                raw = await self.conn.recv()

                # 二进制帧：与前置 pending 的 text 帧配对
                if isinstance(raw, bytes):
                    logger.debug(i18n.translate("connector.binary_received", size=len(raw)))
                    if pending is not None:
                        pending.BinaryData = raw
                        msg = pending
                        pending = None
                        logger.debug(i18n.translate("connector.binary_paired", type=msg.Type, action=msg.Action))
                    else:
                        logger.warning(i18n.translate("connector.binary_without_text"))
                        continue
                # 文本帧：解析 JSON 并检查是否需要等待后续二进制帧
                else:
                    try:
                        d = json.loads(raw)
                        msg = self.Message.from_json(d)
                        logger.debug(i18n.translate("connector.msg_received", msg=msg))
                    except json.decoder.JSONDecodeError:
                        logger.error(i18n.translate("connector.msg_parse_failed", msg=raw))
                        continue
                    except KeyError as e:
                        logger.error(i18n.translate("connector.msg_field_missing", field=e.args[0], msg=raw))
                        await self.Error.error(d, To=d.get("from"), RequestID=d.get("requestId"))
                        continue

                    # Data 为 Base64 编码的 JSON，尝试解码
                    if isinstance(msg.Data, str):
                        import base64 as _b64
                        try:
                            decoded = json.loads(_b64.b64decode(msg.Data).decode("utf-8"))
                            msg.Data = decoded
                        except Exception:
                            try:
                                msg.Data = json.loads(msg.Data)
                            except (json.JSONDecodeError, ValueError):
                                pass

                    # 标记 __binary__ 的消息需要等待后续二进制帧
                    if isinstance(msg.Data, dict) and msg.Data.pop("__binary__", False):
                        pending = msg
                        logger.debug(i18n.translate("connector.pending_set"))
                        continue

                # 服务端自身产生的 error 没有 from(claim 被拒 / 未知消息类型 / 路由失败)。
                if msg.Type == "error" and not msg.From:
                    if self._claim_pending and time.monotonic() - self._connected_at <= self.CLAIM_WINDOW:
                        # claim 被拒时连接依然"健康"，但本连接没有任何设备身份，
                        # 之后所有消息都会被服务端以 "device not found in connection status" 拒绝。
                        # 必须主动断开触发重连：服务端要等 PongWait(60s) 才回收残留会话，
                        # 退避重连几轮后即可 claim 成功。
                        self._claim_pending = False
                        logger.error(i18n.translate("connector.claim_rejected", reason=msg.Data))
                        await self._drop_connection()
                        break
                    # 其余无 from 的服务端 error 不再 continue：交给 main() 镜像给网页并记录

                # 收到任何带 from 的消息说明服务端已按本设备地址完成路由，claim 必然已生效
                if msg.From:
                    self._claim_pending = False

                # 系统消息(pong 等)先走内部逻辑，随后与其余消息一并派发到 main()，
                # 由 main() 无条件镜像回内嵌网页；不再 continue，否则网页永远收不到 pong。
                if msg.Type == "system":
                    logic.system(self, msg)
                await self.main(msg)
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                logger.error(i18n.translate("connector.loop_exception", error=e), exc_info=True)
                break
        logger.debug(i18n.translate("connector.loop_exited"))

    @abstractmethod
    async def main(self, msg: "Message") -> None:
        """子类实现：处理接收到的业务消息。"""
        logger.debug(i18n.translate("connector.msg_received", msg=msg))


    Message = message.Message


    class SystemCls:
        def __init__(self, conn: websockets.ClientConnection):
            self.conn = conn

        async def ping(self):
            await self.conn.send(message.system.ping())


    class SessionCls:
        def __init__(self, conn: websockets.ClientConnection):
            self.conn = conn

        async def claims(self, status: dict):
            await self.conn.send(message.session.claim(status))


    class ErrorCls:
        def __init__(self, conn: websockets.ClientConnection):
            self.conn = conn

        async def error(self, data, To: str | None = None, RequestID: str | None = None):
            if not To:
                # 无 to 的消息一般由服务端自行处理，本地无处可回，静默丢弃(不再告警刷屏)
                return
            await self.conn.send(message.error.error(data, To=To, RequestID=RequestID))
