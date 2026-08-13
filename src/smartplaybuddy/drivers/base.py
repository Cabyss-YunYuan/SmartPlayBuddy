from abc import ABC, abstractmethod
from typing import Any


class BaseDriver(ABC):
    name: str = ""
    versionCode: str = "1.0.0"
    versionName: str = "v1.0.0"
    description: str = ""

    @abstractmethod
    def operate(self, command: str, params: dict) -> Any:
        ...

    def start(self):
        pass

    def stop(self):
        pass
