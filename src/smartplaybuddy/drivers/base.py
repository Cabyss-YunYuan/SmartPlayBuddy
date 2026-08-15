"""驱动基类定义。所有驱动必须继承 BaseDriver 并实现 operate() 方法。"""
from abc import ABC, abstractmethod
from typing import Any


class BaseDriver(ABC):
    """驱动抽象基类。"""
    name: str = ""
    versionCode: str = "1.0.0"
    versionName: str = "v1.0.0"
    description: str = ""

    @abstractmethod
    def operate(self, command: str, params: dict) -> Any:
        """处理驱动命令，返回结果字典。含二进制数据时需在结果中包含 __data__ 和 __mime__ 键。"""
        ...

    def start(self):
        """驱动启动时的初始化钩子。"""
        pass

    def stop(self):
        """驱动停止时的清理钩子。"""
        pass
