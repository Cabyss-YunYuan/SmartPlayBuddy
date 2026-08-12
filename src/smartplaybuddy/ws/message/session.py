from .message import Message


def claim(status: dict):
    return Message(
        Type="session",
        Action="claim",
        Data=status,
    ).to_json()
