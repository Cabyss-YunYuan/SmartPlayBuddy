from . import wsconnector
from .driver import xbox
from . import log

import asyncio
import websockets
import json

logger = log.logger.logger.getChild("Client")

# 转译Xbox操作
class XboxGamepad(xbox.Gamepad):
    def operator(self, operators):
        for operator in operators:
            if operator["type"] == "button":
                if operator["operator"] == "press":
                    eval("self." + operator["button"] + "()")
                elif operator["operator"] == "release":
                    eval("self." + operator["button"] + "_()")
            elif operator["type"] == "joystick":
                if operator["axis"] == "left":
                    self.LEFT_JOYSTICK(operator["x"], operator["y"])
                elif operator["axis"] == "right":
                    self.RIGHT_JOYSTICK(operator["x"], operator["y"])
            elif operator["type"] == "reset":
                self.RESET()

class Client(wsconnector.Connector):
    def __init__(self, **config):
        super().__init__(**config)

    # 接收并处理消息
    # async def main(self):
    #     try:
    #         while True:
    #             # 从服务器接收信息
    #             response = await self.websocket.recv()
    #             # 解码
    #             if type(response) is bytes:
    #                 common = json.loads(response)
    #                 commander = common["commander"]
    #                 del common["commander"]
    #                 # 尝试执行命令
    #                 try:
    #                     # 添加设备
    #                     if common["type"] == "AddDevice":
    #                         if common["device"] == "Xbox":
    #                             self.devices[common["name"]] = XboxGamepad()
    #                     # 操作设备
    #                     elif common["type"] == "OperateDevice":
    #                         if self.devices[common["name"]].__class__.__name__ == "XboxGamepad":
    #                             self.devices[common["name"]].operator(common["operators"])
    #                     # 移除设备
    #                     elif common["type"] == "RemoveDevice":
    #                         del self.devices[common["name"]]
    #                     else:
    #                         await self.websocket.send(
    #                             json.dumps({"type": "Send", "device": commander, "error": common}))
    #                         logger.info(f"\033[4;33m{response}\033[0m")
    #                 except:
    #                     await self.websocket.send(
    #                         json.dumps({"type": "Send", "device": commander, "error": common}))
    #                     logger.warn(f"\033[4;33m{response}\033[0m")
    #             elif type(response) is str:
    #                 logger.info(response)
    #     except ConnectionRefusedError:
    #         logger.error("无法连接到服务器，请确保服务器已运行")
    #     except websockets.exceptions.ConnectionClosed as e:
    #         if e.code != 1000:
    #             logger.error(f"连接关闭：代码=\033[4;33m{e.code}\033[0m，原因=\033[4;33m{e.reason}\033[0m")
    #     finally:
    #         logger.error("与服务器的连接已关闭")


def main():
    async def start():
        auth = {
            # "url": "ws://smtplay.cabyss.cn:2508/server/ws",
            "user": {
                "username": "testuser",
                "password": "Test123456",
                "type": "desktop",
                "device": "test_device"
            }
        }
        client = Client(**auth)
        try:
            while True:
                await asyncio.sleep(1)
        except:
            logger.info("已关闭")
    asyncio.run(start())
