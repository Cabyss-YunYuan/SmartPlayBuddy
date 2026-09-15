"""
客户端主模块。
继承 Connector，处理服务端下发的 command/error/stream 消息，
调度本地驱动执行并将结果回传。支持流式帧转发。
"""
from . import ws
from . import i18n
from . import logger
from .config import Config
from .drivers import drivers
import asyncio
from typing import Dict


logger = logger.logger.getChild("Client")

class Client(ws.Connector):
    """业务客户端：接收服务端指令 → 调用本地驱动 → 回传结果。"""

    auto_auth = True

    def __init__(self, **config):
        super().__init__(**config)
        self._stream_handler = None
        self._loop = asyncio.get_event_loop()
        # 活跃流记录: {action: {from: [stream_id, ...]}}
        self._active_streams: Dict[str, Dict[str, list[str]]] = {}

    async def main(self, msg) -> None:
        try:
            if msg.Type == "command":
                data = msg.Data if isinstance(msg.Data, dict) else {}
                operate = data.get("operate")

                if msg.Action == "*" and operate == "stop_stream":
                    from .drivers import registry as drv_registry
                    drv_registry.stop_all_streams()
                    self._active_streams.clear()
                    logger.info(i18n.translate("client.stream_stopped", action="all", stream_id="all", sender=msg.From or "server"))
                    return

                if msg.Action not in drivers:
                    resp = self.Message(Type="error", Action=msg.Action, To=msg.From, RequestID=msg.RequestID,
                                           Data=i18n.translate("driver.not_found", driver=msg.Action), )
                    await self.send(resp.to_json())
                    logger.warning(i18n.translate("client.key_error", error=resp))
                    return
                
                # 处理 stop_stream：移除回调并通知驱动
                if operate == "stop_stream":
                    from .drivers import registry as drv_registry
                    frm = msg.From
                    stream_id = data.get("stream_id")
                    if frm:
                        streams = self._active_streams.get(msg.Action, {})
                        if stream_id:
                            for f, sids in list(streams.items()):
                                if stream_id in sids:
                                    sids.remove(stream_id)
                                    if not sids:
                                        streams.pop(f, None)
                                    break
                            drv_registry.stop_stream(msg.Action, stream_id)
                        else:
                            sids = streams.pop(frm, [])
                            for sid in sids:
                                drv_registry.stop_stream(msg.Action, sid)
                        logger.info(i18n.translate("client.stream_stopped", action=msg.Action, stream_id=stream_id or "all", sender=frm))
                    else:
                        drv_registry.stop_all_streams(msg.Action)
                        self._active_streams.pop(msg.Action, None)
                        logger.info(i18n.translate("client.stream_stopped", action=msg.Action, stream_id="all", sender="server"))
                    return

                # 处理 start_stream：发送响应后注册流回调
                if operate == "start_stream":
                    stream_id = msg.RequestID
                    msg.Data["stream_id"] = stream_id
                    resp = drivers[msg.Action](msg.Data)
                    if isinstance(resp, dict) and resp.get("status") == "ok":
                        meta = self.Message(
                            Type="response",
                            Action=msg.Action,
                            To=msg.From,
                            RequestID=msg.RequestID,
                            Data=resp.get("result"),
                        )
                        await self.send(meta.to_json())

                        from .drivers import registry as drv_registry
                        if msg.Action not in self._active_streams:
                            self._active_streams[msg.Action] = {}
                        frm = msg.From or ""
                        if frm not in self._active_streams[msg.Action]:
                            self._active_streams[msg.Action][frm] = []
                        self._active_streams[msg.Action][frm].append(stream_id)
                        original_msg = msg
                        def on_frame(frame_msg):
                            asyncio.run_coroutine_threadsafe(
                                self._forward_stream(frame_msg, original_msg),
                                self._loop
                            )
                        drv_registry.start_stream(msg.Action, stream_id, on_frame)
                    else:
                        logger.error(i18n.translate("client.stream_start_failed", action=msg.Action, resp=resp))
                    return

                if type(msg.Data) is dict:
                    loop = asyncio.get_event_loop()
                    resp = await loop.run_in_executor(None, drivers[msg.Action], msg.Data)
                elif type(msg.Data) is list:
                    resp = None
                    loop = asyncio.get_event_loop()
                    for operator in msg.Data:
                        resp = await loop.run_in_executor(None, drivers[msg.Action], operator)
                else:
                    await self.Error.error(i18n.translate("client.invalid_data_type"), To=msg.From, RequestID=msg.RequestID)
                    return

                if isinstance(resp, dict) and resp.get("status") == "ok":
                    if "__data__" in resp:
                        data = resp.pop("__data__")
                        result = resp.get("result")
                        if isinstance(result, dict):
                            result["__binary__"] = True
                        meta = self.Message(
                            Type="response",
                            Action=msg.Action,
                            To=msg.From,
                            RequestID=msg.RequestID,
                            Data=result,
                            Binary=True,
                        )
                        await self.send_pair(meta, data)
                        logger.debug(i18n.translate("client.response_sent", size=len(data), to=msg.From))
                        return
                    else:
                        logger.error(i18n.translate("driver.no_data", result=resp.get("result")))

                elif isinstance(resp, dict) and resp.get("status") == "error":
                    logger.error(i18n.translate("driver.error", message=resp.get("message")))
                    await self.Error.error(resp.get("message", "Driver error"), To=msg.From, RequestID=msg.RequestID)
                else:
                    logger.error(i18n.translate("driver.unexpected_resp", resp=resp))

            elif msg.Type == "error":
                logger.error(msg.Data)
            else:
                await self.Error.error(i18n.translate("client.invalid_message_type"), To=msg.From, RequestID=msg.RequestID)
        except KeyError as e:
            logger.error(i18n.translate("client.key_error", error=e))
            await self.Error.error(i18n.translate("client.key_error", error=e), To=msg.From, RequestID=msg.RequestID)
        except Exception as e:
            logger.error(i18n.translate("client.exception_in_main", type=type(e).__name__, error=e), exc_info=True)
            await self.Error.error(str(e), To=msg.From, RequestID=msg.RequestID)

    async def _forward_stream(self, frame_msg: dict, original_msg):
        """将驱动回调的帧数据转发到服务端。"""
        try:
            if isinstance(frame_msg, dict) and frame_msg.get("BinaryData"):
                data = frame_msg["BinaryData"]
                result = frame_msg.get("Data", {})
                if isinstance(result, dict):
                    result["__binary__"] = True
                meta = self.Message(
                    Type="stream",
                    Action=original_msg.Action,
                    To=original_msg.From,
                    Data=result,
                    Binary=True,
                )
                await self.send_pair(meta, data)
        except Exception as e:
            logger.error(i18n.translate("client.stream_forward_error", error=e), exc_info=True)

    async def start_stream(self, to: str, params: dict):
        await self.send(self.Message(
            Type="command",
            Action="screen",
            To=to,
            Data={**params, "operate": "start_stream"},
        ).to_json())

    def on_close(self):
        from .drivers import registry as drv_registry


        drv_registry.stop_all_streams()
        self._active_streams.clear()
        logger.debug(i18n.translate("client.all_streams_stopped"))


