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
from ..utils import i18n
from ..utils import logger
from ..utils import translate
from . import logic
from . import message
from .stream import BandwidthController

try:  # websockets >= 14 抛 InvalidStatus，旧版本抛 InvalidStatusCode
    from websockets.exceptions import InvalidStatus as _InvalidStatus
except ImportError:  # pragma: no cover
    from websockets.exceptions import InvalidStatusCode as _InvalidStatus

logger = logger.getChild("Connector")

# 服务端在 access token 被吊销(他处登出)时使用的关闭码，见 claimlogic.go closeCodeTokenRevoked。
# 此时 refresh token 通常一并被吊销，只能重新走浏览器登录。
CLOSE_CODE_TOKEN_REVOKED = 4001


class Connector(ABC):
    """WebSocket 连接器抽象基类，子类需实现 main() 处理业务消息。"""
    conn: "websockets.ClientConnection | None"

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
        self.conn: "websockets.ClientConnection | None" = None
        self.close_code: int | None = None
        self.reconnect_attempts = 0
        self._stop_event = asyncio.Event()
        self._claim_pending = False
        self._connected_at = 0.0
        self.session_info: dict | None = None
        self._send_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        #: 全局上行带宽控制器(仅服务端链路生效，重连时重置)
        self._bw_controller = BandwidthController()
        try:
            self.connection = asyncio.create_task(self.run())
        except Exception as e:
            self.connection = None
            logger.error(i18n.translate("connector.task_create_failed", error=e))

    @property
    def stopped(self) -> bool:
        """监督循环是否已被要求停止。"""
        return self._stop_event.is_set()

    @property
    def connected(self) -> bool:
        """服务端连接是否已建立、可用于转发。connect() 前与断开退避期间为 False。"""
        return self.conn is not None

    async def wait_ready(self, timeout: float | None = None) -> bool:
        """等待连接建立并完成 claim，可用于发送消息。

        Args:
            timeout: 最大等待秒数。None 表示无限等待。

        Returns:
            True 表示已就绪，False 表示超时。
        """
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def send(self, payload: "message.Message | bytes | str"):
        if isinstance(payload, message.Message):
            payload = payload.to_json()
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
        logger.info(i18n.translate("message.connecting"))
        try:
            self.conn = await websockets.connect(
                self.url,
                additional_headers=config.get("headers"),
                max_size=None,
                compression=None,
                ping_interval=None,
                ping_timeout=None,
            )
            if self.reconnect_attempts:
                logger.info(i18n.translate("connector.reconnected", attempt=self.reconnect_attempts))
            self.reconnect_attempts = 0
            self._connected_at = time.monotonic()
            self.session_info = None
            self._bw_controller.reset()
            logger.info(i18n.translate("message.connect_success"))

            self.System = self.SystemCls(self.conn)
            self.Session = self.SessionCls(self.conn)
            self.Error = self.ErrorCls(self.conn)

            # 向服务端声明设备状态，随后立即查询服务端视角的权威 session。
            self._claim_pending = True
            await self.Session.claims(config.get("status"))
            await self.Session.query_status()
            self._ready.set()

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
            logger.info(i18n.translate("message.connect_closed"))
        except OSError as e:
            # 涵盖 ConnectionRefusedError / socket.gaierror / 网络不可达
            logger.warning(i18n.translate("message.connect_server_failed"))
            logger.debug(str(e))
        except Exception as e:
            logger.error(i18n.translate("connector.connect_failed", error=e), exc_info=True)
        finally:
            self._claim_pending = False
            self._ready.clear()
            self.on_close()
            self.conn = None

    def on_close(self):
        pass

    @staticmethod
    def _is_claim_rejection(data) -> bool:
        """判断无 from 的 error 是否为 claim 被拒。

        服务端 claim 被拒只有两种返回(见 claimlogic.go)：
          - "unknown device type: <type>"
          - "device '<deviceName>' is already connected"
        其余(如路由失败的 "session not found")均不属于 claim 被拒。
        """
        text = data if isinstance(data, str) else str(data or "")
        return text.startswith("unknown device type:") or (
            text.startswith("device '") and text.endswith("is already connected")
        )

    @staticmethod
    def _is_already_connected(data) -> bool:
        """claim 被拒是否属于"设备已连接(旧会话未过期)"——这是可自愈的正常状态，
        不应断连重连，而应保持连接并补发 status 查询获取权威 session。"""
        text = data if isinstance(data, str) else str(data or "")
        return text.startswith("device '") and text.endswith("is already connected")

    def _on_session_info_updated(self):
        """服务端权威 session 信息更新后的钩子；子类可覆写以同步本地状态(如设备名)。"""
        pass

    async def loop(self):
        """消息主循环：接收 text/binary 帧，配对后派发到 main()。"""
        pending = None
        while True:
            try:
                raw = await self.conn.recv()

                # 二进制帧：与前置 pending 的 text 帧配对
                if isinstance(raw, bytes):
                    # 非流帧记 DEBUG；流帧高频，降到 TRACE(不刷屏，只进日志流的 TRACE 档)
                    if pending is None or pending.Type != "stream":
                        logger.debug(i18n.translate("connector.binary_received", size=len(raw)))
                    else:
                        logger.trace(i18n.translate("connector.binary_received", size=len(raw)))
                    if pending is not None:
                        pending.BinaryData = raw
                        msg = pending
                        pending = None
                        if msg.Type != "stream":
                            logger.debug(i18n.translate("connector.binary_paired", type=msg.Type, action=msg.Action))
                        else:
                            logger.trace(i18n.translate("connector.binary_paired", type=msg.Type, action=msg.Action))
                    else:
                        logger.warning(i18n.translate("connector.binary_without_text"))
                        continue
                # 文本帧：解析 JSON 并检查是否需要等待后续二进制帧
                else:
                    try:
                        d = json.loads(raw)
                        msg = self.Message.from_json(d)
                        # 流帧与保活 ping/pong 高频，降到 TRACE；其余记 DEBUG
                        if msg.Type == "stream" or (msg.Type == "system" and msg.Action in ("ping", "pong")):
                            logger.trace(i18n.translate("connector.msg_received", msg=msg))
                        else:
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

                    # 需要等待后续二进制帧的消息：
                    # - 服务端使用顶层 "binary": true
                    # - 客户端自发自收使用 Data 内 "__binary__": true
                    needs_binary = d.get("binary") or (
                        isinstance(msg.Data, dict) and msg.Data.pop("__binary__", False)
                    )
                    if needs_binary:
                        pending = msg
                        if msg.Type != "stream":
                            logger.debug(i18n.translate("connector.pending_set"))
                        else:
                            logger.trace(i18n.translate("connector.pending_set"))
                        continue

                # 服务端自身产生的 error 没有 from(claim 被拒 / 未知消息类型 / 路由失败)。
                if msg.Type == "error" and not msg.From:
                    # 仅当错误内容命中 claim 被拒特征时才处理；
                    # 其余无 from 的 error(如路由失败的 "session not found")不属于 claim 被拒，
                    # 应交给 main() 按 rid 精确停对应流，绝不能误判而反复断连重连。
                    if (self._claim_pending and self._is_claim_rejection(msg.Data)
                            and time.monotonic() - self._connected_at <= self.CLAIM_WINDOW):
                        self._claim_pending = False
                        if self._is_already_connected(msg.Data):
                            # 旧 session 未过期属正常状态：保持连接，补发 status 查询获取权威 session，
                            # 而非断连重连(否则会陷入"重连→再次 already connected"的死循环)。
                            logger.warning(i18n.translate("connector.already_connected", reason=msg.Data))
                            await self.Session.query_status()
                            continue
                        # claim 被拒时连接依然"健康"，但本连接没有任何设备身份，
                        # 之后所有消息都会被服务端以 "device not found in connection status" 拒绝。
                        # 必须主动断开触发重连：服务端要等 PongWait(60s) 才回收残留会话，
                        # 退避重连几轮后即可 claim 成功。
                        logger.error(i18n.translate("connector.claim_rejected", reason=msg.Data))
                        await self._drop_connection()
                        break
                    # 其余无 from 的服务端 error 不再 continue：交给 main() 镜像给网页并记录

                # 收到任何带 from 的消息说明服务端已按本设备地址完成路由，claim 必然已生效
                if msg.From:
                    self._claim_pending = False

                await self.main(msg)
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                logger.error(i18n.translate("connector.loop_exception", error=e), exc_info=True)
                break
        logger.debug(i18n.translate("connector.loop_exited"))

    @staticmethod
    def system_dispatch(func):
        """装饰器：系统消息处理。ping/pong 就地回复，不进入业务逻辑。"""
        async def wrapper(self, msg):
            if msg.Type == "system":
                from . import logic as ws_logic
                ws_logic.system(self, msg)
                return
            return await func(self, msg)
        return wrapper

    @staticmethod
    def session_dispatch(func):
        """装饰器：session 消息处理。更新 session_info 并触发钩子，不进入业务逻辑。"""
        async def wrapper(self, msg):
            if msg.Type == "session" and msg.Action == "status" and isinstance(msg.Data, dict):
                self._claim_pending = False
                self.session_info = msg.Data
                logger.debug(i18n.translate("connector.session_updated"))
                self._on_session_info_updated()
                return
            return await func(self, msg)
        return wrapper

    @abstractmethod
    async def main(self, msg: "Message") -> None:
        """子类实现：处理接收到的业务消息。"""
        if msg.Type != "stream":
            logger.debug(i18n.translate("connector.msg_received", msg=msg))
        else:
            logger.trace(i18n.translate("connector.msg_received", msg=msg))


    Message = message.Message


    class SystemCls:
        def __init__(self, conn):
            self.conn = conn

        async def ping(self, Data = None, To: str | None = None, RequestID: str | None = None):
            await self.conn.send(message.system.ping(Data=Data, To=To, RequestID=RequestID))


    class SessionCls:
        def __init__(self, conn):
            self.conn = conn

        async def claims(self, status: dict):
            await self.conn.send(message.session.claim(status))

        async def query_status(self):
            """查询服务端视角的本机权威 session 信息。"""
            await self.conn.send(message.session.status())


    class ErrorCls:
        def __init__(self, conn):
            self.conn = conn

        async def error(self, data, To: str | None = None, RequestID: str | None = None):
            if not To:
                return
            await self.conn.send(message.error.error(data, To=To, RequestID=RequestID))
