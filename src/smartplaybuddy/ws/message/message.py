from ...i18n import translate
from ... import log

from dataclasses import dataclass, field
from typing import Any
import json
import base64
import time
import threading


logger = log.logger.getChild("ws").getChild("message")


class SnowflakeGenerator:
    def __init__(self, worker_id: int = 0, datacenter_id: int = 0):
        self.worker_id = worker_id & 0x1F
        self.datacenter_id = datacenter_id & 0x1F
        self.sequence = 0
        self.last_timestamp = -1
        self.lock = threading.Lock()
        self.epoch = 1700000000000

    def _current_millis(self) -> int:
        return int(time.time() * 1000)

    def generate(self) -> str:
        with self.lock:
            timestamp = self._current_millis()
            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & 0xFFF
                if self.sequence == 0:
                    while timestamp <= self.last_timestamp:
                        timestamp = self._current_millis()
            else:
                self.sequence = 0
            self.last_timestamp = timestamp
            snowflake_id = (
                ((timestamp - self.epoch) << 22)
                | (self.datacenter_id << 17)
                | (self.worker_id << 12)
                | self.sequence
            )
            return str(snowflake_id)


_generator = SnowflakeGenerator()


@dataclass
class Message:
    Type: str
    Action: str
    From: str | None = None
    To: str | None = None
    RequestID: str | None = None
    Data: Any = None
    Timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    BinaryData: bytes | None = None

    @classmethod
    def from_json(cls, d: dict) -> "Message":
        data = d.get("data")
        if data is not None:
            raw = base64.b64decode(data)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                try:
                    data = raw.decode("utf-8")
                except UnicodeDecodeError:
                    data = raw
        return cls(
            Type=d["type"],
            Action=d["action"],
            From=d.get("from"),
            To=d.get("to"),
            RequestID=d.get("requestId"),
            Data=data,
            Timestamp=d["timestamp"],
        )

    def _encode_data(self) -> str | None:
        if self.Data is None:
            return None
        if isinstance(self.Data, bytes):
            raw = self.Data
        elif isinstance(self.Data, str):
            raw = self.Data.encode("utf-8")
        elif isinstance(self.Data, (dict, list)):
            raw = json.dumps(self.Data).encode("utf-8")
        else:
            logger.error(translate("error.unsupported_data_type", type=type(self.Data).__name__, value=self.Data))
            return None
        return base64.b64encode(raw).decode("utf-8")

    def to_json(self) -> str:
        d = {
            "type": self.Type,
            "action": self.Action,
            "timestamp": self.Timestamp,
        }
        if self.From is not None:
            d["from"] = self.From
        if self.To is not None:
            d["to"] = self.To
        if self.RequestID is None:
            self.RequestID = _generator.generate()
        d["requestId"] = self.RequestID
        encoded = self._encode_data()
        if encoded is not None:
            d["data"] = encoded

        logger.debug(f"{self}")
        return json.dumps(d)
