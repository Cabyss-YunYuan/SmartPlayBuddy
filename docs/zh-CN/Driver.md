# 驱动系统

## 概述

SmartPlayBuddy 采用**插件化驱动架构**。所有驱动（包括键鼠）均以插件形式存在，通过子进程加载，实现热插拔和崩溃隔离。

## 架构

```
主程序 (SmartPlayBuddy)
│
│ DriverRegistry 扫描 drivers/ 目录
│ 发现插件 → 按需启动子进程
│
├── 子进程: keyboard driver
│   ↕ stdin/stdout 二进制帧 IPC
│
├── 子进程: mouse driver
│   ↕ stdin/stdout 二进制帧 IPC
│
├── 子进程: screen driver
│   ↕ stdin/stdout 二进制帧 IPC（含流式传输）
│
└── 子进程: 自定义驱动...
    ↕ stdin/stdout 二进制帧 IPC
```

- **每个驱动一个独立子进程**，崩溃不影响主程序和其他驱动
- 驱动使用 Python 编写，继承 `BaseDriver` 基类
- 主程序与驱动通过 **stdin/stdout 二进制帧** 进行 IPC 通信
- 子进程崩溃后自动重启重试

## 目录结构

### 开发环境

```
SmartPlayBuddy/
├── src/smartplaybuddy/
│   ├── drivers/           ← 驱动框架 + 插件目录
│   │   ├── __init__.py
│   │   ├── base.py        ← BaseDriver 基类
│   │   ├── host.py        ← 子进程驱动运行器
│   │   ├── registry.py    ← 注册表 + 子进程管理
│   │   ├── keyboard/      ← 键盘驱动插件
│   │   │   ├── manifest.json
│   │   │   ├── driver.py
│   │   │   ├── requirements.txt
│   │   │   └── packages/  ← 依赖安装目录
│   │   ├── mouse/         ← 鼠标驱动插件
│   │   └── screen/        ← 屏幕捕获驱动插件
│   └── client.py
└── ...
```

### 打包后（目录模式）

```
SmartPlayBuddy/
├── SmartPlayBuddy.exe
├── _internal/             ← PyInstaller 打包的 Python 环境
├── drivers/               ← 驱动插件目录（可热替换）
│   ├── keyboard/
│   ├── mouse/
│   ├── screen/
│   └── 自定义驱动/
└── runtime/
    └── python.exe         ← 独立 Python 运行时（用于安装依赖）
```

## 插件规范

每个驱动插件是一个目录，包含以下文件：

| 文件 | 必需 | 说明 |
|------|------|------|
| `manifest.json` | ✅ | 插件元信息 |
| `driver.py` | ✅ | 驱动代码（可配置入口文件名） |
| `requirements.txt` | 可选 | 第三方依赖声明 |
| `packages/` | 自动生成 | 依赖安装目录（隔离） |

### manifest.json

```json
{
  "appid": "smartplaybuddy.driver.keyboard",
  "versionCode": "1.0.0",
  "versionName": "v1.0.0",
  "name": "keyboard",
  "description": "Keyboard driver (pyautogui)",
  "entry": "driver.py",
  "requirements": "requirements.txt",
  "actions": ["keyboard"]
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `appid` | ✅ | 应用唯一标识（反向域名格式） |
| `versionCode` | ✅ | 版本号（语义化版本字符串） |
| `versionName` | ✅ | 版本名称（展示用） |
| `name` | ✅ | 驱动唯一标识（用于注册和调度） |
| `description` | ☐ | 驱动描述 |
| `entry` | ☐ | 入口文件，默认 `driver.py` |
| `requirements` | ☐ | 依赖文件，默认 `requirements.txt` |
| `actions` | ☐ | 该驱动处理的 action 列表，默认 `[name]` |

### driver.py

驱动代码需继承 `BaseDriver` 并实现 `operate` 方法：

```python
from drivers.base import BaseDriver


class MyDriver(BaseDriver):
    name = "my_driver"
    versionCode = "1.0.0"
    versionName = "v1.0.0"
    description = "我的自定义驱动"

    def start(self):
        """驱动启动时调用，初始化资源。"""
        pass

    def operate(self, command: str, params: dict):
        """
        处理操作指令。

        Args:
            command: 动作名称（对应 manifest 中的 actions）
            params:  完整的参数字典

        Returns:
            处理结果字典。含二进制数据时需包含 __data__ 和 __mime__ 键。
        """
        return {"status": "ok", "result": None}

    def stop(self):
        """驱动停止时调用，清理资源。"""
        pass
```

> **注意**：一个驱动文件中只能有一个 `BaseDriver` 子类。

### BaseDriver 接口

```python
from abc import ABC, abstractmethod
from typing import Any


class BaseDriver(ABC):
    name: str = ""              # 驱动名称
    versionCode: str = "1.0.0"  # 版本号
    versionName: str = "v1.0.0" # 版本名称
    description: str = ""       # 描述

    @abstractmethod
    def operate(self, command: str, params: dict) -> Any: ...

    def start(self): pass   # 可选：初始化
    def stop(self): pass    # 可选：清理
