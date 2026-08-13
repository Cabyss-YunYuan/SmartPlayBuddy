# 驱动系统

## 概述

SmartPlayBuddy 采用**插件化驱动架构**。所有驱动（包括键鼠）均以插件形式存在，通过子进程加载，实现热插拔和崩溃隔离。

## 架构

```
主程序 (SmartPlayBuddy)
│
│ DriverRegistry 扫描 drivers/ 目录
│ 发现插件 → 按需启动子进程
│ ├── 子进程: keyboard driver
│ ↕ stdin/stdout JSON 通信
│ ├── 子进程: mouse driver
│ ↕ stdin/stdout JSON 通信
│ └── 子进程: 未来其他驱动...
↕ stdin/stdout JSON 通信
```

- **每个驱动一个独立子进程**，崩溃不影响主程序和其他驱动
- 驱动使用 Python 编写，继承 `BaseDriver` 基类
- 主程序与驱动通过 **stdin/stdout JSON** 进行 IPC 通信

## 目录结构

### 开发环境

```
SmartPlayBuddy/
├── src/smartplaybuddy/
│ ├── drivers/ ← 驱动框架 + 插件目录
│ │ ├── init.py
│ │ ├── base.py ← BaseDriver 基类
│ │ ├── host.py ← 子进程驱动运行器
│ │ ├── registry.py ← 注册表 + 子进程管理
│ │ ├── keyboard/ ← 键盘驱动插件
│ │ │ ├── manifest.json
│ │ │ ├── driver.py
│ │ │ └── requirements.txt
│ │ └── mouse/ ← 鼠标驱动插件
│ │ ├── manifest.json
│ │ ├── driver.py
│ │ └── requirements.txt
│ └── client.py
└── ...
```

### 打包后（目录模式）

```
SmartPlayBuddy/
├── SmartPlayBuddy.exe
├── _internal/ ← PyInstaller 打包的 Python 环境
├── drivers/ ← 驱动插件目录（可热替换）
│ ├── keyboard/
│ ├── mouse/
│ └── 自定义驱动/
└── runtime/
└── python.exe ← 独立 Python 运行时（用于安装依赖）
```

## 插件规范

每个驱动插件是一个目录，包含以下文件：

| 文件 | 必需 | 说明 |
|------|------|------|
| `manifest.json` | ✅ | 插件元信息 |
| `driver.py` | ✅ | 驱动代码 |
| `requirements.txt` | 可选 | 第三方依赖声明 |
| `packages/` | 自动生成 | 依赖安装目录 |

### manifest.json

```json
{
  "name": "mouse",
  "version": "1.0.0",
  "description": "Mouse driver (pyautogui)",
  "entry": "driver.py",
  "requirements": "requirements.txt",
  "actions": ["mouse"]
}
```

| 字段 | 说明 |
|------|------|
| `name` | 驱动唯一标识 |
| `version` | 版本号 |
| `description` | 驱动描述 |
| `entry` | 入口文件，默认 `driver.py` |
| `requirements` | 依赖文件，默认 `requirements.txt` |
| `actions` | 该驱动处理的 action 列表 |

### driver.py

驱动代码需继承 `BaseDriver` 并实现 `operate` 方法：

```python
from drivers.base import BaseDriver
class MyDriver(BaseDriver):
    name = "my_driver"
    version = "1.0.0"
    description = "我的自定义驱动"
def start(self):
    """驱动启动时调用，初始化资源"""
    pass

def operate(self, command: str, params: dict):
    """
    处理操作指令。

    Args:
        command: 动作名称（对应 manifest 中的 actions）
        params:  完整的参数字典

    Returns:
        处理结果（任意可 JSON 序列化的值）
    """
    pass

def stop(self):
    """驱动停止时调用，清理资源"""
    pass
```

> **注意**：一个驱动文件中只能有一个 `BaseDriver` 子类。

### BaseDriver 接口

```python
from abc import ABC, abstractmethod
from typing import Any

class BaseDriver(ABC):
    name: str = "" # 驱动名称
    version: str = "1.0.0" # 版本号
    description: str = "" # 描述
@abstractmethod
def operate(self, command: str, params: dict) -> Any: ...

def start(self): pass   # 可选：初始化
def stop(self): pass    # 可选：清理
```

## 工作流程

```
1. 主程序启动
    └→ DriverRegistry 初始化
2. 收到指令（如 Action="mouse"）
    └→ DriversDict["mouse"]
    └→ Registry.get_callable("mouse")
    ├→ 首次调用：扫描 drivers/ 目录
    ├→ 找到 mouse/manifest.json
    ├→ 安装依赖（pip install --target packages/）
    ├→ 启动子进程
    └→ 返回可调用对象
3. 子进程启动
    └→ host.py → run_driver()
    ├→ 加载 driver.py
    ├→ 实例化 BaseDriver 子类
    ├→ 调用 start()
    └→ 进入 stdin/stdout 通信循环
4. IPC 通信
    主程序 → {"command":"operate", "cmd":"mouse", "params":{...}}
    子进程 ← {"status":"ok", "result":{...}}
5. 主程序退出
    └→ registry.shutdown() → 停止所有子进程
```

## IPC 通信协议

主程序与驱动子进程通过 **stdin/stdout** 以 **JSON Lines** 格式通信（每行一个 JSON 对象）。

### 主程序 → 驱动

```json
{"command": "operate", "cmd": "mouse", "params": {"operate": "move_to", "x": "0.5", "y": "0.5", "duration": "1"}}
```
```json
{"command": "stop"}
```
```json
{"command": "ping"}
```

### 驱动 → 主程序

```json
{"status": "ready", "name": "mouse"}
```

```json
{"status": "ok", "result": null}
```

```json
{"status": "error", "message": "错误描述"}
```

## 依赖管理

- 每个驱动可在 `requirements.txt` 中声明自己的第三方依赖
- 首次加载时，主程序自动执行 `pip install --target packages/` 将依赖安装到驱动本地目录
- 各驱动的依赖**互相隔离**，不会产生版本冲突
- 打包后，若附带 `runtime/python.exe`，可自动安装依赖；否则需预装

## 热插拔

驱动支持运行时加载和替换：

```python
from smartplaybuddy.drivers import registry
# 重新扫描驱动目录
registry.reload("mouse")
# 停止所有驱动
registry.shutdown()
```

替换驱动只需：
1. 修改或替换 `drivers/` 目录下的插件文件
2. 调用 `registry.reload("驱动名")` 重新加载

## 开发新驱动

以创建一个串口设备驱动为例：

**1. 创建插件目录**

```
drivers/serial_device/
```

**2. 编写 manifest.json**

```json
{
  "name": "serial_device",
  "version": "1.0.0",
  "description": "Serial port device driver",
  "entry": "driver.py",
  "requirements": "requirements.txt",
  "actions": ["serial_device"]
}
```

**3. 编写 driver.py**

```python
from drivers.base import BaseDriver
import serial

class SerialDeviceDriver(BaseDriver):
    name = "serial_device"
def __init__(self):
    self.ser = None

def start(self):
    self.ser = serial.Serial("COM3", 9600, timeout=1)

def operate(self, command: str, params: dict):
    if command == "serial_device":
        data = params.get("data", "")
        self.ser.write(data.encode())
        response = self.ser.readline().decode()
        return {"response": response}

def stop(self):
    if self.ser:
        self.ser.close()
```

**4. 声明依赖**

```requirements.txt
pyserial
```

放入 `drivers/` 目录后即可使用，无需修改主程序。
