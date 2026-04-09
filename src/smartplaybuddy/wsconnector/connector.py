import asyncio
import time
import websockets
import json
from .. import i18n
from .. import log

logger = log.logger.logger.getChild("Connector")

class Connector:
    websocket: websockets.ClientConnection

    def __init__(self, **config):
        self.url = config.get("url", "ws://smtplay.cabyss.cn:2508/server/ws")
        self.user = config["user"]
        self.translator = i18n.translator.Translator()
        try:
            self.connection = asyncio.create_task(self.connect())
        except Exception as e:
            logger.error(self.translator.translate("error.task_create_failed", error=e))

    async def connect(self):
        logger.debug(self.translator.translate("message.connecting"))
        try:
            self.websocket = await websockets.connect(self.url)
            logger.debug(self.translator.translate("message.connect_success"))
            # 发送验证信息
            await self.websocket.send(json.dumps(self.user))
            res = json.loads(await self.websocket.recv())
            if res.get("code") != 2001:
                logger.error(res.get("message"))
                return
            logger.info(res.get("message"))
            # 开始接收消息循环
            await self.loop()
        except TimeoutError:
            logger.error(self.translator.translate("message.connect_timeout"))
        except ConnectionRefusedError:
            logger.error(self.translator.translate("message.connect_server_failed"))
        except websockets.exceptions.ConnectionClosed as e:
            if e.rcvd.code != 1000:
                logger.error(self.translator.translate("error.connect_closed", code=e.rcvd.code, reason=e.rcvd.reason))
            logger.debug(self.translator.translate("message.connect_closed"))

    async def loop(self):
        while True:
            logger.debug(self.translator.translate("message.msg_receive_start"))
            # 从服务器接收信息
            response = await self.websocket.recv()
            # 处理接收到的消息
            logger.debug(f"{response}")
            try:
                command = json.loads(response)
            except json.decoder.JSONDecodeError:
                logger.error(self.translator.translate("error.msg_parse_failed", msg=response))
                continue
            if command["type"] == "system":
                if command["action"] == "pong":
                    latency = int(time.time() * 1000) - command['data']['time']
                    logger.info(self.translator.translate("command.ping", device=command["from"], latency=f"{latency}ms"))
            await self.main(command)

    async def main(self, response: dict) -> None:
        logger.info(f"收到消息: {response}")
