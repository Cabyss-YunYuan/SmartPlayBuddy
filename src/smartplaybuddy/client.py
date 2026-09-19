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
        #: 本地 WS 桥(UI 模式下由 main() 注入)；用于把网页发起的远端请求回执转回网页
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
        # ── 授权状态 ──
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

    @staticmethod
    def system_dispatch(func):
        """装饰器：系统消息处理。ping/pong 就地回复，不进入业务逻辑。"""
        async def wrapper(self, msg):
            if msg.Type == "system":
                from .ws import logic as ws_logic
                ws_logic.system(self, msg)
                return
            return await func(self, msg)
        return wrapper

    @staticmethod
    def require_auth(func):
        """装饰器：授权校验。未通过校验的消息不进入业务逻辑。"""
        async def wrapper(self, msg):
            if self.bridge is not None:
                self.bridge.broadcast_to_web(msg)
            if not await self._check_auth(msg):
                logger.debug(i18n.translate("permit.unauthorized_blocked",
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
        """处理服务端下发的消息。
        command 就地执行(本机被远端控制的被控角色)，结果回给服务端。
        """
        if msg.Type == "command":
            await self._execute_command(msg, self._server_reply)
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
                    logger.warning(i18n.translate("permit.pass_probe_failed", pass_rid=pass_rid))
                else:
                    logger.warning(i18n.translate("permit.probe_failed", request_id=self._event_lock.get("request_id", "") if self._event_lock else ""))
                    await self._do_revoke()
            elif not self._stop_stream_by_rid(msg.RequestID):
                with self._log_forwarder.suppressed():
                    logger.warning(msg.Data)
        elif msg.Type not in ("response", "stream", "session"):
            await self.Error.error(i18n.translate("client.invalid_message_type"), To=msg.From, RequestID=msg.RequestID)

    # ── 授权校验 ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_uid(address: str | None) -> str:
        """从设备地址 '{type}:{uid}:{deviceName}[:...]' 中提取 uid(第二段)。"""
        if not address:
            return ""
        parts = address.split(":")
        return parts[1] if len(parts) >= 2 else ""

    async def _check_auth(self, msg) -> bool:
        """统一授权校验。返回 True 放行，False 拒绝。"""
        sender_uid = self._extract_uid(msg.From)
        local_uid = str(self.session_info.get("userId", "")) if isinstance(self.session_info, dict) else ""
        if not local_uid:
            from .ws.bridge import _resolve_uid
            local_uid = _resolve_uid()

        # From 为空 = 服务端自身产生的消息(非来自任何设备)，直接放行
        if not sender_uid:
            return True

        rid = msg.RequestID

        # 事件锁处理（优先于 UID 检查，确保同 UID 也能建立/匹配事件锁）
        if rid:
            # 已有事件锁 → 放行
            if self._event_lock and self._event_lock.get("request_id") == rid:
                return True

            # 一次性放行 → 激活事件锁
            if rid in self._passes:
                if msg.Type == "event":
                    info = self._passes.pop(rid)
                    self._event_lock = {
                        "request_id": rid,
                        "from": msg.From,
                        "description": info.get("description", ""),
                    }
                    logger.info(i18n.translate("permit.event_activated", request_id=rid))
                    self._stop_pass_probe(rid)
                    self._start_event_probe()
                else:
                    del self._passes[rid]
                return True

        # 同 UID → 放行
        if sender_uid == local_uid:
            return True

        if not rid:
            return False

        # request：放行，记录 RID 用于后续 response 校验
        if msg.Type == "request":
            self._outgoing_request_rids.add(rid)
            return True

        # response：仅放行我们实际处理过的 request 的回执
        if msg.Type == "response":
            if rid in self._outgoing_request_rids:
                self._outgoing_request_rids.discard(rid)
                return True
            return False

        if msg.Type == "event" and msg.Action == "permit":
            logger.debug(i18n.translate("permit.activate_blocked_by_auth",
                                        rid=rid, sender_uid=sender_uid, local_uid=local_uid))
        return False

    # ── 授权请求处理 ──────────────────────────────────────────────────

    def _self_address_prefix(self) -> str:
        """本机 client 在服务端的地址前缀：client:{uid}:{deviceName}。"""
        uid = ""
        if isinstance(self.session_info, dict):
            uid = str(self.session_info.get("userId", ""))
        if not uid:
            from .ws.bridge import _resolve_uid
            uid = _resolve_uid()
        if not uid:
            return ""
        return f"client:{uid}:{self.device_name}"

    async def _handle_auth_request(self, msg):
        """处理来自 Mod 的跨 UID 授权请求(request/request_permit)。

        不直接弹窗，而是广播 request(permission) 到所有同 UID 设备，
        由回环触发本地弹窗，或由其他设备审批后回传结果。
        """
        data = msg.Data if isinstance(msg.Data, dict) else {}
        operate = data.get("operate")

        if operate != "request_permit":
            await self.Error.error(
                i18n.translate("permit.unsupported_operate", operate=operate),
                To=msg.From, RequestID=msg.RequestID,
            )
            return

        if self._pending_auth:
            await self._send_auth_response(msg, "rejected", reason="busy")
            return

        if self._event_lock is not None:
            await self._send_auth_response(msg, "rejected", reason="busy")
            return

        self._pending_auth = True
        description = data.get("description", "")
        rid = msg.RequestID

        self._pending_auth_requests[rid] = {
            "from": msg.From,
            "description": description,
        }

        await self._broadcast_permission_request(rid, msg.From, description)

    async def _handle_permission_request(self, msg):
        """处理广播的 permission 请求：来自自己=回环弹窗，来自其他设备=忽略。"""
        data = msg.Data if isinstance(msg.Data, dict) else {}
        from_addr = msg.From
        self_prefix = self._self_address_prefix()

        if not (self_prefix and from_addr == self_prefix):
            return

        if not self._pending_auth:
            return

        rid = msg.RequestID
        if rid not in self._pending_auth_requests:
            return

        description = data.get("description", "")
        requester = data.get("requester", "")

        future = asyncio.get_event_loop().create_future()
        self._pending_permission_dialogs[rid] = {"future": future, "dialog": None}

        if getattr(Config, "_ui", False):
            asyncio.ensure_future(self._show_permission_dialog(requester or from_addr, description, future, rid))
        else:
            from .ui.request import prompt_auth_cli
            asyncio.ensure_future(
                self._resolve_dialog_future(prompt_auth_cli(requester or from_addr, description), future)
            )

        approved = await future

        self._pending_permission_dialogs.pop(rid, None)

        if rid in self._resolved_permission_rids:
            return

        info = self._pending_auth_requests.pop(rid, None)
        if not info:
            return
        self._resolved_permission_rids.add(rid)
        self._pending_auth = False

        if approved:
            mod_address = info["from"]
            self._passes[rid] = {
                "from": mod_address,
                "description": info.get("description", ""),
            }
            resp = self.Message(
                Type="response",
                Action="permit",
                To=mod_address,
                RequestID=rid,
                Data={"status": "approved", "request_id": rid},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.request_approved", request_id=rid))
            self._start_pass_probe(rid, mod_address)
            await self._broadcast_permit_state({
                "from": mod_address,
                "description": info.get("description", ""),
                "request_id": rid,
            })
            await self._broadcast_permission_resolved(rid, "approved")
        else:
            resp = self.Message(
                Type="response",
                Action="permit",
                To=info["from"],
                RequestID=rid,
                Data={"status": "rejected", "request_id": rid},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.request_rejected", request_id=rid))
            await self._broadcast_permission_resolved(rid, "rejected")

    async def _show_permission_dialog(self, requester: str, description: str, future: asyncio.Future, rid: str):
        """创建并显示授权对话框，立即存储 dialog 引用以便外部关闭。"""
        from .ui.request import AuthRequestDialog
        from .ui import window as _main_window

        parent = _main_window if _main_window else None
        dialog = AuthRequestDialog(requester, description, parent)
        dialog._future = future
        dialog.open()

        if rid in self._pending_permission_dialogs:
            self._pending_permission_dialogs[rid]["dialog"] = dialog

        try:
            await future
        except Exception:
            pass

    @staticmethod
    async def _resolve_dialog_future(awaitable, future: asyncio.Future):
        """将协程的结果转发到可外部解决的 future。"""
        try:
            result = await awaitable
            if not future.done():
                future.set_result(result)
        except Exception:
            if not future.done():
                future.set_result(False)

    async def _handle_permission_approved(self, msg):
        """处理 permission 请求的 response 回传(本地回环或其他设备审批)。"""
        rid = msg.RequestID
        from_addr = msg.From
        self_prefix = self._self_address_prefix()

        dialog_info = self._pending_permission_dialogs.pop(rid, None)
        if dialog_info:
            fut = dialog_info.get("future")
            if fut and not fut.done():
                fut.set_result(False)
            dlg = dialog_info.get("dialog")
            if dlg:
                dlg.close()

        if self_prefix and from_addr == self_prefix:
            return

        data = msg.Data if isinstance(msg.Data, dict) else {}
        status = data.get("status", "unknown")
        resolved_by = data.get("resolved_by", from_addr)

        if rid in self._resolved_permission_rids:
            return
        self._resolved_permission_rids.add(rid)

        info = self._pending_auth_requests.pop(rid, None)
        if not info:
            logger.info(i18n.translate("permit.broadcast_resolved",
                                       request_id=rid, resolved_by=resolved_by, status=status))
            return

        self._pending_auth = False
        mod_address = info["from"]
        description = info.get("description", "")

        if status == "approved":
            self._passes[rid] = {
                "from": mod_address,
                "description": description,
            }
            resp = self.Message(
                Type="response",
                Action="permit",
                To=mod_address,
                RequestID=rid,
                Data={"status": "approved", "request_id": rid},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.remote_approved", request_id=rid, from_addr=resolved_by))
            self._start_pass_probe(rid, mod_address)
            await self._broadcast_permit_state({
                "from": mod_address,
                "description": description,
                "request_id": rid,
            })
        else:
            resp = self.Message(
                Type="response",
                Action="permit",
                To=mod_address,
                RequestID=rid,
                Data={"status": "rejected", "request_id": rid},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.remote_rejected", request_id=rid, from_addr=resolved_by))

        await self._broadcast_permission_resolved(rid, status)

    async def _broadcast_permission_resolved(self, rid: str, status: str):
        """广播 response(permission) 通知所有设备关闭弹窗。"""
        uid = ""
        if isinstance(self.session_info, dict):
            uid = str(self.session_info.get("userId", ""))
        if not uid:
            from .ws.bridge import _resolve_uid
            uid = _resolve_uid()
        if not uid:
            return
        await self.send(self.Message(
            Type="response",
            Action="permission",
            To=f"client|web:{uid}:*",
            RequestID=rid,
            Data={"status": status, "resolved_by": self._self_address_prefix()},
        ))

    async def _broadcast_permission_request(self, rid: str, mod_address: str, description: str):
        """广播 request(permission) 到所有同 UID 设备，请求用户确认。"""
        uid = ""
        if isinstance(self.session_info, dict):
            uid = str(self.session_info.get("userId", ""))
        if not uid:
            from .ws.bridge import _resolve_uid
            uid = _resolve_uid()
        if not uid:
            return
        await self.send(self.Message(
            Type="request",
            Action="permission",
            To=f"client|web:{uid}:*",
            RequestID=rid,
            Data={"requester": mod_address, "description": description},
        ))

    async def _handle_event(self, msg):
        """处理 event 类型消息(释放等)。"""
        data = msg.Data if isinstance(msg.Data, dict) else {}
        operate = data.get("operate")

        if operate == "release":
            if self._event_lock and self._event_lock.get("request_id") == msg.RequestID:
                self._stop_event_probe()
                self._event_lock = None
                logger.info(i18n.translate("permit.event_released", request_id=msg.RequestID))
                await self._broadcast_permit_state(None)
            return

        if operate == "permit_state":
            if self.bridge:
                self.bridge.broadcast_to_web(msg)
            return

    async def _handle_permit_command(self, msg):
        """处理 permit 相关命令(revoke/query)，由用户主动发起。"""
        data = msg.Data if isinstance(msg.Data, dict) else {}
        operate = data.get("operate")

        if operate == "revoke":
            await self._do_revoke()
        elif operate == "query":
            await self._reply_permit_query(msg)
        else:
            await self.Error.error(
                i18n.translate("permit.unsupported_operate", operate=operate),
                To=msg.From, RequestID=msg.RequestID,
            )

    async def _do_revoke(self):
        """清除事件锁，通知 mod 权限已被用户收回，广播状态变化。"""
        lock_info = self._event_lock
        if not lock_info:
            logger.debug(i18n.translate("permit.revoke_no_lock"))
            return

        mod_address = lock_info.get("from", "")
        rid = lock_info.get("request_id", "")
        self._stop_event_probe()
        self._event_lock = None
        logger.info(i18n.translate("permit.revoked", request_id=rid))

        if mod_address and self.connected:
            await self.send(self.Message(
                Type="error",
                Action="permit",
                To=mod_address,
                RequestID=rid,
                Data={"operate": "revoked"},
            ))

        await self._broadcast_permit_state(None)

    async def _broadcast_permit_state(self, lock_info: dict | None):
        """通过通配符广播 permit 状态变化，并通知本地内嵌窗口。"""
        if self.connected:
            uid = ""
            if isinstance(self.session_info, dict):
                uid = str(self.session_info.get("userId", ""))
            if not uid:
                from .ws.bridge import _resolve_uid
                uid = _resolve_uid()
            if uid:
                await self.send(self.Message(
                    Type="event",
                    Action="permit",
                    To=f"web|client:{uid}:*",
                    Data={"operate": "permit_state", "lock": lock_info},
                ))

        if self.bridge:
            self.bridge.broadcast_to_web(self.Message(
                Type="event",
                Action="permit",
                Data={"operate": "permit_state", "lock": lock_info},
            ))

    def _start_pass_probe(self, pass_rid: str, mod_address: str):
        """启动 pass 阶段探针：每秒 ping mod，不可达时清除该次授权。"""
        probe_rid = f"pass-probe:{pass_rid}"
        self._pass_probe_map[probe_rid] = pass_rid
        self._probe_rids.add(probe_rid)
        self._loop.create_task(self._probe_pass_once(probe_rid, pass_rid, mod_address))

    def _stop_pass_probe(self, pass_rid: str):
        """停止指定 pass 的探针(已激活为事件锁)。"""
        to_remove = None
        for probe_rid, rid in self._pass_probe_map.items():
            if rid == pass_rid:
                to_remove = probe_rid
                break
        if to_remove:
            del self._pass_probe_map[to_remove]
            self._probe_rids.discard(to_remove)

    async def _probe_pass_once(self, probe_rid: str, pass_rid: str, mod_address: str):
        """单次 pass 探针：ping 一次后等待下次调度。"""
        try:
            while pass_rid in self._passes and probe_rid in self._pass_probe_map:
                await asyncio.sleep(1.0)
                if pass_rid not in self._passes or probe_rid not in self._pass_probe_map:
                    break
                if getattr(self, "conn", None) is None:
                    continue
                try:
                    await self.System.ping(To=mod_address, RequestID=probe_rid)
                except Exception as e:
                    logger.debug(i18n.translate("permit.probe_send_failed", error=e))
        except asyncio.CancelledError:
            raise

    def _start_event_probe(self):
        """启动事件锁探针：每秒 ping mod，对端不可达时自动释放锁。"""
        self._stop_event_probe()
        self._probe_task = self._loop.create_task(self._event_probe_loop())

    def _stop_event_probe(self):
        """停止事件锁探针。"""
        if self._probe_task:
            self._probe_task.cancel()
            self._probe_task = None
        self._probe_rids.clear()
        self._pass_probe_map.clear()

    async def _event_probe_loop(self):
        """每秒向 mod 发一条 system/ping，服务端回 error 时由 main() 触发释放。"""
        try:
            while self._event_lock:
                await asyncio.sleep(1.0)
                if not self._event_lock or getattr(self, "conn", None) is None:
                    continue
                mod_address = self._event_lock.get("from", "")
                if not mod_address:
                    continue
                try:
                    rid = f"probe:{self._event_lock.get('request_id', '')}:{id(self._probe_task)}"
                    self._probe_rids.add(rid)
                    await self.System.ping(To=mod_address, RequestID=rid)
                except Exception as e:
                    logger.debug(i18n.translate("permit.probe_send_failed", error=e))
        except asyncio.CancelledError:
            raise

    async def _reply_permit_query(self, msg):
        """回复当前事件锁状态查询。"""
        lock = self._event_lock
        if lock:
            result = {
                "locked": True,
                "from": lock.get("from", ""),
                "description": lock.get("description", ""),
                "request_id": lock.get("request_id", ""),
            }
        else:
            result = {"locked": False}
        resp = self.Message(
            Type="response",
            Action="permit",
            To=msg.From,
            RequestID=msg.RequestID,
            Data=result,
        )
        await self._server_reply.send_json(resp)

    async def revoke_permit(self):
        """Python API：供悬浮球等本地 UI 直接调用，收回 mod 控制权。"""
        await self._do_revoke()

    def query_permit(self) -> dict:
        """Python API：返回当前事件锁状态。"""
        if self._event_lock:
            return {
                "locked": True,
                "from": self._event_lock.get("from", ""),
                "description": self._event_lock.get("description", ""),
                "request_id": self._event_lock.get("request_id", ""),
            }
        return {"locked": False}

    async def _prompt_user(self, from_address: str, description: str) -> bool:
        """根据运行模式选择 UI 或命令行确认。"""
        if getattr(Config, "_ui", False):
            from .ui.request import show_auth_dialog
            return await show_auth_dialog(from_address, description)
        else:
            from .ui.request import prompt_auth_cli
            return await prompt_auth_cli(from_address, description)

    async def _send_auth_response(self, original_msg, status: str, **extra):
        """向请求方发送授权结果 response。"""
        resp_data = {"status": status, "request_id": original_msg.RequestID, **extra}
        resp = self.Message(
            Type="response",
            Action="permit",
            To=original_msg.From,
            RequestID=original_msg.RequestID,
            Data=resp_data,
        )
        await self.send(resp)

    def _stop_stream_by_rid(self, rid) -> bool:
        """服务端流路由失败时，按 rid(=stream_id) 定向停这一条流；返回是否命中并停掉。

        信任闸门：仅当 rid 命中本机已登记的在发流(日志订阅 _subs / 驱动流 _active_streams)
        才停；rid 不认识则忽略。日志流只可能走 server_reply 通道，故限定该通道，绝不误伤
        本地内嵌(_WebReply)的订阅。"""
        if not rid:
            return False
        if self._log_forwarder.unsubscribe_by_stream(self._server_reply, rid):
            logger.debug(i18n.translate("client.stream_stopped",
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
                logger.debug(i18n.translate("client.stream_stopped",
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

            if msg.Action == "permit":
                await self._handle_permit_command(msg)
                return

            # 内置日志流：action=log 不经驱动子进程，直接在主进程订阅 SmtPlay logger
            if msg.Action == LOG_ACTION:
                await self._handle_log_stream(msg, reply, data, operate)
                return

            if msg.Action == "*" and operate == "stop_stream":
                from .drivers import registry as drv_registry
                drv_registry.stop_all_streams()
                self._active_streams.clear()
                self._log_forwarder.clear()
                logger.debug(i18n.translate("client.stream_stopped", action="all", stream_id="all", sender=msg.From or "server"))
                return

            if msg.Action not in drivers:
                resp = self.Message(Type="error", Action=msg.Action, To=msg.From, RequestID=msg.RequestID,
                                       Data=i18n.translate("driver.not_found", driver=msg.Action), )
                await reply.send_json(resp)
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
                    logger.debug(i18n.translate("client.stream_stopped", action=msg.Action, stream_id=stream_id or "all", sender=_skey))
                else:
                    drv_registry.stop_all_streams(msg.Action)
                    self._active_streams.pop(msg.Action, None)
                    logger.debug(i18n.translate("client.stream_stopped", action=msg.Action, stream_id=stream_id or "all", sender="server"))
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
                Type="response",
                Action=LOG_ACTION,
                To=msg.From,
                RequestID=rid,
                Data=result,
            )
            await reply.send_json(resp)
            logger.debug(i18n.translate("client.log_stream_started", to=msg.From or "local", level=result.get("level")))
            return

        if operate == "stop_stream":
            sid = data.get("stream_id") or rid
            self._log_forwarder.unsubscribe(reply, msg.From, sid)
            logger.debug(i18n.translate("client.log_stream_stopped", stream_id=sid, sender=msg.From or "local"))
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
        self._passes.clear()
        self._event_lock = None
        self._pending_auth = False
        self._pending_auth_requests.clear()
        self._pending_permission_dialogs.clear()
        self._resolved_permission_rids.clear()
        self._outgoing_request_rids.clear()
        await super().close()

    async def start_stream(self, to: str, params: dict):
        await self.send(self.Message(
            Type="command",
            Action="screen",
            To=to,
            Data={**params, "operate": "start_stream"},
        ))

    def on_close(self):
        from .drivers import registry as drv_registry

        self._stop_event_probe()
        drv_registry.stop_all_streams()
        self._active_streams.clear()
        self._log_forwarder.unsubscribe_by_reply(self._server_reply)
        logger.debug(i18n.translate("client.all_streams_stopped"))

    def _on_session_info_updated(self):
        """从服务端权威 session 提取 deviceName 更新本地设备名。

        本地 Config.device_name 不完全可信(可能与服务端不同步)，一律以服务端
        session/status 返回的 device.deviceName 为准；更新后 bridge 等模块的地址
        解析自动使用权威设备名。"""
        info = self.session_info or {}
        device = info.get("device") if isinstance(info, dict) else None
        if not isinstance(device, dict):
            return
        name = device.get("deviceName")
        if name and name != self.device_name:
            old = self.device_name
            self.device_name = name
            logger.debug(i18n.translate("client.device_name_updated", old=old, new=name))

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
        """产出所有活动流的保活目标 (reply, to, stream_id)：日志订阅 + 驱动流。"""
        yield from self._log_forwarder.keepalive_targets()
        for action, streams in list(self._active_streams.items()):
            for frm, sids in list(streams.items()):
                for stream_id, reply in list(sids.items()):
                    yield reply, frm, stream_id

    def _stop_stream_by_target(self, reply, to, stream_id):
        """已知通道时精确停一条流(本地网页断开 / 本机↔服务端断线触发)：先试日志订阅，再试驱动流。"""
        if self._log_forwarder.unsubscribe_by_stream(reply, stream_id):
            logger.debug(i18n.translate("client.stream_stopped",
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
                logger.debug(i18n.translate("client.stream_stopped",
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
