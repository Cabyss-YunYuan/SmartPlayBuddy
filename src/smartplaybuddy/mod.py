"""
Mod 开发入口模块。
提供 Mod 基类供第三方开发者继承，实现自定义消息处理逻辑。
"""
from . import ws
from .utils import i18n
from .utils import logger
from .config import Config
from .ws import message
import asyncio


logger = logger.getChild("Mod")


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
        self._mod._revoke_future = None
        self._mod._body_completed = False
        self._mod._body_task = asyncio.current_task()

        msg = message.Message(
            Type="request",
            Action="permit",
            To=self._target,
            Data={
                "operate": "request-permit",
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

        if status == "revoked":
            self._mod._revoke_future = None
            self._mod._body_completed = True
            raise PermissionError("授权已被用户收回")

        if status != "approved":
            raise PermissionError(f"授权被拒绝: {status}")

        await self._mod.send(message.Message(
            Type="event",
            Action="permit",
            To=self._target,
            RequestID=self._rid,
            Data={"operate": "activate"},
        ))

        self._mod._revoke_future = loop.create_future()

        return self._rid

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._mod._body_completed = True
        self._mod._body_task = None

        if self._rid:
            await self._mod.send(message.Message(
                Type="event",
                Action="permit",
                To=self._target,
                RequestID=self._rid,
                Data={"operate": "release"},
            ))

        revoke_future = self._mod._revoke_future
        self._mod._revoke_future = None

        if revoke_future is not None and revoke_future.done() and not revoke_future.cancelled():
            raise PermissionError(i18n.translate("permit.revoked_by_user"))

        return False


class Mod(ws.Connector):
    """Mod 基类，开发者继承并实现 main() 方法处理消息。"""

    auto_auth = True

    def __init__(self, **config):
        super().__init__(**config)
        self._permit_future: asyncio.Future | None = None
        self._revoke_future: asyncio.Future | None = None
        self._body_completed: bool = False
        self._body_task: asyncio.Task | None = None

    @staticmethod
    def permit_dispatch(func):
        """装饰器：permit response / revoke error 拦截。"""
        async def wrapper(self, msg):
            if msg.Type == "response" and msg.Action == "permit":
                if (self._permit_future
                        and not self._permit_future.done()):
                    status = (msg.Data or {}).get("status", "rejected") if isinstance(msg.Data, dict) else "rejected"
                    self._permit_future.set_result(status)
                    return
            elif (msg.Type == "error" and msg.Action == "permit"
                  and isinstance(msg.Data, dict)
                  and msg.Data.get("operate") == "revoked"):
                logger.info(i18n.translate("permit.revoke_received", request_id=msg.RequestID))
                if self._revoke_future and not self._revoke_future.done():
                    self._revoke_future.set_result("revoked")
                if self._body_task and not self._body_completed:
                    self._body_task.cancel()
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

        await user.ensure_tokens()

        client = mod(**Config.build_connect_config("mod"))

        try:
            await client.connection
        finally:
            await client.close()

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        logger.debug(i18n.translate("system.close"))
