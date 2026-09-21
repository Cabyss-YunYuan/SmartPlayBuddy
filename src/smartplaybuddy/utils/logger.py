"""
日志模块。

两部分：
  1) 根 logger 配置：控制台 + 按天轮转文件 + 可选诊断档；自定义 TRACE 级别。
  2) LogForwarder：实时日志转发 + 全局分级缓冲(logging.Handler)。
"""
import asyncio
import logging
from collections import deque
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
import os
import importlib.util

# ─────────────────────── Part 1: Logger 配置 ───────────────────────

TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(self, message, *args, **kwargs):
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


logging.Logger.trace = _trace
logging.Logger.TRACE = TRACE

log_format = logging.Formatter("%(levelname)-8s|\t%(asctime)s\t%(name)-30s\t%(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)

package = "smartplaybuddy"
logdir = "logs"

name = "Smtplay"
level = logging.INFO

root_path = os.path.dirname(importlib.util.find_spec(package).submodule_search_locations[0])
if os.path.basename(root_path) == "src":
    root_path = os.path.dirname(root_path)

log_path = os.path.join(root_path, logdir)
if not os.path.exists(log_path):
    os.makedirs(log_path)

logger = logging.getLogger(name)
logger.setLevel(TRACE)
console_handler.setLevel(level)
logger.addHandler(console_handler)

file_handler = TimedRotatingFileHandler(f"{log_path}/{name}.log", encoding="utf-8", when="D", interval=1, backupCount=30)
file_handler.setFormatter(log_format)
file_handler.setLevel(logging.INFO)
logger.addHandler(file_handler)

if level < logging.INFO:
    from datetime import datetime
    _level_name = logging.getLevelName(level)
    _timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _diag_path = f"{log_path}/{name}.{_level_name}.{_timestamp}.log"

    diag_handler = logging.FileHandler(_diag_path, encoding="utf-8")
    diag_handler.setFormatter(log_format)
    diag_handler.setLevel(level)
    logger.addHandler(diag_handler)


# ─────────────────────── Part 2: LogForwarder ───────────────────────

_log_forwarder_logger = logger.getChild("LogStream")


def _t(key, **kwargs):
    from .i18n import translate
    return translate(key, **kwargs)


LOG_ACTION = "log"

_QUEUE_MAX = 512
_TAIL_SIZE = 100
_LEVELS = (TRACE, logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)


def _resolve_level(level) -> int:
    key = str(level).upper()
    if key == "TRACE":
        return TRACE
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
        super().__init__(level=TRACE)
        self._loop = loop
        self._subs: dict[str, _Subscriber] = {}
        self._tails: dict[int, deque] = {lv: deque(maxlen=tail_size) for lv in _LEVELS}
        self._root = logger
        self._suppress = False

    @contextmanager
    def suppressed(self):
        self._suppress = True
        try:
            yield
        finally:
            self._suppress = False

    def install(self):
        if self not in self._root.handlers:
            self._root.addHandler(self)

    def bind_loop(self, loop):
        self._loop = loop

    def unbind_loop(self, loop):
        if self._loop is loop:
            self._loop = None

    def subscribe(self, stream_id, reply, to, request_id, level="INFO", name=None, tail=0) -> dict:
        min_level = _resolve_level(level)
        key = (reply, to, stream_id)
        sub = _Subscriber(key, stream_id, reply, to, request_id, min_level, name)
        self._subs[key] = sub
        if self._loop is not None:
            sub.task = self._loop.create_task(self._pump(sub))

        replay = self._replay(min_level, name, tail)
        _log_forwarder_logger.debug(_t("logstream.subscribed",
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
        _log_forwarder_logger.debug(_t("logstream.unsubscribed", stream_id=stream_id))

    def unsubscribe_by_reply(self, reply):
        for key in [k for k in self._subs if k[0] is reply]:
            self.unsubscribe(*key)

    def unsubscribe_by_stream(self, reply, stream_id) -> bool:
        keys = [k for k in self._subs if k[0] is reply and k[2] == stream_id]
        for key in keys:
            self.unsubscribe(*key)
        return bool(keys)

    def keepalive_targets(self):
        for sub in list(self._subs.values()):
            yield sub.reply, sub.to, sub.stream_id

    def get_subscriptions(self) -> list[dict]:
        """返回所有日志订阅的摘要（供 query 使用）。"""
        return [
            {
                "stream_id": sub.stream_id,
                "target": sub.to,
                "level": logging.getLevelName(sub.min_level),
                "name": sub.name_prefix,
            }
            for sub in self._subs.values()
        ]

    def clear(self):
        for key in list(self._subs.keys()):
            self.unsubscribe(*key)

    def _replay(self, min_level: int, name: str | None, tail: int) -> list[dict]:
        if tail <= 0:
            return []
        candidates = [lv for lv in _LEVELS if lv <= min_level]
        buf = self._tails[max(candidates)] if candidates else self._tails[_LEVELS[0]]
        items = [
            it for it in buf
            if it["levelno"] >= min_level and (not name or it["name"].startswith(name))
        ]
        return [{k: v for k, v in it.items() if k != "levelno"} for it in items[-tail:]]

    def emit(self, record: logging.LogRecord):
        try:
            item = self._format(record)
        except Exception:
            return

        for lv in _LEVELS:
            if record.levelno >= lv:
                self._tails[lv].append(item)

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
                sub.queue.get_nowait()
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
            from ..ws.message.message import Message

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
                    _log_forwarder_logger.debug(_t("logstream.send_failed", stream_id=sub.stream_id, error=e))
                    break
        except asyncio.CancelledError:
            raise
        finally:
            if self._subs.get(sub.key) is sub:
                self._subs.pop(sub.key, None)


_forwarder: LogForwarder | None = None


def get_log_forwarder(loop: asyncio.AbstractEventLoop | None = None) -> LogForwarder:
    global _forwarder
    if _forwarder is None:
        _forwarder = LogForwarder(loop)
        _forwarder.install()
    elif loop is not None:
        _forwarder.bind_loop(loop)
    return _forwarder
