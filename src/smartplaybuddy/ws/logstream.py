"""
实时日志转发 + 全局分级日志缓冲。

两部分职责：
  1) 全局分级环形缓冲(_tails)：为每个级别(TRACE..CRITICAL)各保留最近 N 条「>= 该级别」
     的记录。纯缓冲、不依赖事件循环。订阅时按请求级别直接取对应档回放，避免低频级别(INFO)
     的历史被高频 DEBUG/TRACE 噪声稀释。
  2) 实时转发(_subs)：每个订阅者独立有界队列 + 泵协程；emit 在任意业务线程用
     call_soon_threadsafe 入队(队满丢最旧)，泵在事件循环里 send_json，发送失败自愈退订。

以 logging.Handler 形式挂到 "SmtPlay" 根 logger，action 固定为保留字 "log"，
复用现有 start_stream / stream / stop_stream 协议，不经驱动子进程。
进程内全局单例：跨 Client(登录/重连)生命周期保留缓冲历史。
"""
import asyncio
import logging
from collections import deque
from contextlib import contextmanager

from .. import i18n
from .. import logger as _log
from .message.message import Message

logger = _log.logger.getChild("LogStream")

#: 保留 action 名。控制台用 command/action=log/operate=start_stream 订阅日志。
LOG_ACTION = "log"

#: 每个订阅者待发队列上限；超出丢弃最旧记录(实时性优先于完整性)。
_QUEUE_MAX = 512
#: 每个级别档缓冲保留的最近记录数。
_TAIL_SIZE = 100
#: 参与分级的级别(升序)。含自定义 TRACE：其档为「全部记录的最近 N 条」(逐帧深挖用)，
#: 由高频流帧主导；DEBUG+ 档不含 TRACE，故订阅 DEBUG 及以上仍然是干净的。
_LEVELS = (_log.TRACE, logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)


def _resolve_level(level) -> int:
    """把级别名解析为数值；额外识别自定义 TRACE(logging 模块无此属性)。"""
    key = str(level).upper()
    if key == "TRACE":
        return _log.TRACE
    return getattr(logging, key, logging.INFO)


class _Subscriber:
    __slots__ = ("key", "stream_id", "reply", "to", "request_id", "min_level", "name_prefix", "queue", "task")

    def __init__(self, key, stream_id, reply, to, request_id, min_level, name_prefix):
        self.key = key
        self.stream_id = stream_id
        self.reply = reply
        self.to = to
        self.request_id = request_id
        self.min_level = min_level
        self.name_prefix = name_prefix
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self.task: asyncio.Task | None = None

    def matches(self, record: logging.LogRecord) -> bool:
        if record.levelno < self.min_level:
            return False
        if self.name_prefix and not record.name.startswith(self.name_prefix):
            return False
        return True


