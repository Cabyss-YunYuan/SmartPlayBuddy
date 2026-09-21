"""
流控基础设施：有界帧队列 + 字节速率限制 + 带宽自适应。

- _ByteLimiter: 滑动窗口字节速率限制器(用于单流带宽上限)
- _BandwidthController: 全局上行带宽控制器(AIMD 自适应，仅服务端链路)
- _StreamSender: 单条流的有界帧队列 + 泵协程 + 多级限流
"""
import asyncio
import time
from collections import deque
from typing import Any

from ..utils import translate, logger

logger = logger.getChild("Stream")


class _ByteLimiter:
    """滑动窗口字节速率限制器。用于单流带宽上限。"""

    __slots__ = ("_entries", "_max_bps")

    def __init__(self, max_bytes_per_sec: float):
        self._entries: deque = deque()
        self._max_bps = max_bytes_per_sec

    @property
    def max_bps(self) -> float:
        return self._max_bps

    @max_bps.setter
    def max_bps(self, value: float):
        self._max_bps = max(1.0, value)

    def try_acquire(self, n_bytes: int) -> bool:
        now = time.monotonic()
        cutoff = now - 1.0
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()
        current = sum(b for _, b in self._entries)
        if current + n_bytes > self._max_bps:
            return False
        self._entries.append((now, n_bytes))
        return True


class BandwidthController:
    """全局上行带宽控制器：滑动窗口监测 + AIMD 自适应估计。

    仅作用于服务端链路(公网)，本地桥不受限。
    - 启动时由测速结果设定初始估计值(set_estimate)
    - 无丢帧时每秒 +5% 渐进逼近实际带宽
    - 有丢帧时 -20% 快速回退
    - 超 80% 使用率时 log warning(节流，每 5s 最多一条)
    - 超 100% 时丢帧 + log error(节流)
    """

    _LOG_INTERVAL = 5.0

    def __init__(self):
        self._estimate = 0.0
        self._window: deque = deque()
        self._last_adjust = time.monotonic()
        self._had_drop = False
        self._last_warn_at = 0.0
        self._last_error_at = 0.0

    @property
    def estimate(self) -> float:
        return self._estimate

    def set_estimate(self, value: float):
        self._estimate = max(100_000, value)
        logger.debug(translate("client.bw_estimate_set",
                                    bps=f"{self._estimate / 125:.0f} KB/s"))

    def can_send(self, n_bytes: int) -> bool:
        if self._estimate <= 0:
            self._window.append((time.monotonic(), n_bytes))
            return True

        now = time.monotonic()
        cutoff = now - 1.0
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        current_bps = sum(b for _, b in self._window)

        if now - self._last_adjust >= 1.0:
            if self._had_drop:
                self._estimate *= 0.8
                self._had_drop = False
            else:
                self._estimate *= 1.05
            self._last_adjust = now

        usage = (current_bps + n_bytes) / self._estimate if self._estimate > 0 else 1.0

        if usage > 1.0:
            self._had_drop = True
            if now - self._last_error_at >= self._LOG_INTERVAL:
                logger.error(translate("client.bw_limit_exceeded",
                                            estimate=f"{self._estimate / 125:.0f}",
                                            current=f"{(current_bps + n_bytes) / 125:.0f}"))
                self._last_error_at = now
            return False

        if usage > 0.8:
            if now - self._last_warn_at >= self._LOG_INTERVAL:
                logger.warning(translate("client.bw_near_limit",
                                              estimate=f"{self._estimate / 125:.0f}",
                                              usage=f"{usage * 100:.0f}"))
                self._last_warn_at = now

        self._window.append((now, n_bytes))
        return True

    def reset(self):
        self._estimate = 0.0
        self._window.clear()
        self._last_adjust = time.monotonic()
        self._had_drop = False
        self._last_warn_at = 0.0
        self._last_error_at = 0.0


