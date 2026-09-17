import asyncio
import time
from ... import i18n
from ... import logger
from .. import message

logger = logger.logger.getChild("System")

def system(self, msg):
    if msg.Action == "pong":
        if msg.Data is None:
            return
        latency = int(time.time() * 1000) - (msg.Data['time'])
        # 保活每秒每流回一条 pong，延迟统计降到 TRACE 免刷屏(需要时订阅 TRACE 档可见)
        logger.trace(i18n.translate("system.ping", device=msg.From if msg.From else "server", latency=f"{latency}ms"))
    elif msg.Action == "ping":
        # 收到对端(经服务端路由来的远端网页)的 ping：原样回一条 pong，形态与服务端自答一致。
        # To=msg.From 让服务端把 pong 路由回发起者；发起者已离线时服务端回带 rid 的 "session not found"。
        # 必须走 self.send(持 _send_lock)，否则会与屏幕流的 text+binary 成对帧交错、破坏配对。
        asyncio.create_task(
            self.send(message.system.pong(To=msg.From, RequestID=msg.RequestID, Data=msg.Data))
        )
