"""
授权校验与权限管理 Mixin。

从 Client 中提取的完整授权子系统：
  - 统一授权校验 (_check_auth)
  - 跨 UID 授权请求/审批/广播
  - 事件锁 + 探针 (pass probe / event probe)
  - permit 命令处理 (revoke/query)
  - 公共 API (revoke_permit / query_permit)

通过 Mixin 注入 Client，方法可直接访问 self 上的属性。
"""
import asyncio

from ..utils import i18n
from ..utils import logger
from ..utils import translate
from ..config import Config
from .message.message import generate_rid

logger = logger.getChild("Permit")


class PermitMixin:
    """授权管理 Mixin。子类需提供以下属性：

    状态属性：
        _passes, _event_lock, _pending_auth, _pending_auth_requests,
        _pending_permission_dialogs, _resolved_permission_rids,
        _outgoing_request_rids, _probe_rids, _pass_probe_map, _probe_task

    接口属性（由 Connector / Client 提供）：
        session_info, device_name, bridge, _loop,
        send(), Error, Message, System, connected
    """

    @staticmethod
    def _extract_uid(address: str | None) -> str:
        if not address:
            return ""
        parts = address.split(":")
        return parts[1] if len(parts) >= 2 else ""

    async def _check_auth(self, msg) -> bool:
        sender_uid = self._extract_uid(msg.From)
        local_uid = str(self.session_info.get("userId", "")) if isinstance(self.session_info, dict) else ""
        if not local_uid:
            from .bridge import _resolve_uid
            local_uid = _resolve_uid()

        if not sender_uid:
            return True

        rid = msg.RequestID

        if rid:
            if self._event_lock and self._event_lock.get("request_id") == rid:
                return True

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

        if sender_uid == local_uid:
            return True

        if not rid:
            return False

        if msg.Type == "request":
            self._outgoing_request_rids.add(rid)
            return True

        if msg.Type == "response":
            if rid in self._outgoing_request_rids:
                self._outgoing_request_rids.discard(rid)
                return True
            return False

        if msg.Type == "event" and msg.Action == "permit":
            logger.debug(i18n.translate("permit.activate_blocked_by_auth",
                                        rid=rid, sender_uid=sender_uid, local_uid=local_uid))
        return False

    def _self_address_prefix(self) -> str:
        uid = ""
        if isinstance(self.session_info, dict):
            uid = str(self.session_info.get("userId", ""))
        if not uid:
            from .bridge import _resolve_uid
            uid = _resolve_uid()
        if not uid:
            return ""
        return f"client:{uid}:{self.device_name}"

    async def _handle_auth_request(self, msg):
        data = msg.Data if isinstance(msg.Data, dict) else {}
        operate = data.get("operate")

        if operate != "request-permit":
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
        data = msg.Data if isinstance(msg.Data, dict) else {}
        from_addr = msg.From
        self_prefix = self._self_address_prefix()

        if not (self_prefix and from_addr == self_prefix):
            return

        rid1 = data.get("permit_rid") or msg.RequestID
        if not rid1:
            return

        if rid1 not in self._pending_auth_requests:
            return

        rid2 = self._rid1_to_rid2.get(rid1)
        if not rid2:
            return

        if rid2 in self._resolved_permission_rids:
            return

        description = data.get("description", "")
        requester = data.get("requester", "")

        future = asyncio.get_event_loop().create_future()
        self._pending_permission_dialogs[rid2] = {"future": future, "dialog": None}

        if getattr(Config, "_ui", False):
            asyncio.ensure_future(self._show_permission_dialog(requester or from_addr, description, future, rid2))
        else:
            from ..ui.request import prompt_auth_cli
            asyncio.ensure_future(
                self._resolve_dialog_future(prompt_auth_cli(requester or from_addr, description), future)
            )

        approved = await future

        self._pending_permission_dialogs.pop(rid2, None)

        if rid2 in self._resolved_permission_rids:
            return

        info = self._pending_auth_requests.pop(rid1, None)
        self._rid1_to_rid2.pop(rid1, None)
        if not info:
            return
        self._resolved_permission_rids.add(rid2)
        self._pending_auth = False

        if approved:
            mod_address = info["from"]
            self._passes[rid1] = {
                "from": mod_address,
                "description": info.get("description", ""),
            }
            resp = self.Message(
                Type="response", Action="permit",
                To=mod_address, RequestID=rid1,
                Data={"status": "approved", "request_id": rid1},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.request_approved", request_id=rid1))
            self._start_pass_probe(rid1, mod_address)
            await self._broadcast_permit_state({
                "from": mod_address,
                "description": info.get("description", ""),
                "request_id": rid1,
            })
            await self._broadcast_permission_resolved(rid2, "approved")
        else:
            resp = self.Message(
                Type="response", Action="permit",
                To=info["from"], RequestID=rid1,
                Data={"status": "rejected", "request_id": rid1},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.request_rejected", request_id=rid1))
            await self._broadcast_permission_resolved(rid2, "rejected")

    async def _show_permission_dialog(self, requester, description, future, rid):
        from ..ui.request import AuthRequestDialog
        from ..ui import window as _main_window

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
    async def _resolve_dialog_future(awaitable, future):
        try:
            result = await awaitable
            if not future.done():
                future.set_result(result)
        except Exception:
            if not future.done():
                future.set_result(False)

    async def _handle_permission_approved(self, msg):
        data = msg.Data if isinstance(msg.Data, dict) else {}

        if data.get("source") == "self":
            return

        rid2 = msg.RequestID
        if not rid2:
            return

        if rid2 in self._resolved_permission_rids:
            return

        rid1 = data.get("permit_rid")
        if not rid1:
            return

        status = data.get("status", "unknown")
        resolved_by = data.get("resolved_by", msg.From or "unknown")

        dialog_info = self._pending_permission_dialogs.pop(rid2, None)
        if dialog_info:
            fut = dialog_info.get("future")
            if fut and not fut.done():
                fut.set_result(status == "approved")
            dlg = dialog_info.get("dialog")
            if dlg:
                dlg.close()

        self._resolved_permission_rids.add(rid2)

        info = self._pending_auth_requests.pop(rid1, None)
        self._rid1_to_rid2.pop(rid1, None)
        if not info:
            logger.info(i18n.translate("permit.broadcast_resolved",
                                       request_id=rid1, resolved_by=resolved_by, status=status))
            return

        self._pending_auth = False
        mod_address = info["from"]
        description = info.get("description", "")

        if status == "approved":
            self._passes[rid1] = {"from": mod_address, "description": description}
            resp = self.Message(
                Type="response", Action="permit",
                To=mod_address, RequestID=rid1,
                Data={"status": "approved", "request_id": rid1},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.remote_approved", request_id=rid1, from_addr=resolved_by))
            self._start_pass_probe(rid1, mod_address)
            await self._broadcast_permit_state({
                "from": mod_address, "description": description, "request_id": rid1,
            })
        else:
            resp = self.Message(
                Type="response", Action="permit",
                To=mod_address, RequestID=rid1,
                Data={"status": "rejected", "request_id": rid1},
            )
            await self.send(resp)
            logger.info(i18n.translate("permit.remote_rejected", request_id=rid1, from_addr=resolved_by))

        await self._broadcast_permission_resolved(rid2, status)

    async def _broadcast_permission_resolved(self, rid2, status):
        uid = ""
        if isinstance(self.session_info, dict):
            uid = str(self.session_info.get("userId", ""))
        if not uid:
            from .bridge import _resolve_uid
            uid = _resolve_uid()
        if not uid:
            return
        new_rid = generate_rid()
        self._outgoing_request_rids.add(new_rid)
        broadcast_msg = self.Message(
            Type="response", Action="permission",
            To=f"client|web:{uid}:*", RequestID=new_rid,
            Data={"status": status, "resolved_by": self._self_address_prefix(),
                  "permit_rid": rid2, "source": "self"},
        )
        await self.send(broadcast_msg)
        if self.bridge:
            self.bridge.broadcast_to_web(broadcast_msg)

    async def _broadcast_permission_request(self, rid1, mod_address, description):
        uid = ""
        if isinstance(self.session_info, dict):
            uid = str(self.session_info.get("userId", ""))
        if not uid:
            from .bridge import _resolve_uid
            uid = _resolve_uid()
        if not uid:
            return
        rid2 = generate_rid()
        self._rid1_to_rid2[rid1] = rid2
        self._outgoing_request_rids.add(rid2)
        broadcast_msg = self.Message(
            Type="request", Action="permission",
            To=f"client|web:{uid}:*", RequestID=rid2,
            Data={"requester": mod_address, "description": description,
                  "permit_rid": rid1, "source": "self"},
        )
        await self.send(broadcast_msg)
        if self.bridge:
            self.bridge.broadcast_to_web(broadcast_msg)

    async def _handle_event(self, msg):
        data = msg.Data if isinstance(msg.Data, dict) else {}
        operate = data.get("operate")

        if operate == "release":
            if self._event_lock and self._event_lock.get("request_id") == msg.RequestID:
                self._stop_event_probe()
                self._event_lock = None
                logger.info(i18n.translate("permit.event_released", request_id=msg.RequestID))
                await self._broadcast_permit_state(None)
            return

        if operate == "permit-state":
            if self.bridge:
                self.bridge.broadcast_to_web(msg)
            return

    async def _handle_permit_command(self, msg):
        data = msg.Data if isinstance(msg.Data, dict) else {}
        operate = data.get("operate")

        if operate == "revoke":
            await self._do_revoke()
        else:
            await self.Error.error(
                i18n.translate("permit.unsupported_operate", operate=operate),
                To=msg.From, RequestID=msg.RequestID,
            )

    async def _do_revoke(self):
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
                Type="error", Action="permit",
                To=mod_address, RequestID=rid,
                Data={"operate": "revoked"},
            ))

        await self._broadcast_permit_state(None)

    async def _broadcast_permit_state(self, lock_info):
        if self.connected:
            uid = ""
            if isinstance(self.session_info, dict):
                uid = str(self.session_info.get("userId", ""))
            if not uid:
                from .bridge import _resolve_uid
                uid = _resolve_uid()
            if uid:
                await self.send(self.Message(
                    Type="event", Action="permit",
                    To=f"web|client:{uid}:*",
                    Data={"operate": "permit-state", "lock": lock_info},
                ))

        if self.bridge:
            self.bridge.broadcast_to_web(self.Message(
                Type="event", Action="permit",
                Data={"operate": "permit-state", "lock": lock_info},
            ))

    def _start_pass_probe(self, pass_rid, mod_address):
        probe_rid = f"pass-probe:{pass_rid}"
        self._pass_probe_map[probe_rid] = pass_rid
        self._probe_rids.add(probe_rid)
        self._loop.create_task(self._probe_pass_once(probe_rid, pass_rid, mod_address))

    def _stop_pass_probe(self, pass_rid):
        for probe_rid, rid in self._pass_probe_map.items():
            if rid == pass_rid:
                del self._pass_probe_map[probe_rid]
                self._probe_rids.discard(probe_rid)
                break

    async def _probe_pass_once(self, probe_rid, pass_rid, mod_address):
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
        self._stop_event_probe()
        self._probe_task = self._loop.create_task(self._event_probe_loop())

    def _stop_event_probe(self):
        if self._probe_task:
            self._probe_task.cancel()
            self._probe_task = None
        self._probe_rids.clear()
        self._pass_probe_map.clear()

    async def _event_probe_loop(self):
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

    async def _reply_permit_query(self, msg, reply):
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
            Type="response", Action="permit",
            To=msg.From, RequestID=msg.RequestID, Data=result,
        )
        await reply.send_json(resp.to_json())

    async def revoke_permit(self):
        await self._do_revoke()

    def query_permit(self) -> dict:
        if self._event_lock:
            return {
                "locked": True,
                "from": self._event_lock.get("from", ""),
                "description": self._event_lock.get("description", ""),
                "request_id": self._event_lock.get("request_id", ""),
            }
        return {"locked": False}

    async def _send_auth_response(self, original_msg, status, **extra):
        resp_data = {"status": status, "request_id": original_msg.RequestID, **extra}
        resp = self.Message(
            Type="response", Action="permit",
            To=original_msg.From, RequestID=original_msg.RequestID,
            Data=resp_data,
        )
        await self.send(resp)