class StreamSender:
    """单条流的有界帧队列 + 泵协程。

    驱动子进程线程通过 call_soon_threadsafe 入队；泵在事件循环里逐帧发送。
    入队前依次检查：全局字节速率 → 单流字节速率 → 单流帧率间隔 → 有界队列。
    任一层不通过则丢弃帧——实时流场景下最新帧永远比旧帧更有价值。
    """

    __slots__ = ("_queue", "_max", "_pump", "_loop", "_reply", "_original_msg",
                 "_bw_ctrl", "_send_interval", "_last_ts", "_stream_limiter",
                 "_message_cls", "_drop_counts", "_last_drop_log_at", "_stopped")

    def __init__(self, max_size: int, loop, reply, original_msg,
                 message_cls,
                 bandwidth_controller: "BandwidthController | None" = None,
                 send_fps: int = 0,
                 send_bw: float = 0):
        self._queue: deque = deque()
        self._max = max_size
        self._loop = loop
        self._reply = reply
        self._original_msg = original_msg
        self._bw_ctrl = bandwidth_controller
        self._send_interval = 1.0 / send_fps if send_fps > 0 else 0.0
        self._last_ts = 0.0
        self._stream_limiter = _ByteLimiter(send_bw) if send_bw > 0 else None
        self._message_cls = message_cls
        self._drop_counts: dict[str, int] = {}
        self._last_drop_log_at = 0.0
        self._stopped = False
        self._pump = loop.create_task(self._pump_loop())

    def enqueue(self, frame_msg: dict):
        """线程安全入队；由驱动子进程的 on_frame 回调调用。"""
        self._loop.call_soon_threadsafe(self._enqueue_sync, frame_msg)

    def _enqueue_sync(self, frame_msg: dict):
        binary_data = frame_msg.get("BinaryData")
        n_bytes = len(binary_data) if binary_data else 0

        if self._bw_ctrl is not None and not self._bw_ctrl.can_send(n_bytes):
            self._record_drop("bw_global")
            return

        if self._stream_limiter is not None and not self._stream_limiter.try_acquire(n_bytes):
            self._record_drop("bw_stream")
            return

        if self._send_interval > 0:
            ts = frame_msg.get("_ts", 0.0)
            if ts > 0 and ts - self._last_ts < self._send_interval:
                self._record_drop("fps")
                return
            self._last_ts = ts

        if len(self._queue) >= self._max:
            self._queue.popleft()
            self._record_drop("queue_full")
        self._queue.append(frame_msg)

    def _record_drop(self, reason: str):
        self._drop_counts[reason] = self._drop_counts.get(reason, 0) + 1
        now = time.monotonic()
        if now - self._last_drop_log_at >= 5.0:
            self._last_drop_log_at = now
            parts = [f"{k}={v}" for k, v in self._drop_counts.items()]
            logger.debug(translate("client.stream_frames_dropped",
                                        drops=", ".join(parts)))

    def get_stats(self) -> dict:
        """返回流的运行时统计快照（供 query 使用）。"""
        stats: dict = {
            "drops": dict(self._drop_counts),
            "queue": {"size": self._max, "current": len(self._queue)},
        }
        if self._stream_limiter is not None:
            stats["bandwidth"] = {"stream_limit_bps": self._stream_limiter.max_bps}
        if self._bw_ctrl is not None:
            stats.setdefault("bandwidth", {})["global_estimate_bps"] = self._bw_ctrl.estimate
        return stats

    async def _pump_loop(self):
        try:
            while True:
                while not self._queue:
                    if self._stopped:
                        return
                    await asyncio.sleep(0.005)
                frame_msg = self._queue.popleft()
                if self._stopped:
                    return
                await self._reply.send_pair(
                    self._message_cls(
                        Type="stream",
                        Action=self._original_msg.Action,
                        To=self._original_msg.From,
                        RequestID=frame_msg.get("RequestID") or self._original_msg.RequestID,
                        Data={**(frame_msg.get("Data") or {}), "__binary__": True},
                        Binary=True,
                    ),
                    frame_msg["BinaryData"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(translate("client.stream_forward_error", error=e), exc_info=True)

    def stop(self):
        self._stopped = True
        self._queue.clear()
        if self._pump and not self._pump.done():
            self._pump.cancel()

    def set_stream_bw(self, max_bytes_per_sec: float):
        """动态设定单流带宽上限(测速结果回填)。"""
        if self._stream_limiter is not None:
            self._stream_limiter.max_bps = max_bytes_per_sec
        else:
            self._stream_limiter = _ByteLimiter(max_bytes_per_sec)
