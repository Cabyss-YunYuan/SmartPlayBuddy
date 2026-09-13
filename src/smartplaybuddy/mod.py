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

    auto_auth = True

    def __init__(self, **config):
        super().__init__(**config)

    async def main(self, msg) -> None:
        print(msg)


def main(mod: type[Mod] = Mod):
    """Mod 启动入口。"""
    async def start():
        from . import config as app_config
        from . import user

        import platform

        await asyncio.to_thread(user.ensure_tokens)

        mod_config = {
            "url": WS_URL,
            "status": {
                "device": {
                    "type": "mod",
                    "deviceName": app_config.DEVICE_NAME,
                    "deviceInfo": "",
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                    "appVersion": app_config.VERSION,
                }
            }
        }
        client = mod(**mod_config)

        try:
            await client.connection
        finally:
            await client.close()

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        logger.info(i18n.translate("system.close"))
