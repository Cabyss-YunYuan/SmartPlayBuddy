"""
Mod 开发入口模块。
提供 Mod 基类供第三方开发者继承，实现自定义消息处理逻辑。
"""
from . import ws
from . import i18n
from . import log
from .config import WS_URL
from .ws import route

import asyncio
import time
from typing import Dict, Any

logger = log.logger.getChild("Mod")

class Mod(ws.Connector):
    """Mod 基类，开发者继承并实现 main() 方法处理消息。"""

    def __init__(self, **config):
        super().__init__(**config)
        # 待确认的控制请求: {requestId: 目标设备}
        self._pending_control: Dict[str, str] = {}
        # 已授权设备: {目标设备: 匹配通过的 requestId}
        self._authorized: Dict[str, str] = {}

    async def main(self, msg) -> None:
        print(msg)

    async def preprocess(self, msg) -> bool:
        """拦截控制授权响应：只有 requestId 匹配且同账号的响应才生效。"""
        if msg.Type == "response" and msg.Action == "request_control":
            to = self._pending_control.pop(msg.RequestID, None)
            if to is None:
                logger.warning(i18n.translate("mod.rid_mismatch", rid=msg.RequestID))
                return False
            self_to = self.resolve_to(msg.To)
            if not msg.From or not self_to or not route.same_account(msg.From, self_to):
                logger.warning(i18n.translate("mod.account_mismatch", sender=msg.From, to=self_to))
                return False
            data = msg.Data if isinstance(msg.Data, dict) else {}
            if data.get("accepted") and data.get("requestId") == msg.RequestID:
                self._authorized[to] = msg.RequestID
                logger.info(i18n.translate("mod.control_authorized", to=to, rid=msg.RequestID))
            else:
                logger.info(i18n.translate("mod.control_denied", to=to, rid=msg.RequestID))
            return False
        return True

    def is_authorized(self, to: str) -> bool:
        """目标设备是否已同意接受控制。"""
        return to in self._authorized

    async def request_control(self, to: str) -> str:
        """向目标设备发送控制请求事件，返回本次请求的 RequestID。"""
        msg = self.Message(
            Type="event",
            Action="request_control",
            To=to,
            Data={"request": "control"},
        )
        payload = msg.to_json()
        self._pending_control[msg.RequestID] = to
        await self.conn.send(payload)
        logger.info(i18n.translate("mod.control_request_sent", to=to, rid=msg.RequestID))
        return msg.RequestID

    async def wait_for_authorization(self, to: str, timeout: float = 30.0) -> bool:
        """等待目标设备同意控制授权。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if to in self._authorized:
                return True
            await asyncio.sleep(0.1)
        return False

    async def send_command(self, to: str, action: str, data: Any) -> bool:
        """发送控制指令，必须附带目标设备批准过的 requestID，否则客户端拒绝执行。"""
        rid = self._authorized.get(to)
        if rid is None:
            logger.warning(i18n.translate("mod.not_authorized", to=to))
            return False
        await self.conn.send(self.Message(
            Type="command",
            Action=action,
            To=to,
            RequestID=rid,
            Data=data,
        ).to_json())
        return True


def main(mod: type[Mod] = Mod):
    """Mod 启动入口。"""
    async def start():
        from . import config
        from . import user

        import platform


        tokens = user.refresh_login() or user.login()
        user.save_tokens(tokens)

        cfg = {
            "url": WS_URL,
            "headers": {
                "Authorization": f"Bearer {tokens.access_token}",
            },
            "userId": user.user_id(tokens),
            "status": {
                "device": {
                    "type": "mod",
                    "deviceName": "test-mod",   #test
                    "deviceInfo": "",
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                    "appVersion": config.VERSION,
                }
            }
        }
        client = mod(**cfg)

        while True:
            await asyncio.sleep(1)

    try:
        asyncio.run(start())
    except:
        logger.info(i18n.translate("system.close"))
