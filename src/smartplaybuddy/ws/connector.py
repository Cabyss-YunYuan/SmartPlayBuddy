import asyncio
import websockets
import json
from abc import ABC, abstractmethod
from ..i18n import *
from .. import log
from . import logic
from . import message

logger = log.logger.getChild("Connector")

class Connector(ABC):
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
            logger.error(translate("error.task_create_failed", error=e))

    async def connect(self, config):
        logger.debug(translate("message.connecting"))
        try:
            self.conn = await websockets.connect(
                self.url,
                additional_headers=config.get("headers"),
                max_size=None,
                compression=None,
            )
            logger.debug(translate("message.connect_success"))

            self.System = self.SystemCls(self.conn)
            self.Session = self.SessionCls(self.conn)
            self.Error = self.ErrorCls(self.conn)

            await self.Session.claims(config.get("status"))

            await self.loop()
        except TimeoutError:
            logger.error(translate("message.connect_timeout"))
        except ConnectionRefusedError:
            logger.error(translate("message.connect_server_failed"))
        except websockets.exceptions.ConnectionClosed as e:
            if e.rcvd is not None and e.rcvd.code != 1000:
                logger.error(translate("error.connect_closed", code=e.rcvd.code, reason=e.rcvd.reason))
            logger.info(translate("message.connect_closed"))
        finally:
            self.on_close()

    def on_close(self):
        pass

    async def loop(self):
        pending = None
        while True:
            try:
                raw = await self.conn.recv()

                if isinstance(raw, bytes):
                    logger.debug(translate("connector.binary_received", size=len(raw)))
                    if pending is not None:
                        pending.BinaryData = raw
                        msg = pending
                        pending = None
                        logger.debug(translate("connector.binary_paired", type=msg.Type, action=msg.Action))
                    else:
                        logger.error(translate("error.binary_without_text"))
                        continue
                else:
                    try:
                        d = json.loads(raw)
                        msg = message.Message.from_json(d)
                        logger.debug(translate("connector.msg_received", msg=msg))
                    except json.decoder.JSONDecodeError:
                        logger.error(translate("error.msg_parse_failed", msg=raw))
                        continue
                    except KeyError as e:
                        logger.error(translate("error.msg_field_missing", field=e.args[0], msg=raw))
                        await self.Error.error(d, To=d.get("from"), RequestID=d.get("requestId"))
                        continue

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

                    if isinstance(msg.Data, dict) and msg.Data.pop("__binary__", False):
                        pending = msg
                        logger.debug(translate("connector.pending_set"))
                        continue

                if msg.Type == "system":
                    logic.system(self, msg)
                await self.main(msg)
            except websockets.exceptions.ConnectionClosed:
                logger.info(translate("message.connect_closed"))
                break
            except Exception as e:
                logger.error(translate("error.loop_exception", error=e), exc_info=True)
                break
        logger.debug(translate("connector.loop_exited"))

    @abstractmethod
    async def main(self, msg: message.Message) -> None:
        logger.info(msg)


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
