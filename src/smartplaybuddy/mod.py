"""
Mod 开发入口模块。
提供 Mod 基类供第三方开发者继承，实现自定义消息处理逻辑。
"""
from . import ws
from . import i18n
from . import log
from .config import WS_URL

import asyncio

logger = log.logger.getChild("Mod")

class Mod(ws.Connector):
    """Mod 基类，开发者继承并实现 main() 方法处理消息。"""

    def __init__(self, **config):
        super().__init__(**config)

    async def main(self, msg) -> None:
        print(msg)


def main(mod: type[Mod] = Mod):
    """Mod 启动入口。"""
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
        logger.info(i18n.translate("system.close"))
