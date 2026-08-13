from . import ws
from .i18n import *
from . import log
from .config import WS_URL
from .drivers import drivers
from .ws import message

import asyncio
import json
import struct
from . import config

logger = log.logger.getChild("Client")

class Client(ws.Connector):
    def __init__(self, **config):
        super().__init__(**config)
        self._stream_handler = None
    
    async def main(self, msg) -> None:
        try:
            if msg.Type == "command":
                if msg.Action not in drivers:
                    resp = message.Message(Type="error", Action=msg.Action, To=msg.From, RequestID=msg.RequestID,
                                           Data=translate("driver.not_found", driver=msg.Action), )
                    await self.conn.send(resp.to_json())
                    logger.warning(translate("client.key_error", error=resp))
                    return
                
                # 处理 stop_stream：不等待响应，直接发送
                if msg.Data.get("operate") == "stop_stream":
                    from .drivers import registry as drv_registry
                    info = drv_registry._info.get(msg.Action)
                    if info:
                        driver_name = info["manifest"]["name"]
                        proc = drv_registry._procs.get(driver_name)
                        if proc:
                            # 直接发送命令，不等待响应
                            payload = json.dumps({"command": "operate", "cmd": "stop_stream", "params": msg.Data}).encode("utf-8")
                            header = struct.pack("<BII", 0, len(payload), 0)
                            proc._process.stdin.write(header + payload)
                            proc._process.stdin.flush()
                    logger.info(translate("client.stream_stopped", action=msg.Action))
                    return
                
                # 处理 start_stream：发送响应后启动后台读取
                if msg.Data.get("operate") == "start_stream":
                    resp = drivers[msg.Action](msg.Data)
                    if isinstance(resp, dict) and resp.get("status") == "ok":
                        meta = message.Message(
                            Type="response",
                            Action=msg.Action,
                            To=msg.From,
                            RequestID=msg.RequestID,
                            Data=resp.get("result"),
                        )
                        await self.conn.send(meta.to_json())

                        from .drivers import registry as drv_registry
                        original_msg = msg
                        def on_frame(frame_msg):
                            asyncio.run_coroutine_threadsafe(
                                self._forward_stream(frame_msg, original_msg),
                                self._loop
                            )
                        self._loop = asyncio.get_event_loop()
                        drv_registry.start_stream(msg.Action, on_frame)
                    else:
                        logger.error(translate("client.stream_start_failed", action=msg.Action, resp=resp))
                    return
                
                if type(msg.Data) is dict:
                    resp = drivers[msg.Action](msg.Data)
                elif type(msg.Data) is list:
                    resp = None
                    for operator in msg.Data:
                        resp = drivers[msg.Action](operator)
                else:
                    await self.Error.error(translate("client.invalid_data_type"), To=msg.From, RequestID=msg.RequestID)
                    return

                if isinstance(resp, dict) and resp.get("status") == "ok":
                    if "__data__" in resp:
                        data = resp.pop("__data__")
                        result = resp.get("result")
                        if isinstance(result, dict):
                            result["__binary__"] = True
                        meta = message.Message(
                            Type="response",
                            Action=msg.Action,
                            To=msg.From,
                            RequestID=msg.RequestID,
                            Data=result,
                        )
                        await self.conn.send(meta.to_json())
                        await self.conn.send(data)
                        logger.debug(translate("client.response_sent", size=len(data), to=msg.From))
                        return
                    else:
                        logger.error(translate("driver.no_data", result=resp.get("result")))

                elif isinstance(resp, dict) and resp.get("status") == "error":
                    logger.error(translate("driver.error", message=resp.get("message")))
                    await self.Error.error(resp.get("message", "Driver error"), To=msg.From, RequestID=msg.RequestID)
                else:
                    logger.error(translate("driver.unexpected_resp", resp=resp))

            elif msg.Type == "error":
                logger.error(msg.Data)
            else:
                await self.Error.error(translate("client.invalid_message_type"), To=msg.From, RequestID=msg.RequestID)
        except KeyError as e:
            logger.error(translate("client.key_error", error=e))
            await self.Error.error(translate("client.key_error", error=e), To=msg.From, RequestID=msg.RequestID)
        except Exception as e:
            logger.error(translate("client.main_exception", error=e), exc_info=True)
            await self.Error.error(str(e), To=msg.From, RequestID=msg.RequestID)

    async def _forward_stream(self, frame_msg: dict, original_msg):
        try:
            if isinstance(frame_msg, dict) and frame_msg.get("BinaryData"):
                data = frame_msg["BinaryData"]
                result = frame_msg.get("Data", {})
                if isinstance(result, dict):
                    result["__binary__"] = True
                meta = message.Message(
                    Type="stream",
                    Action=original_msg.Action,
                    To=original_msg.From,
                    Data=result,
                )
                await self.conn.send(meta.to_json())
                await self.conn.send(data)
        except Exception as e:
            logger.error(translate("client.stream_forward_error", error=e), exc_info=True)

    async def start_stream(self, to: str, params: dict):
        await self.conn.send(message.Message(
            Type="command",
            Action="screen",
            To=to,
            Data={**params, "operate": "start_stream"},
        ).to_json())


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--driver-host":
        from .drivers.host import run_driver
        driver_file = sys.argv[2]
        packages_dir = sys.argv[3] if len(sys.argv) > 3 else None
        run_driver(driver_file, packages_dir)
        return

    async def start():
        from . import config
        from . import user

        import platform
        import pyautogui


        tokens = user.refresh_login() or user.login()
        user.save_tokens(tokens)

        config = {
            "url": WS_URL,
            "headers": {
                "Authorization": f"Bearer {tokens.access_token}",
            },
            "status": {
                "device": {
                    "type": "client",
                    "deviceName": "a",
                    "deviceInfo": "",
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                    "appVersion": config.VERSION,
                    "screenResolution": f"{pyautogui.size().width}x{pyautogui.size().height}",
                }
            }
        }
        client = Client(**config)

        while True:
            await asyncio.sleep(1)
            # await client.System.ping()

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        logger.info(translate("system.close"))
    finally:
        from .drivers import registry as drv_registry
        drv_registry.shutdown()

if __name__ == "__main__":
    main()
