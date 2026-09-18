from .message import Message


def claim(status: dict):
    return Message(
        Type="session",
        Action="claim",
        Data=status,
    ).to_json()


def status():
    """查询服务端视角的本机权威 session 信息(无 Data)。"""
    return Message(
        Type="session",
        Action="status",
    ).to_json()
