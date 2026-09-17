from .message import Message


def ping(Data = None, To: str | None = None, RequestID: str | None = None):
    if Data is None:
        from time import time
        Data = {"time": int(time() * 1000)}
    return Message(
        Type="system",
        Action="ping",
        To=To,
        RequestID=RequestID,
        Data=Data,
    ).to_json()


def pong(To: str | None = None, RequestID: str | None = None, Data=None):
    """原样回显一条 ping：Action=pong，RequestID 与 Data 照抄(供发起者算往返延迟)。

    To 指向 ping 的发起者——经服务端路由回去时必填(msg.From)；本地桥直连回发时可留空。
    与服务端 PingLogic.Ping() 的"服务端自答 pong"保持同一形态。
    """
    return Message(
        Type="system",
        Action="pong",
        To=To,
        RequestID=RequestID,
        Data=Data,
    ).to_json()
