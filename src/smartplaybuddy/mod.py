from . import wsconnector
from . import log

import asyncio
import websockets
import json

logger = log.logger.logger.getChild("Mod")

class Mod(wsconnector.Connector):
    def __init__(self, **config):
        super().__init__(**config)

    async def main(self):
        try:
            while True:
                # 从服务器接收信息
                response = await self.websocket.recv()
                # 解码
                if type(response) is bytes:
                    task = json.loads(response)
                    logger.info(f'\033[1;32m{task["operator"]["username"]}: {task}\033[0m')
                    # 尝试执行任务
                    try:
                        asyncio.create_task(self._task(self.websocket, task))
                    except:
                        await self.websocket.send(json.dumps({"type": "Send", "user": task["operator"]["username"], "device": task["operator"]["devicename"], "error": task}))
                        logger.error(f"\033[4;33m{response}\033[0m")
                elif type(response) is str:
                    logger.info(f'\033[1;34m{response}\033[0m')
        except ConnectionRefusedError:
            logger.error("无法连接到服务器，请确保服务器已运行")
        except websockets.exceptions.ConnectionClosed as e:
            if e.code != 1000:
                logger.error(f"连接关闭：代码=\033[4;33m{e.code}\033[0m，原因=\033[4;33m{e.reason}\033[0m")
        except websockets.exceptions.ConnectionClosedError:
            logger.error("与服务器的连接已关闭")

    @staticmethod
    async def _task(websocket, task):
        operate = {"type": "Send", "user": task["operator"]["username"], "commander": task["operator"]["devicename"],"device": task["device"], "command": task["task"]}
        await websocket.send(json.dumps(operate))


def main():
    async def start():
        logger.info("开始运行...")
        config = {
            "url": "ws://smtplay.cabyss.cn:2508/server/ws",
            "user": {
                "username": "tests",
                "password": "123456",
                "type": "mod",
                "device": "test_mod"
            }
        }
        mod = Mod(**config)
        try:
            while True:
                await asyncio.sleep(1)
        except:
            logger.info("已关闭")
    asyncio.run(start())