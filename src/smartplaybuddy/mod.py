from . import ws
from .i18n import *
from . import log
from .config import WS_URL

import asyncio
import time
import json
import base64


logger = log.logger.getChild("Mod")

class Mod(ws.Connector):
    def __init__(self, **config):
        super().__init__(**config)

    async def main(self, msg: dict) -> None:


        pass


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
                    "type": "mod",
                }
            }
        }
        client = Mod(**config)

        while True:
            await asyncio.sleep(1)

    try:
        asyncio.run(start())
    except:
        logger.info(translate("system.close"))
