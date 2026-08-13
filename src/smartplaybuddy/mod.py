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

    async def main(self, msg) -> None:
        print(msg)


def main(mod:type[Mod] = Mod):
    async def start():
        from . import config
        from . import user

        import platform


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
                    "deviceName": "",
                    "deviceInfo": "",
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                    "appVersion": config.VERSION,
                }
            }
        }
        client = mod(**config)

        while True:
            await asyncio.sleep(1)

    try:
        asyncio.run(start())
    except:
        logger.info(translate("system.close"))