def _build_client_config():
    import platform
    import pyautogui
    return {
        "url": Config.ws_url,
        "status": {
            "device": {
                "type": "client",
                "deviceName": Config.device_name,
                "deviceInfo": "",
                "platform": platform.platform(),
                "machine": platform.machine(),
                "appVersion": Config.version,
                "screenResolution": f"{pyautogui.size().width}x{pyautogui.size().height}",
            }
        }
    }


def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--driver-host":
        from .drivers.host import run_driver
        driver_file = sys.argv[2]
        packages_dir = sys.argv[3] if len(sys.argv) > 3 else None
        run_driver(driver_file, packages_dir)
        return

    if "--no-ui" not in sys.argv:
        try:
            import qasync
            from . import ui

            Config._ui = True
        except ImportError:
            pass

    async def start():
        from .drivers import registry
        registry.scan()

        client = Client(**_build_client_config())

        try:
            await client.connection
        finally:
            await client.close()

    try:
        if Config._ui:
            qt_app = ui.get_app()
            loop = qasync.QEventLoop(qt_app)
            asyncio.set_event_loop(loop)

            from .drivers import registry
            registry.scan()

            _state = {"client": None, "task": None}

            def _on_auth_changed(logged_in: bool):
                if logged_in:
                    if _state["client"]:
                        return
                    _state["client"] = Client(**_build_client_config())
                    _state["task"] = asyncio.ensure_future(_state["client"].connection)
                else:
                    if _state["task"] and not _state["task"].done():
                        _state["task"].cancel()
                    if _state["client"]:
                        client = _state["client"]
                        _state["client"] = None
                        _state["task"] = None
                        asyncio.ensure_future(client.close())

            ui.window.auth_changed.connect(_on_auth_changed)

            def _on_close():
                _on_auth_changed(False)
                ui.window.on_close()
                loop.stop()
            qt_app.lastWindowClosed.connect(_on_close)

            loop.run_forever()
        else:
            loop = asyncio.new_event_loop()
            task = loop.create_task(start())
            asyncio.set_event_loop(loop)
            loop.run_forever()
    except KeyboardInterrupt:
        if Config._ui:
            ui.window.on_close()
        else:
            task.cancel()
            try:
                loop.run_until_complete(task)
            except BaseException:
                pass
        logger.debug(i18n.translate("system.close"))
    finally:
        from .user.login import _login_proc
        if _login_proc is not None and _login_proc.is_alive():
            _login_proc.terminate()
        from .drivers import registry as drv_registry
        drv_registry.shutdown()


if __name__ == "__main__":
    main()
