import asyncio
import websockets
import json

from .. import log

logger = log.logger.logger.getChild("Connector")

class Connector:
    websocket = None

    def __init__(self, **config):
        self.uri = config.get("uri")
        self.user = config.get("user")
        self.devices = {}
        try:
            self.connection = asyncio.create_task(self.connect())
        except Exception as e:
            logger.error(f"创建连接任务失败: {e}")

    async def connect(self):
        logger.info("开始连接...")
        try:
            self.websocket = await websockets.connect(self.uri)
            logger.info("✅ 已建立WebSocket连接")
            # 发送验证信息
            await self.websocket.send(json.dumps(self.user))
            # 开始接收消息循环
            await self.loop()
        except TimeoutError:
            logger.error("连接超时")
        except ConnectionRefusedError:
            logger.error("无法连接到服务器，请确保服务器已运行")
        except websockets.exceptions.ConnectionClosed as e:
            if e.code != 1000:
                logger.error(f"连接关闭：代码=\033[4;33m{e.code}\033[0m，原因=\033[4;33m{e.reason}\033[0m")
            logger.info("与服务器的连接已关闭")

    async def loop(self):
        while True:
            logger.info("开始接收消息...")
            # 从服务器接收信息
            response = await self.websocket.recv()
            # 处理接收到的消息
            logger.info(f"收到消息: {response}")