```

### 流式驱动扩展接口

支持屏幕捕获等高频数据流的驱动，需额外实现以下方法：

```python
class StreamingDriver(BaseDriver):

    def is_streaming(self) -> bool:
        """返回当前是否有活跃的流。"""
        return bool(self._streams)

    def get_active_streams(self) -> dict:
        """返回所有活跃流配置: {stream_id: config}。"""
        return dict(self._streams)

    def capture_frames(self) -> list:
        """
        捕获一帧数据。由 host.py 在流式模式下周期性调用。
        
        返回列表，每个元素为帧结果字典，必须包含:
        - stream_id: 流标识
        - __data__: 二进制帧数据 (bytes)
        - __mime__: MIME 类型 (如 "image/jpeg")
        - result: 帧元数据 (如 {"format": "jpeg", "width": 1920, "height": 1080})
        """
        return []
```

## IPC 通信协议

主程序与驱动子进程通过 **stdin/stdout** 以**二进制帧**格式通信。

### 帧格式

```

┌──────┬───────────┬───────────┬────────────┬────────────┐
│ type │ json_len  │ bin_len   │ json_bytes │ bin_bytes  │
│ 1B   │ 4B (LE)   │ 4B (LE)   │ 变长       │ 变长       │
└──────┴───────────┴───────────┴────────────┴────────────┘
```
- `type`：消息类型（`0` = 普通消息）
- `json_len`：JSON 部分字节长度
- `bin_len`：二进制数据部分字节长度（可为 0）
- 总帧头大小固定为 9 字节（`<BII` 格式）

### 主程序 → 驱动

**执行操作：**

```
帧头(type=0) + {"command":"operate", "cmd":"mouse", "params":{"operate":"move_to", "x":"0.5", "y":"0.5"}}
```

**停止驱动：**

```
帧头(type=0) + {"command":"stop"}
```

### 驱动 → 主程序

**就绪信号（启动后首先发送）：**

```
帧头(type=0) + {"status":"ready"}
```

**普通响应：**

```
帧头(type=0) + {"status":"ok", "result":null}
```

**带二进制数据的响应（如截图）：**

```
帧头(type=0, json_len=N, bin_len=M) + {"status":"ok", "result":{"format":"jpeg","width":1920,"height":1080}} + [JPEG bytes]
```

**流式帧：**

```
帧头(type=0) + {"status":"ok", "type":"stream", "stream_id":"xxx", "result":{...}} + [帧二进制数据]
```

**错误响应：**

```
帧头(type=0) + {"status":"error", "message":"错误描述"}
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
    ├→ 启动子进程（无窗口模式）
    └→ 返回可调用对象

3. 子进程启动
    └→ host.py → run_driver()
    ├→ 加载 driver.py
    ├→ 实例化 BaseDriver 子类
    ├→ 调用 start()
    ├→ 发送 {"status":"ready"}
    └→ 进入通信循环

4. IPC 通信
    主程序 → 帧头 + {"command":"operate", "cmd":"mouse", "params":{...}}
    子进程 ← 帧头 + {"status":"ok", "result":{...}}

5. 流式传输（如屏幕捕获）
    ├→ 主程序发送 start_stream 命令
    ├→ 驱动进入流式模式
    ├→ host.py 周期性调用 capture_frames()
    ├→ 每帧通过 IPC 发送（JSON 元数据 + 二进制帧数据）
    ├→ 主程序注册回调，将帧数据转发到服务端
    └→ 收到 stop_stream 命令后停止

6. 主程序退出
    └→ registry.shutdown() → 停止所有子进程
```

## 崩溃恢复

- 驱动子进程崩溃时，主程序自动检测并重启
- 重启后自动重试一次操作（`_operate_with_recovery`）
- 重试仍失败则返回错误响应

## 依赖管理

- 每个驱动可在 `requirements.txt` 中声明自己的第三方依赖
- 首次加载时，主程序自动执行 `pip install --target packages/` 将依赖安装到驱动本地目录
- 各驱动的依赖**互相隔离**，不会产生版本冲突
- 若 `packages/` 目录已存在且非空，跳过安装
- 打包后，若附带 `runtime/python.exe`，可自动安装依赖；否则需预装

## 热插拔

驱动支持运行时加载和替换：

```python
from smartplaybuddy.drivers import registry

# 重新扫描驱动目录
registry.rescan()

# 重载指定驱动
registry.reload("mouse")

# 停止所有驱动
registry.shutdown()
```

替换驱动只需：
1. 修改或替换 `drivers/` 目录下的插件文件
2. 调用 `registry.reload("驱动名")` 或 `registry.rescan()` 重新加载

## 开发新驱动

以创建一个串口设备驱动为例：

**1. 创建插件目录**

```

drivers/serial_device/
```
**2. 编写 manifest.json**

```json
{
  "appid": "myapp.driver.serial_device",
  "versionCode": "1.0.0",
  "versionName": "v1.0.0",
  "name": "serial_device",
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
            return {"status": "ok", "result": {"response": response}}

    def stop(self):
        if self.ser:
            self.ser.close()
```

**4. 声明依赖**

`requirements.txt`：

```requirements.txt
pyserial
```

放入 `drivers/` 目录后即可使用，无需修改主程序。
