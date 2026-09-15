"""
Mod 开发入口模块。
提供 Mod 基类供第三方开发者继承，实现自定义消息处理逻辑。
"""
from . import ws
from . import i18n
from . import logger
from .config import Config
import asyncio


logger = logger.logger.getChild("Mod")

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
        from . import user

        import platform

        await user.ensure_tokens()

        mod_config = {
            "url": Config.ws_url,
            "status": {
                "device": {
                    "type": "mod",
                    "deviceName": Config.device_name,
                    "deviceInfo": "",
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                    "appVersion": Config.version,
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
        logger.debug(i18n.translate("system.close"))
