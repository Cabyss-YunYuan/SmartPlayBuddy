from .message import Message


def error(data, To: str | None = None, RequestID: str | None = None):
    return Message(
        Type="error",
        Action="error",
        To=To,
        RequestID=RequestID,
        Data=data,
    ).to_json()
