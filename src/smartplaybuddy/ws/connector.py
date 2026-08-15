"""
WebSocket 连接器基类。
处理 text + binary 双帧协议：当 text 帧标记 __binary__=true 时，
等待紧随其后的 binary 帧完成配对，再派发到 main()。
"""
import asyncio
import websockets
import json
from abc import ABC, abstractmethod
from .. import i18n
from .. import log
from . import logic
from . import message

logger = log.logger.getChild("Connector")

class Connector(ABC):
    """WebSocket 连接器抽象基类，子类需实现 main() 处理业务消息。"""
    conn: websockets.ClientConnection

    System: "SystemCls"
    Session: "SessionCls"
    Error: "ErrorCls"

    def __init__(self, **config):
        self.url = config.get("url", "ws://smtplay.cabyss.cn:2508/ws")
        # self.user = config["user"]
        try:
            self.connection = asyncio.create_task(self.connect(config))
        except Exception as e:
            logger.error(i18n.translate("connector.task_create_failed", error=e))

    async def connect(self, config):
        logger.debug(i18n.translate("message.connecting"))
        try:
            self.conn = await websockets.connect(
                self.url,
                additional_headers=config.get("headers"),
                max_size=None,
                compression=None,
            )
            logger.debug(i18n.translate("message.connect_success"))

            self.System = self.SystemCls(self.conn)
            self.Session = self.SessionCls(self.conn)
            self.Error = self.ErrorCls(self.conn)

            # 向服务端声明设备状态
            await self.Session.claims(config.get("status"))

            await self.loop()
        except TimeoutError:
            logger.error(i18n.translate("message.connect_timeout"))
        except ConnectionRefusedError:
            logger.error(i18n.translate("message.connect_server_failed"))
        except websockets.exceptions.ConnectionClosed as e:
            if e.rcvd is not None and e.rcvd.code != 1000:
                logger.error(i18n.translate("connector.connect_closed_error", code=e.rcvd.code, reason=e.rcvd.reason))
            logger.debug(i18n.translate("message.connect_closed"))
        finally:
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
                        logger.error(i18n.translate("connector.binary_without_text"))
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

                # 系统消息走内部逻辑，其余派发到子类
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
            await self.conn.send(message.error.error(data, To=To, RequestID=RequestID))
