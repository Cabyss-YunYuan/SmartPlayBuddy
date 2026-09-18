"""
Mod 开发入口模块。
提供 Mod 基类供第三方开发者继承，实现自定义消息处理逻辑。
"""
from . import ws
from . import i18n
from . import logger
from .config import Config
from .ws import message
import asyncio


logger = logger.logger.getChild("Mod")


class _PermitEvent:
    """授权事件：封装 request → event lock → 操作 → release 的完整流程。"""

    def __init__(self, mod: "Mod", target: str, description: str):
        self._mod = mod
        self._target = target
        self._description = description
        self._rid: str | None = None

    async def __aenter__(self) -> str:
        loop = asyncio.get_event_loop()
        self._mod._permit_future = loop.create_future()

        msg = message.Message(
            Type="request",
            Action="permit",
            To=self._target,
            Data={
                "operate": "request_permit",
                "description": self._description,
            },
        )
        await self._mod.send(msg)
        self._rid = msg.RequestID

        try:
            status = await asyncio.wait_for(self._mod._permit_future, timeout=120)
        except asyncio.TimeoutError:
            self._mod._permit_future = None
            raise TimeoutError("授权请求超时")

        self._mod._permit_future = None

        if status != "approved":
            raise PermissionError(f"授权被拒绝: {status}")

        await self._mod.send(message.Message(
            Type="event",
            Action="permit",
            To=self._target,
            RequestID=self._rid,
            Data={"operate": "activate"},
        ))

        return self._rid

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._rid:
            await self._mod.send(message.Message(
                Type="event",
                Action="permit",
                To=self._target,
                RequestID=self._rid,
                Data={"operate": "release"},
            ))
        return False


class Mod(ws.Connector):
    """Mod 基类，开发者继承并实现 main() 方法处理消息。"""

    auto_auth = True

    def __init__(self, **config):
        super().__init__(**config)
        self._permit_future: asyncio.Future | None = None

    @staticmethod
    def permit_dispatch(func):
        """装饰器：permit response 拦截。resolve _permit_future，不进入业务逻辑。"""
        async def wrapper(self, msg):
            if (msg.Type == "response" and msg.Action == "permit"
                    and self._permit_future
                    and not self._permit_future.done()):
                status = (msg.Data or {}).get("status", "rejected") if isinstance(msg.Data, dict) else "rejected"
                self._permit_future.set_result(status)
                return
            return await func(self, msg)
        return wrapper

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "main" in cls.__dict__:
            cls.main = cls.permit_dispatch(cls.system_dispatch(cls.session_dispatch(cls.__dict__["main"])))

    async def main(self, msg) -> None:
        print(msg)

    def permit(self, target: str, description: str = "") -> "_PermitEvent":
        """授权上下文管理器。用法：

            async with self.permit(target, "屏幕捕获") as rid:
                await self.send(self.Message(
                    Type="command", Action="screen", To=target, RequestID=rid,
                    Data={"operate": "capture", "format": "jpeg"},
                ))
        """
        return _PermitEvent(self, target, description)

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
