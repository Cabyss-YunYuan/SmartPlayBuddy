from . import ws
from .i18n import *
from . import log
from .config import WS_URL
from .driver import drivers

import asyncio

logger = log.logger.getChild("Client")

class Client(ws.Connector):
    def __init__(self, **config):
        super().__init__(**config)

    # 接收并处理消息
    async def main(self, msg) -> None:
        try:
            if msg.Type == "command":
                if type(msg.Data) is dict:
                    drivers[msg.Action](msg.Data)
                elif type(msg.Data) is list:
                    for operator in msg.Data:
                        drivers[msg.Action](operator)
                else:
                    await self.Error.error("Invalid data type", To=msg.From, RequestID=msg.RequestID)
            else:
                await self.Error.error("Invalid message type", To=msg.From, RequestID=msg.RequestID)
        except KeyError as e:
            logger.error(f"KeyError: {e}")
            await self.Error.error(f"KeyError: {e}", To=msg.From, RequestID=msg.RequestID)


def main():
    async def start():
        from . import user

        tokens = user.refresh_login() or user.login()
        user.save_tokens(tokens)

        config = {
            "url": WS_URL,
            "headers": {
                "Authorization": f"Bearer {tokens.access_token}",
            },
            "status": {
                "device": {
                    "type": "client",
                }
            }
        }
        client = Client(**config)

        while True:
            await asyncio.sleep(1)
            # await client.System.ping()

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        logger.info(translate("system.close"))
