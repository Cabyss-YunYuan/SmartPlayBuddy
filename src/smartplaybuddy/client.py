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
from .ws.bridge import LocalBridge
from .ws.logstream import get_log_forwarder, LOG_ACTION
# from .ws import message
import asyncio
from typing import Dict


logger = logger.logger.getChild("Client")


class _ServerReply:
    """回复通道：写回服务端那条唯一连接。接口与 LocalBridge._WebReply 一致，
    使 _execute_command 无需关心结果最终流向服务端还是本地网页。"""

    def __init__(self, client: "Client"):
        self._c = client

    async def send_json(self, payload: str):
        await self._c.send(payload)

    async def send_pair(self, meta, binary: bytes):
        await self._c.send_pair(meta, binary)

    async def error(self, data, To: str | None = None, RequestID: str | None = None):
        await self._c.Error.error(data, To=To, RequestID=RequestID)


class Client(ws.Connector):
    """业务客户端：接收服务端指令 → 调用本地驱动 → 回传结果。"""

    auto_auth = True

    #: 流 ping 探针 (单独验证服务端路由/停流行为时用)。
    _KEEPALIVE_ENABLED = True
    #: 保活周期(秒)：每轮对所有在发流(日志/驱动 × 本地/远程)各发一条 ping 探对端是否还在。
    _KEEPALIVE_INTERVAL = 1.0

    def __init__(self, **config):
        super().__init__(**config)
        self._stream_handler = None
        self._loop = asyncio.get_event_loop()
        # 活跃流记录: {action: {from: {stream_id: reply}}}
        # reply 与流同处存放：集中保活循环据此按每条流的原通道发 ping，无需另设映射。
        self._active_streams: Dict[str, Dict[str, Dict[str, object]]] = {}
        #: 本地 WS 桥(UI 模式下由 main() 注入)；用于把网页发起的远端请求回执转回网页
        self.bridge: LocalBridge | None = None
        #: 本机设备名，供桥判定命令是否指向本机
        self.device_name = Config.device_name
        self._server_reply = _ServerReply(self)
        #: 全局日志转发器(分级缓冲单例)；绑定当前事件循环用于实时转发
        self._log_forwarder = get_log_forwarder(self._loop)
        #: 集中保活循环：对所有在发流按 _KEEPALIVE_INTERVAL 周期探活(治对端离开后的"空跑")
        #: _KEEPALIVE_ENABLED=False 时不创建 task，彻底停发保活 ping(排查服务端时用)。
        self._keepalive_task = (
            self._loop.create_task(self._keepalive_loop()) if self._KEEPALIVE_ENABLED else None
        )

    async def main(self, msg) -> None:
        """处理服务端下发的消息。
        回程所有消息无条件透传(镜像)回内嵌窗口，无论本地是否处理过；
        command 额外就地执行(本机被远端控制的被控角色)，结果回给服务端。
        """
        if self.bridge is not None:
            self.bridge.broadcast_to_web(msg)

        if msg.Type == "command":
            await self._execute_command(msg, self._server_reply)
        elif msg.Type == "error":
            # 先按 rid 停流：命中=对端正常离开，停掉即止(stream_stopped 已记 info)。
            # 未命中(rid 缺失/不认识)才落 error，且挂起转发——否则 error→日志→帧→error 自激，
            # 日志会指数暴涨(曾撑到数百 MB)。
            if not self._stop_stream_by_rid(msg.RequestID):
                with self._log_forwarder.suppressed():
                    logger.error(msg.Data)
        elif msg.Type not in ("response", "stream", "system"):
            await self.Error.error(i18n.translate("client.invalid_message_type"), To=msg.From, RequestID=msg.RequestID)

    def _stop_stream_by_rid(self, rid) -> bool:
        """服务端流路由失败时，按 rid(=stream_id) 定向停这一条流；返回是否命中并停掉。

        信任闸门：仅当 rid 命中本机已登记的在发流(日志订阅 _subs / 驱动流 _active_streams)
        才停；rid 不认识则忽略。日志流只可能走 server_reply 通道，故限定该通道，绝不误伤
        本地内嵌(_WebReply)的订阅。"""
        if not rid:
            return False
        if self._log_forwarder.unsubscribe_by_stream(self._server_reply, rid):
            logger.info(i18n.translate("client.stream_stopped",
                                       action=LOG_ACTION, stream_id=rid, sender="route-error"))
            return True
        from .drivers import registry as drv_registry
        for action, streams in list(self._active_streams.items()):
            hit = False
            for frm, sids in list(streams.items()):
                if rid in sids:
                    del sids[rid]
                    if not sids:
                        streams.pop(frm, None)
                    hit = True
                    break
            if hit:
                drv_registry.stop_stream(action, rid)
                logger.info(i18n.translate("client.stream_stopped",
                                           action=action, stream_id=rid, sender="route-error"))
                return True
        return False

    async def execute_local(self, msg, reply, *, stream_key: str | None = None) -> None:
        """由 LocalBridge 调用：网页控制本机，执行结果直接回给该网页连接。"""
        await self._execute_command(msg, reply, stream_key=stream_key)

    async def _execute_command(self, msg, reply, *, stream_key: str | None = None) -> None:
        try:
            data = msg.Data if isinstance(msg.Data, dict) else {}
            operate = data.get("operate")
            _skey = stream_key or msg.From or ""

            # 内置日志流：action=log 不经驱动子进程，直接在主进程订阅 SmtPlay logger
            if msg.Action == LOG_ACTION:
                await self._handle_log_stream(msg, reply, data, operate)
                return

            if msg.Action == "*" and operate == "stop_stream":
                from .drivers import registry as drv_registry
                drv_registry.stop_all_streams()
                self._active_streams.clear()
                self._log_forwarder.clear()
                logger.info(i18n.translate("client.stream_stopped", action="all", stream_id="all", sender=msg.From or "server"))
                return

            if msg.Action not in drivers:
                resp = self.Message(Type="error", Action=msg.Action, To=msg.From, RequestID=msg.RequestID,
                                       Data=i18n.translate("driver.not_found", driver=msg.Action), )
                await reply.send_json(resp.to_json())
                logger.warning(i18n.translate("client.key_error", error=resp))
                return

            # 处理 stop_stream：移除回调并通知驱动
            if operate == "stop_stream":
                from .drivers import registry as drv_registry
                stream_id = data.get("stream_id")
                if _skey:
                    streams = self._active_streams.get(msg.Action, {})
                    if stream_id:
                        for f, sids in list(streams.items()):
                            if stream_id in sids:
                                del sids[stream_id]
                                if not sids:
                                    streams.pop(f, None)
                                break
                        drv_registry.stop_stream(msg.Action, stream_id)
                    else:
                        sids = streams.pop(_skey, {})
                        for sid in sids:
                            drv_registry.stop_stream(msg.Action, sid)
                    logger.info(i18n.translate("client.stream_stopped", action=msg.Action, stream_id=stream_id or "all", sender=_skey))
                else:
                    drv_registry.stop_all_streams(msg.Action)
                    self._active_streams.pop(msg.Action, None)
                    logger.info(i18n.translate("client.stream_stopped", action=msg.Action, stream_id=stream_id or "all", sender="server"))
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
                    await reply.send_json(meta.to_json())

                    from .drivers import registry as drv_registry
                    if msg.Action not in self._active_streams:
                        self._active_streams[msg.Action] = {}
                    if _skey not in self._active_streams[msg.Action]:
                        self._active_streams[msg.Action][_skey] = {}
                    self._active_streams[msg.Action][_skey][stream_id] = reply
                    original_msg = msg
                    def on_frame(frame_msg):
                        asyncio.run_coroutine_threadsafe(
                            self._forward_stream(frame_msg, original_msg, reply),
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
                await reply.error(i18n.translate("client.invalid_data_type"), To=msg.From, RequestID=msg.RequestID)
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
                    await reply.send_pair(meta, data)
                    logger.debug(i18n.translate("client.response_sent", size=len(data), to=msg.From))
                    return
                else:
                    logger.error(i18n.translate("driver.no_data", result=resp.get("result")))

            elif isinstance(resp, dict) and resp.get("status") == "error":
                logger.error(i18n.translate("driver.error", message=resp.get("message")))
                await reply.error(resp.get("message") or i18n.translate("driver.error_fallback"), To=msg.From, RequestID=msg.RequestID)
            else:
                logger.error(i18n.translate("driver.unexpected_resp", resp=resp))
        except KeyError as e:
            logger.error(i18n.translate("client.key_error", error=e))
            await reply.error(i18n.translate("client.key_error", error=e), To=msg.From, RequestID=msg.RequestID)
        except Exception as e:
            logger.error(i18n.translate("client.exception_in_main", type=type(e).__name__, error=e), exc_info=True)
            await reply.error(str(e), To=msg.From, RequestID=msg.RequestID)

    async def _forward_stream(self, frame_msg: dict, original_msg, reply):
        """将驱动回调的帧数据转发到回复通道(服务端或本地网页)。"""
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
                    RequestID=frame_msg.get("RequestID") or original_msg.RequestID,
                    Data=result,
                    Binary=True,
                )
                await reply.send_pair(meta, data)
        except Exception as e:
            logger.error(i18n.translate("client.stream_forward_error", error=e), exc_info=True)

    async def _handle_log_stream(self, msg, reply, data: dict, operate) -> None:
        """内置日志流：action=log，主进程直接订阅 SmtPlay logger，本地/远程 reply 通道通用。"""
        rid = msg.RequestID
        if operate == "start_stream":
            level = data.get("level", "INFO")
            name = data.get("name")
            tail = int(data.get("tail", 0) or 0)
            result = self._log_forwarder.subscribe(
                stream_id=rid, reply=reply, to=msg.From, request_id=rid,
                level=level, name=name, tail=tail,
            )
            resp = self.Message(
                Type="response", Action=LOG_ACTION, To=msg.From,
                RequestID=rid, Data=result,
            )
            await reply.send_json(resp.to_json())
            logger.info(i18n.translate("client.log_stream_started", to=msg.From or "local", level=result.get("level")))
            return

        if operate == "stop_stream":
            sid = data.get("stream_id") or rid
            self._log_forwarder.unsubscribe(reply, msg.From, sid)
            logger.info(i18n.translate("client.log_stream_stopped", stream_id=sid, sender=msg.From or "local"))
            return

        await reply.error(
            i18n.translate("client.log_unsupported_operate", operate=operate),
            To=msg.From, RequestID=rid,
        )

    async def close(self):
        if getattr(self, "_keepalive_task", None) and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._log_forwarder.clear()
        self._log_forwarder.unbind_loop(self._loop)
        await super().close()

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
        self._log_forwarder.unsubscribe_by_reply(self._server_reply)
        logger.debug(i18n.translate("client.all_streams_stopped"))

    async def _keepalive_loop(self):
        """每 _KEEPALIVE_INTERVAL 秒枚举所有活动流，各发一条 ping(RequestID=stream_id)探对端。

        - 远程流(经服务端)：ping 走 _ServerReply。对端"静默离开"(连接在、键已删)时，
          服务端弹回带 rid 的 "session not found"，由 main() 的 _stop_stream_by_rid 命中停流。
        - 发送本身失败 = 通道已断(本地网页关闭，或本机↔服务端断线)：记一笔后就地精确停这条流。
        """
        while True:
            try:
                await asyncio.sleep(self._KEEPALIVE_INTERVAL)
                if getattr(self, "conn", None) is None:
                    continue
                for reply, to, stream_id in self._keepalive_targets():
                    if not to:
                        continue
                    # 本地网页流(reply=_WebReply)的帧经桥直连浏览器、不经服务端；其记账地址
                    # (local:{conn})服务端从未注册，拿它 System.ping 只会走服务端连接并回
                    # "session not found"。存活由 LocalBridge 的连接关闭检测(_drop_conn)负责，
                    # 故只对经服务端的流(reply=_ServerReply)探活。
                    if reply is not self._server_reply:
                        continue
                    try:
                        await self.System.ping(To=to, RequestID=stream_id)
                    except Exception as e:
                        logger.debug(i18n.translate("client.keepalive_send_failed",
                                                    stream_id=stream_id, error=e))
                        self._stop_stream_by_target(reply, to, stream_id)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(i18n.translate("client.keepalive_tick_failed", error=e))

    def _keepalive_targets(self):
        """产出所有在发流的保活目标 (reply, to, stream_id)：日志订阅 + 驱动流。"""
        yield from self._log_forwarder.keepalive_targets()
        for action, streams in list(self._active_streams.items()):
            for frm, sids in list(streams.items()):
                for stream_id, reply in list(sids.items()):
                    yield reply, frm, stream_id

    def _stop_stream_by_target(self, reply, to, stream_id):
        """已知通道时精确停一条流(本地网页断开 / 本机↔服务端断线触发)：先试日志订阅，再试驱动流。"""
        if self._log_forwarder.unsubscribe_by_stream(reply, stream_id):
            logger.info(i18n.translate("client.stream_stopped",
                                       action=LOG_ACTION, stream_id=stream_id, sender="keepalive"))
            return
        from .drivers import registry as drv_registry
        for action, streams in list(self._active_streams.items()):
            sids = streams.get(to)
            if sids and sids.get(stream_id) is reply:
                del sids[stream_id]
                if not sids:
                    streams.pop(to, None)
                drv_registry.stop_stream(action, stream_id)
                logger.info(i18n.translate("client.stream_stopped",
                                           action=action, stream_id=stream_id, sender="keepalive"))
                return


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

    # 尽早安装全局分级日志缓冲(loop 稍后由 Client 绑定)，使登录前日志也可回放
    from .ws.logstream import get_log_forwarder
    get_log_forwarder()

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

            _state = {"client": None, "task": None, "bridge": None}

            async def _start_bridge(client):
                try:
                    bridge = LocalBridge(client, allowed_origins=Config.bridge_origins)
                    client.bridge = bridge
                    await bridge.start()
                    _state["bridge"] = bridge
                    ui.window.set_native_bridge(bridge.ws_url, bridge.token)
                except Exception as e:
                    logger.error(i18n.translate("bridge.start_failed", error=e), exc_info=True)

            async def _teardown(client):
                bridge = _state["bridge"]
                _state["bridge"] = None
                if bridge:
                    await bridge.stop()
                if Config._ui:
                    ui.window.clear_native_bridge()
                if client:
                    await client.close()

            def _on_auth_changed(logged_in: bool):
                if logged_in:
                    if _state["client"]:
                        return
                    client = Client(**_build_client_config())
                    _state["client"] = client
                    _state["task"] = asyncio.ensure_future(client.connection)
                    asyncio.ensure_future(_start_bridge(client))
                else:
                    if _state["task"] and not _state["task"].done():
                        _state["task"].cancel()
                    client = _state["client"]
                    _state["client"] = None
                    _state["task"] = None
                    asyncio.ensure_future(_teardown(client))

            ui.window.auth_changed.connect(_on_auth_changed)

            def _on_close():
                _on_auth_changed(False)
                ui.window.on_close()
                loop.stop()
            qt_app.lastWindowClosed.connect(_on_close)
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