class LogForwarder(logging.Handler):
    """挂到 SmtPlay 根 logger：分级缓冲 + 实时转发给已订阅的控制端连接。"""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None, tail_size: int = _TAIL_SIZE):
        super().__init__(level=_log.TRACE)   # 捕获含 TRACE 在内的全部级别，供分级缓冲与实时过滤
        self._loop = loop
        self._subs: dict[str, _Subscriber] = {}
        self._tails: dict[int, deque] = {lv: deque(maxlen=tail_size) for lv in _LEVELS}
        self._root = _log.logger                # "SmtPlay" logger
        self._self_prefix = logger.name         # "SmtPlay.LogStream"，跳过自身日志避免反馈放大
        self._suppress = False                  # 挂起实时转发(仍写缓冲)，用于切断 error→日志→帧 自激环

    @contextmanager
    def suppressed(self):
        """临时挂起实时转发(分级缓冲照常写)：记录"路由失败"这类会被订阅者回灌、
        进而自激成 error→日志→帧→error 死循环的日志时使用。"""
        self._suppress = True
        try:
            yield
        finally:
            self._suppress = False

    # ---------- 安装 / loop 绑定 ----------

    def install(self):
        if self not in self._root.handlers:
            self._root.addHandler(self)

    def bind_loop(self, loop):
        self._loop = loop

    def unbind_loop(self, loop):
        if self._loop is loop:
            self._loop = None

    # ---------- 订阅管理(事件循环线程内调用) ----------

    def subscribe(self, stream_id, reply, to, request_id, level="INFO", name=None, tail=0) -> dict:
        min_level = _resolve_level(level)
        # 复合键(reply, to, stream_id)：两条入口通道 + 各自发起者天然隔离，
        # 即使不同网页的 requestId 撞车(SDK 雪花 worker/dc 恒为 0，同毫秒即同 ID)也不会互相顶掉。
        key = (reply, to, stream_id)
        sub = _Subscriber(key, stream_id, reply, to, request_id, min_level, name)
        self._subs[key] = sub
        if self._loop is not None:
            sub.task = self._loop.create_task(self._pump(sub))

        replay = self._replay(min_level, name, tail)
        logger.info(i18n.translate("logstream.subscribed",
                                   stream_id=stream_id, level=logging.getLevelName(min_level), name=name or "-"))
        return {
            "stream_id": stream_id,
            "level": logging.getLevelName(min_level),
            "name": name,
            "replayed": len(replay),
            "replay": replay,
        }

    def unsubscribe(self, reply, to, stream_id):
        key = (reply, to, stream_id)
        sub = self._subs.pop(key, None)
        if not sub:
            return
        if sub.task and not sub.task.done():
            sub.task.cancel()
        logger.info(i18n.translate("logstream.unsubscribed", stream_id=stream_id))

    def unsubscribe_by_reply(self, reply):
        for key in [k for k in self._subs if k[0] is reply]:
            self.unsubscribe(*key)

    def unsubscribe_by_stream(self, reply, stream_id) -> bool:
        """停掉指定通道下某 stream_id 的订阅；返回是否命中。
        命中=本机确实在发这条流(信任闸门)；未命中则调用方应忽略该 rid。"""
        keys = [k for k in self._subs if k[0] is reply and k[2] == stream_id]
        for key in keys:
            self.unsubscribe(*key)
        return bool(keys)

    def keepalive_targets(self):
        """产出所有在发日志订阅的保活目标 (reply, to, stream_id)，供 Client 集中保活循环枚举。
        本地订阅 reply=_WebReply(帧经桥直连网页、不经服务端)；远程订阅 to=对端地址、reply=_ServerReply。"""
        for sub in list(self._subs.values()):
            yield sub.reply, sub.to, sub.stream_id

    def clear(self):
        for key in list(self._subs.keys()):
            self.unsubscribe(*key)

    # ---------- 回放：按请求级别选对应档缓冲 ----------

    def _replay(self, min_level: int, name: str | None, tail: int) -> list[dict]:
        if tail <= 0:
            return []
        # 取「阈值不超过 min_level 的最高档」缓冲：其内容恰是 >= 该阈值的最近记录，
        # 因此不会被更低级别的噪声稀释。
        candidates = [lv for lv in _LEVELS if lv <= min_level]
        buf = self._tails[max(candidates)] if candidates else self._tails[_LEVELS[0]]
        items = [
            it for it in buf
            if it["levelno"] >= min_level and (not name or it["name"].startswith(name))
        ]
        return [{k: v for k, v in it.items() if k != "levelno"} for it in items[-tail:]]

    # ---------- logging.Handler ----------

    def emit(self, record: logging.LogRecord):
        if record.name.startswith(self._self_prefix):
            return
        try:
            item = self._format(record)
        except Exception:
            return

        # 分级缓冲：写入所有阈值 <= 本记录级别的档(存引用，不重复格式化)
        for lv in _LEVELS:
            if record.levelno >= lv:
                self._tails[lv].append(item)

        # 实时转发
        if self._suppress:
            return
        loop = self._loop
        if loop is None or not self._subs:
            return
        for sub in list(self._subs.values()):
            if sub.matches(record):
                loop.call_soon_threadsafe(self._enqueue, sub, item)

    @staticmethod
    def _enqueue(sub: _Subscriber, item: dict):
        try:
            sub.queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                sub.queue.get_nowait()      # 丢最旧
                sub.queue.put_nowait(item)
            except Exception:
                pass

    @staticmethod
    def _format(record: logging.LogRecord) -> dict:
        return {
            "levelno": record.levelno,
            "level": record.levelname,
            "name": record.name,
            "time": int(record.created * 1000),
            "message": record.getMessage(),
        }

    async def _pump(self, sub: _Subscriber):
        try:
            while True:
                item = await sub.queue.get()
                payload = {k: v for k, v in item.items() if k != "levelno"}
                payload["stream_id"] = sub.stream_id
                msg = Message(
                    Type="stream",
                    Action=LOG_ACTION,
                    To=sub.to,
                    RequestID=sub.request_id,
                    Data=payload,
                )
                try:
                    await sub.reply.send_json(msg.to_json())
                except Exception as e:
                    logger.debug(i18n.translate("logstream.send_failed", stream_id=sub.stream_id, error=e))
                    break                   # 连接已死：退出泵，finally 自愈退订
        except asyncio.CancelledError:
            raise
        finally:
            if self._subs.get(sub.key) is sub:
                self._subs.pop(sub.key, None)


# ---------- 进程内全局单例 ----------
_forwarder: LogForwarder | None = None


def get_log_forwarder(loop: asyncio.AbstractEventLoop | None = None) -> LogForwarder:
    """全局单例：首次调用安装 handler(开始分级缓冲)，其后仅绑定/更新事件循环。
    跨 Client 生命周期保留缓冲历史。"""
    global _forwarder
    if _forwarder is None:
        _forwarder = LogForwarder(loop)
        _forwarder.install()
    elif loop is not None:
        _forwarder.bind_loop(loop)
    return _forwarder