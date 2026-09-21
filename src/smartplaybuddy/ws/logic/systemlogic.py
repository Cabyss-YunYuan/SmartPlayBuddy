import asyncio
import time
from ...utils import i18n
from ...utils import logger
from ...utils import translate
from .. import message

logger = logger.getChild("System")


def system(self, msg):
    if msg.Action == "pong":
        if msg.Data is None:
            return
        latency = int(time.time() * 1000) - (msg.Data['time'])
        logger.trace(i18n.translate("system.ping", device=msg.From if msg.From else "server", latency=f"{latency}ms"))
    elif msg.Action == "ping":
        asyncio.create_task(
            self.send(message.system.pong(To=msg.From, RequestID=msg.RequestID, Data=msg.Data))
        )
    elif msg.Action == "speed-test-result":
        _resolve_speed_test(self, msg)
    elif msg.Action == "speed-test":
        asyncio.create_task(_handle_speed_test_request(self, msg))
    elif msg.Action == "speed-test-return":
        _resolve_speed_test_return(self, msg)


def _resolve_speed_test(self, msg):
    """测速 result 到达：暂存数据与到达时刻，等 return 到达后一并完成本轮。"""
    rid = msg.RequestID
    futures = getattr(self, "_speed_test_futures", None)
    if not futures or rid not in futures:
        return
    results = getattr(self, "_speed_test_results", None)
    if results is None:
        results = {}
        setattr(self, "_speed_test_results", results)
    results[rid] = {"result": msg.Data, "at": time.monotonic()}


def _resolve_speed_test_return(self, msg):
    """测速 return(二进制回显)到达：合并 result 数据，resolve 本轮 future。

    以 return 到达作为一轮结束，使上下行串行化：下一轮载荷不会与本轮回显争抢链路。
    """
    rid = msg.RequestID
    futures = getattr(self, "_speed_test_futures", None)
    if not futures or rid not in futures:
        return
    stored = (getattr(self, "_speed_test_results", None) or {}).pop(rid, None)
    fut = futures.pop(rid, None)
    if fut is not None and not fut.done():
        fut.set_result({
            "result": stored["result"] if stored else None,
            "result_at": stored["at"] if stored else None,
            "return_at": time.monotonic(),
            "return_binary": msg.BinaryData,
        })


async def _handle_speed_test_request(self, msg):
    """收到远端设备发来的测速请求：记录接收时间，回发 result + return(回显二进制)。"""
    received_at = int(time.time() * 1000)
    payload_size = len(msg.BinaryData) if msg.BinaryData else 0

    result_msg = message.Message(
        Type="system",
        Action="speed-test-result",
        To=msg.From,
        RequestID=msg.RequestID,
        Data={"receivedBytes": payload_size, "receivedAt": received_at},
    )
    await self.send(result_msg)

    return_msg = message.Message(
        Type="system",
        Action="speed-test-return",
        To=msg.From,
        RequestID=msg.RequestID,
        Data=msg.Data,
        Binary=True,
    )
    if msg.BinaryData:
        await self.send_pair(return_msg, msg.BinaryData)
    else:
        await self.send(return_msg)
