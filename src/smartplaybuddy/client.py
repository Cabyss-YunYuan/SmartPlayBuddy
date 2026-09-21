"""
客户端主模块。
继承 Connector，处理服务端下发的 command/error/stream 消息，
调度本地驱动执行并将结果回传。支持流式帧转发。
"""
from . import ws
from .utils import logger, translate
from .config import Config
from .drivers import drivers
from .ws.bridge import LocalBridge
from .ws.permit import PermitMixin
from .utils.logger import get_log_forwarder, LOG_ACTION
from .ws.stream import StreamSender
from .ws.message.message import generate_rid
import asyncio
import time
from typing import Dict


logger = logger.getChild("Client")


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


class Client(PermitMixin, ws.Connector):
    """业务客户端：接收服务端指令 → 调用本地驱动 → 回传结果。支持流式帧转发。"""

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
        self._stream_senders: Dict[str, "StreamSender"] = {}
        self.bridge: LocalBridge | None = None
        #: 本机设备名，供桥判定命令是否指向本机
        self.device_name = Config.device_name
        self._server_reply = _ServerReply(self)
        #: 全局日志转发器(分级缓冲单例)；绑定当前事件循环用于实时转发
        self._log_forwarder = get_log_forwarder(self._loop)
        #: 集中保活循环：对所有活动流按 _KEEPALIVE_INTERVAL 周期探活(治对端离开后的"空转")
        #: _KEEPALIVE_ENABLED=False 时不创建 task，彻底停发保活 ping(排查服务端时用)。
        self._keepalive_task = (
            self._loop.create_task(self._keepalive_loop()) if self._KEEPALIVE_ENABLED else None
        )
        # ── 授权状态 (PermitMixin 使用) ──
        self._passes: dict[str, dict] = {}
        self._event_lock: dict | None = None
        self._pending_auth = False
        #: 待处理的远程授权请求(广播发出后等待审批): {rid: {from, description}}
        self._pending_auth_requests: dict[str, dict] = {}
        #: 本机正在展示的 permission 弹窗: {rid: {from, description}}
        self._pending_permission_dialogs: dict[str, dict] = {}
        #: 已由本地弹窗或外部审批解决的 RID，防止重复发 mod 响应
        self._resolved_permission_rids: set[str] = set()
        #: 我们发出的 request 的 RID 集合，用于校验 response 是否合法
        self._outgoing_request_rids: set[str] = set()
        # ── 事件锁探针 ──
        self._probe_task: asyncio.Task | None = None
        self._probe_rids: set[str] = set()
        self._pass_probe_map: dict[str, str] = {}
        # ── 测速 ──
        self._speed_test_futures: dict[str, asyncio.Future] = {}
        self._speed_test_results: dict[str, dict] = {}
        self._speed_test_done = False

    @staticmethod
    def require_auth(func):
        """装饰器：授权校验。未通过校验的消息不进入业务逻辑。"""
        async def wrapper(self, msg):
            if self.bridge is not None:
                self.bridge.broadcast_to_web(msg)
            if not await self._check_auth(msg):
                logger.debug(translate("permit.unauthorized_blocked",
                                            type=msg.Type, rid=msg.RequestID, from_addr=msg.From))
                if msg.Type not in ("response", "stream", "system", "session"):
                    await self.Error.error("unauthorized", To=msg.From, RequestID=msg.RequestID)
                return
            return await func(self, msg)
        return wrapper

    @require_auth
    @ws.Connector.system_dispatch
    @ws.Connector.session_dispatch
    async def main(self, msg) -> None:
        """处理服务端下发的消息。"""
        if msg.Type == "command":
            await self._execute_command(msg, self._server_reply)
        elif msg.Type == "query":
            await self._handle_query(msg, self._server_reply)
        elif msg.Type == "request":
            if msg.Action == "permission":
                asyncio.create_task(self._handle_permission_request(msg))
            else:
                asyncio.create_task(self._handle_auth_request(msg))
        elif msg.Type == "response":
            if msg.Action == "permission":
                await self._handle_permission_approved(msg)
        elif msg.Type == "event":
            await self._handle_event(msg)
        elif msg.Type == "error":
            if msg.Action == "permit" and isinstance(msg.Data, dict) and msg.Data.get("operate") == "revoked":
                pass
            elif msg.RequestID in self._probe_rids:
                self._probe_rids.discard(msg.RequestID)
                pass_rid = self._pass_probe_map.pop(msg.RequestID, None)
                if pass_rid:
                    self._passes.pop(pass_rid, None)
                    logger.warning(translate("permit.pass_probe_failed", pass_rid=pass_rid))
                else:
                    logger.warning(translate("permit.probe_failed", request_id=self._event_lock.get("request_id", "") if self._event_lock else ""))
                    await self._do_revoke()
            elif not self._stop_stream_by_rid(msg.RequestID):
                with self._log_forwarder.suppressed():
                    logger.warning(msg.Data)
        elif msg.Type not in ("response", "stream", "session"):
            await self.Error.error(translate("client.invalid_message_type"), To=msg.From, RequestID=msg.RequestID)

    # ── 查询 ──

    async def _handle_query(self, msg, reply) -> None:
        """处理 query 类型消息（只读查询）。"""
        if msg.Action == "streams":
            await self._handle_query_streams(msg, reply)
        elif msg.Action == "permit":
            await self._reply_permit_query(msg, reply)
        else:
            await reply.error(translate("client.query_action_not_found",
                                             action=msg.Action),
                              To=msg.From, RequestID=msg.RequestID)

    async def _handle_query_streams(self, msg, reply) -> None:
        """查询流状态：无 stream_id 返回列表，有则返回单条详情。"""
        data = msg.Data if isinstance(msg.Data, dict) else {}
        target_sid = data.get("stream_id")

        driver_streams = self._collect_driver_streams(target_sid)
        log_streams = self._collect_log_streams(target_sid)
        all_streams = driver_streams + log_streams

        if target_sid and not all_streams:
            await reply.error(translate("client.query_stream_not_found",
                                             stream_id=target_sid),
                              To=msg.From, RequestID=msg.RequestID)
            return

        resp = self.Message(
            Type="response", Action="streams",
            To=msg.From, RequestID=msg.RequestID,
            Data={"streams": all_streams},
        )
        await reply.send_json(resp.to_json())

    def _collect_driver_streams(self, target_sid: str | None = None) -> list[dict]:
        """收集驱动流信息。target_sid 非空时只返回匹配的流。"""
        result = []
        for action, from_map in self._active_streams.items():
            for frm, sid_map in from_map.items():
                for sid, _reply in sid_map.items():
                    if target_sid and sid != target_sid:
                        continue
                    sender = self._stream_senders.get(sid)
                    entry: dict = {
                        "stream_id": sid,
                        "action": action,
                        "type": "driver",
                        "target": frm,
                    }
                    if sender and target_sid:
                        entry["stats"] = sender.get_stats()
                    result.append(entry)
        return result

    def _collect_log_streams(self, target_sid: str | None = None) -> list[dict]:
        """收集日志流信息。target_sid 非空时只返回匹配的流。"""
        subs = self._log_forwarder.get_subscriptions()
        result = []
        for sub in subs:
            if target_sid and sub["stream_id"] != target_sid:
                continue
            entry: dict = {
                "stream_id": sub["stream_id"],
                "action": LOG_ACTION,
                "type": "log",
                "target": sub["target"],
            }
            if target_sid:
                entry["level"] = sub["level"]
                entry["name"] = sub["name"]
            result.append(entry)
        return result

    # ── 停流 ──

    async def _handle_stop_stream(self, msg, reply, data: dict) -> None:
        """command/stop-stream：按 stream_id 停流，"*" 停所有。"""
        stream_id = data.get("stream_id", "")
        if stream_id == "*":
            self.stop_all_streams()
            resp = self.Message(
                Type="response", Action="stop-stream",
                To=msg.From, RequestID=msg.RequestID,
                Data={"stopped": "all"},
            )
        elif self.stop_stream_by_id(stream_id):
            resp = self.Message(
                Type="response", Action="stop-stream",
                To=msg.From, RequestID=msg.RequestID,
                Data={"stopped": stream_id},
            )
        else:
            resp = self.Message(
                Type="response", Action="stop-stream",
                To=msg.From, RequestID=msg.RequestID,
                Data={"stopped": None, "error": "not_found"},
            )
        await reply.send_json(resp.to_json())

    def stop_stream_by_id(self, stream_id: str) -> bool:
        """按 stream_id 跨 action 停流：日志流 → 驱动流。返回是否命中。"""
        if self._log_forwarder.unsubscribe_by_stream(self._server_reply, stream_id):
            logger.debug(translate("client.stream_stopped",
                                        action=LOG_ACTION, stream_id=stream_id, sender="api"))
            return True
        from .drivers import registry as drv_registry
        for action, streams in list(self._active_streams.items()):
            for frm, sids in list(streams.items()):
                if stream_id in sids:
                    del sids[stream_id]
                    if not sids:
                        streams.pop(frm, None)
                    self._stop_sender(stream_id)
                    drv_registry.stop_stream(action, stream_id)
                    logger.debug(translate("client.stream_stopped",
                                                action=action, stream_id=stream_id, sender="api"))
                    return True
        return False

    def stop_all_streams(self):
        """停止所有流（驱动流 + 日志流）。"""
        from .drivers import registry as drv_registry
        drv_registry.stop_all_streams()
        self._active_streams.clear()
        self._stop_all_senders()
        self._log_forwarder.clear()
        logger.debug(translate("client.stream_stopped",
                                    action="all", stream_id="all", sender="api"))

    # ── 流管理 ──

    def _stop_stream_by_rid(self, rid) -> bool:
        """服务端流路由失败时，按 rid(=stream_id) 定向停这一条流；返回是否命中并停掉。

        信任闸门：仅当 rid 命中本机已登记的在发流(日志订阅 _subs / 驱动流 _active_streams)
        才停；rid 不认识则忽略。日志流只可能走 server_reply 通道，故限定该通道，绝不误伤
        本地内嵌(_WebReply)的订阅。"""
        if not rid:
            return False
        if self._log_forwarder.unsubscribe_by_stream(self._server_reply, rid):
            logger.debug(translate("client.stream_stopped",
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
                self._stop_sender(rid)
                drv_registry.stop_stream(action, rid)
                logger.debug(translate("client.stream_stopped",
                                            action=action, stream_id=rid, sender="route-error"))
                return True
        return False

    def _stop_sender(self, stream_id: str):
        sender = self._stream_senders.pop(stream_id, None)
        if sender:
            sender.stop()

    def _stop_all_senders(self):
        for sender in self._stream_senders.values():
            sender.stop()
        self._stream_senders.clear()

    async def execute_local(self, msg, reply) -> None:
        """由 LocalBridge 调用：网页控制本机，执行结果直接回给该网页连接。"""
        if not msg.From:
            uid = getattr(Config, "user", {}).get("uid", "")
            msg.From = f"client:{uid}:{self.device_name}"
        await self.main(msg)

    async def _execute_command(self, msg, reply, *, stream_key: str | None = None) -> None:
        try:
            data = msg.Data if isinstance(msg.Data, dict) else {}
            operate = data.get("operate")
            _skey = stream_key or msg.From or ""

            if msg.Action == "permit":
                await self._handle_permit_command(msg)
                return

            if msg.Action == "stop-stream":
                await self._handle_stop_stream(msg, reply, data)
                return

            # 内置日志流：action=log 不经驱动子进程，直接在主进程订阅 SmtPlay logger
            if msg.Action == LOG_ACTION:
                await self._handle_log_stream(msg, reply, data, operate)
                return

            if msg.Action not in drivers:
                resp = self.Message(Type="error", Action=msg.Action, To=msg.From, RequestID=msg.RequestID,
                                    Data=translate("driver.not_found", driver=msg.Action), )
                await reply.send_json(resp)
                logger.warning(translate("client.key_error", error=resp))
                return

            # 处理 stop-stream：移除回调并通知驱动
            if operate == "stop-stream":
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
                        self._stop_sender(stream_id)
                        drv_registry.stop_stream(msg.Action, stream_id)
                    else:
                        sids = streams.pop(_skey, {})
                        for sid in sids:
                            self._stop_sender(sid)
                            drv_registry.stop_stream(msg.Action, sid)
                    logger.debug(translate("client.stream_stopped", action=msg.Action, stream_id=stream_id or "all", sender=_skey))
                else:
                    drv_registry.stop_all_streams(msg.Action)
                    for sid in list(self._stream_senders):
                        self._stop_sender(sid)
                    self._active_streams.pop(msg.Action, None)
                    logger.debug(translate("client.stream_stopped", action=msg.Action, stream_id=stream_id or "all", sender="server"))
                return

            # 处理 start-stream：发送响应后注册流回调
            if operate == "start-stream":
                stream_id = msg.RequestID
                msg.Data["stream_id"] = stream_id
                resp = drivers[msg.Action](msg.Data)
                if isinstance(resp, dict) and resp.get("status") == "ok":
                    meta = self.Message(
                        Type="response", Action=msg.Action,
                        To=msg.From, RequestID=msg.RequestID,
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
                    bw_ctrl = self._bw_controller if reply is self._server_reply else None
                    send_fps = int(msg.Data.get("send_fps", 0))
                    send_bw = float(msg.Data.get("send_bw", 0))
                    queue_size = int(msg.Data.get("queue_size", 3))
                    sender = StreamSender(queue_size, self._loop, reply, original_msg,
                                          self.Message,
                                          bandwidth_controller=bw_ctrl,
                                          send_fps=send_fps, send_bw=send_bw)
                    self._stream_senders[stream_id] = sender

                    # 服务端链路流：先启动帧泵，再异步测速到对端设定单流上限
                    if reply is self._server_reply and msg.From:
                        self._loop.create_task(
                            self._apply_stream_speed_test(sender, msg.From, stream_id)
                        )

                    def on_frame(frame_msg, _sender=sender):
                        frame_msg["_ts"] = time.monotonic()
                        _sender.enqueue(frame_msg)
                    drv_registry.start_stream(msg.Action, stream_id, on_frame)
                else:
                    logger.error(translate("client.stream_start_failed", action=msg.Action, resp=resp))
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
                await reply.error(translate("client.invalid_data_type"), To=msg.From, RequestID=msg.RequestID)
                return

            if isinstance(resp, dict) and resp.get("status") == "ok":
                if "__data__" in resp:
                    data = resp.pop("__data__")
                    result = resp.get("result")
                    if isinstance(result, dict):
                        result["__binary__"] = True
                    meta = self.Message(
                        Type="response", Action=msg.Action,
                        To=msg.From, RequestID=msg.RequestID,
                        Data=result, Binary=True,
                    )
                    await reply.send_pair(meta, data)
                    logger.debug(translate("client.response_sent", size=len(data), to=msg.From))
                    return

            elif isinstance(resp, dict) and resp.get("status") == "error":
                logger.error(translate("driver.error", message=resp.get("message")))
                await reply.error(resp.get("message") or translate("driver.error_fallback"), To=msg.From, RequestID=msg.RequestID)
            else:
                logger.error(translate("driver.unexpected_resp", resp=resp))
        except KeyError as e:
            logger.error(translate("client.key_error", error=e))
            await reply.error(translate("client.key_error", error=e), To=msg.From, RequestID=msg.RequestID)
        except Exception as e:
            logger.error(translate("client.exception_in_main", type=type(e).__name__, error=e), exc_info=True)
            await reply.error(str(e), To=msg.From, RequestID=msg.RequestID)

    async def _handle_log_stream(self, msg, reply, data: dict, operate) -> None:
        """内置日志流：action=log，主进程直接订阅 SmtPlay logger，本地/远程 reply 通道通用。"""
        rid = msg.RequestID
        if operate == "start-stream":
            level = data.get("level", "INFO")
            name = data.get("name")
            tail = int(data.get("tail", 0) or 0)
            result = self._log_forwarder.subscribe(
                stream_id=rid, reply=reply, to=msg.From, request_id=rid,
                level=level, name=name, tail=tail,
            )
            resp = self.Message(
                Type="response", Action=LOG_ACTION,
                To=msg.From, RequestID=rid, Data=result,
            )
            await reply.send_json(resp)
            logger.debug(translate("client.log_stream_started", to=msg.From or "local", level=result.get("level")))

            # 远程日志流：异步测速到对端，结果初始化全局 BandwidthController
            if reply is self._server_reply and msg.From:
                self._loop.create_task(self._measure_log_stream_bandwidth(msg.From))
            return

        if operate == "stop-stream":
            sid = data.get("stream_id") or rid
            self._log_forwarder.unsubscribe(reply, msg.From, sid)
            logger.debug(translate("client.log_stream_stopped", stream_id=sid, sender=msg.From or "local"))
            return

        await reply.error(
            translate("client.log_unsupported_operate", operate=operate),
            To=msg.From, RequestID=rid,
        )

    # ── 生命周期 ──

    async def close(self):
        if getattr(self, "_keepalive_task", None) and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._log_forwarder.clear()
        self._log_forwarder.unbind_loop(self._loop)
        self._passes.clear()
        self._event_lock = None
        self._pending_auth = False
        self._pending_auth_requests.clear()
        self._pending_permission_dialogs.clear()
        self._resolved_permission_rids.clear()
        self._outgoing_request_rids.clear()
        for fut in self._speed_test_futures.values():
            if not fut.done():
                fut.cancel()
        self._speed_test_futures.clear()
        self._speed_test_results.clear()
        await super().close()

    async def start_stream(self, to: str, params: dict):
        await self.send(self.Message(
            Type="command", Action="screen",
            To=to, Data={**params, "operate": "start-stream"},
        ))

    def on_close(self):
        from .drivers import registry as drv_registry

        self._stop_event_probe()
        drv_registry.stop_all_streams()
        self._active_streams.clear()
        self._stop_all_senders()
        self._log_forwarder.unsubscribe_by_reply(self._server_reply)
        logger.debug(translate("client.all_streams_stopped"))

    def _on_session_info_updated(self):
        info = self.session_info or {}
        device = info.get("device") if isinstance(info, dict) else None
        if not isinstance(device, dict):
            return
        name = device.get("deviceName")
        if name and name != self.device_name:
            old = self.device_name
            self.device_name = name
            logger.debug(translate("client.device_name_updated", old=old, new=name))
        if not self._speed_test_done:
            self._speed_test_done = True
            self._loop.create_task(self._run_speed_test())

    # ── 保活 ──

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
                        logger.debug(translate("client.keepalive_send_failed",
                                                    stream_id=stream_id, error=e))
                        self._stop_stream_by_target(reply, to, stream_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(translate("client.keepalive_tick_failed", error=e))

    def _keepalive_targets(self):
        """产出所有活动流的保活目标 (reply, to, stream_id)：日志订阅 + 驱动流。"""
        yield from self._log_forwarder.keepalive_targets()
        for action, streams in list(self._active_streams.items()):
            for frm, sids in list(streams.items()):
                for stream_id, reply in list(sids.items()):
                    yield reply, frm, stream_id

    def _stop_stream_by_target(self, reply, to, stream_id):
        """已知通道时精确停一条流(本地网页断开 / 本机↔服务端断线触发)：先试日志订阅，再试驱动流。"""
        if self._log_forwarder.unsubscribe_by_stream(reply, stream_id):
            logger.debug(translate("client.stream_stopped",
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
                logger.debug(translate("client.stream_stopped",
                                            action=action, stream_id=stream_id, sender="keepalive"))
                return

    async def _run_speed_test(self):
        """渐进式测速到服务端：初始化全局 BandwidthController。

        每轮: 发送 payload → result 到达(上行段 t1) → return 回显到达(下行段 t2)。
        estimate 取上行段带宽，因为 BandwidthController 限的是本机上行。
        """
        results = await self._run_speed_test_rounds(target=None, max_size=10_000_000, use_upload_only=True)
        if results:
            best = max(results)
            self._bw_controller.set_estimate(best * 0.8)

    async def _apply_stream_speed_test(self, sender: "StreamSender", target: str, stream_id: str):
        """异步测速到对端设备，结果设定单流带宽上限。首次测速同时初始化全局 BandwidthController。"""
        try:
            bw = await self._run_speed_test_to(target)
            if bw > 0:
                stream_bw = bw * 0.8
                if self._bw_controller.estimate <= 0:
                    self._bw_controller.set_estimate(stream_bw)
                sender.set_stream_bw(stream_bw)
                logger.debug(translate("client.bw_stream_estimate",
                                            stream_id=stream_id, target=target,
                                            bps=f"{stream_bw / 125:.0f} KB/s"))
            else:
                logger.warning(translate("client.bw_stream_test_no_result",
                                              stream_id=stream_id, target=target))
        except Exception as e:
            logger.warning(translate("client.bw_stream_test_failed",
                                          stream_id=stream_id, error=e))

    async def _measure_log_stream_bandwidth(self, target: str):
        """日志流触发的异步测速：结果仅用于初始化全局 BandwidthController。"""
        try:
            bw = await self._run_speed_test_to(target)
            if bw > 0:
                if self._bw_controller.estimate <= 0:
                    self._bw_controller.set_estimate(bw * 0.8)
                logger.debug(translate("client.bw_stream_estimate",
                                            stream_id="log", target=target,
                                            bps=f"{bw * 0.8 / 125:.0f} KB/s"))
            else:
                logger.warning(translate("client.bw_stream_test_no_result",
                                              stream_id="log", target=target))
        except Exception as e:
            logger.warning(translate("client.bw_stream_test_failed",
                                          stream_id="log", error=e))

    async def _run_speed_test_to(self, target: str) -> float:
        """测速到指定对端设备，返回路径瓶颈带宽(bytes/s)。

        路径为 本机→服务端→对端：t1 覆盖 本机上行+服务端转发+对端回 result，
        t2 覆盖 对端回显下行。取 min(上行, 下行) 保守估计整条路径容量。
        """
        results = await self._run_speed_test_rounds(target=target, max_size=50_000_000, use_upload_only=False)
        return max(results) if results else 0.0

    async def _run_speed_test_rounds(self, target: str | None, max_size: int, use_upload_only: bool) -> list[float]:
        from .ws.message.system import speed_test

        if target is None:
            await self.wait_ready(timeout=5.0)

        results: list[float] = []
        size = 100_000
        for _ in range(7):
            rid = None
            try:
                rid = generate_rid()
                meta, payload = speed_test(payload_size=size, RequestID=rid)
                if target is not None:
                    meta.To = target
                fut = self._loop.create_future()
                self._speed_test_futures[rid] = fut
                sent_at = time.monotonic()
                await self.send_pair(meta, payload)

                res = await asyncio.wait_for(fut, timeout=15.0)
                result_at = res.get("result_at") or sent_at
                return_at = res.get("return_at") or result_at
                t1 = result_at - sent_at
                t2 = max(0.0, return_at - result_at)

                if t1 > 0.001:
                    upload_bps = len(payload) / t1
                    download_bps = len(payload) / t2 if t2 > 0.001 else 0.0
                    if use_upload_only:
                        results.append(upload_bps)
                    else:
                        results.append(min(upload_bps, download_bps) if download_bps > 0 else upload_bps)
                    logger.debug(translate("client.bw_round_diag",
                                                size=size, t1=f"{t1*1000:.1f}", t2=f"{t2*1000:.1f}",
                                                up=f"{upload_bps/125:.0f}", down=f"{download_bps/125:.0f}"))
                    if t1 < 0.1 and size < max_size:
                        size = min(size * 5, max_size)
            except Exception as e:
                logger.debug(translate("client.bw_speed_test_failed", error=e))
            finally:
                if rid is not None:
                    self._speed_test_futures.pop(rid, None)
                    self._speed_test_results.pop(rid, None)

        return results
def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--driver-host":
        from .drivers.host import run_driver
        driver_file = sys.argv[2]
        packages_dir = sys.argv[3] if len(sys.argv) > 3 else None
        run_driver(driver_file, packages_dir)
        return

    # 尽早安装全局分级日志缓冲(loop 稍后由 Client 绑定)，使登录前日志也可回放
    from .utils.logger import get_log_forwarder
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

        client = Client(**Config.build_connect_config())

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
                    logger.error(translate("bridge.start_failed", error=e), exc_info=True)

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
                    client = Client(**Config.build_connect_config())
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
        logger.debug(translate("system.close"))
    finally:
        from .user.login import _login_proc
        if _login_proc is not None and _login_proc.is_alive():
            _login_proc.terminate()
        from .drivers import registry as drv_registry
        drv_registry.shutdown()


if __name__ == "__main__":
    main()
