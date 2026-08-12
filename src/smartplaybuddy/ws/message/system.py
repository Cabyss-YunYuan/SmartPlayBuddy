from .message import Message


def ping(To: str | None = None):
    from time import time


    return Message(
        Type="system",
        Action="ping",
        To=To,
        Data={"time": int(time() * 1000)},
    ).to_json()
