# Driver System

## Overview

SmartPlayBuddy uses a **plugin-based driver architecture**. All drivers (including keyboard/mouse) exist as plugins, loaded in separate subprocesses, enabling hot-plugging and crash isolation.

## Architecture

```
Main Program (SmartPlayBuddy)
│
│ DriverRegistry scans the drivers/ directory
│ Discovers plugins → launches subprocesses on demand
│
├── Subprocess: keyboard driver
│   ↕ stdin/stdout binary frame IPC
│
├── Subprocess: mouse driver
│   ↕ stdin/stdout binary frame IPC
│
├── Subprocess: screen driver
│   ↕ stdin/stdout binary frame IPC (with streaming)
│
└── Subprocess: custom driver...
    ↕ stdin/stdout binary frame IPC
```

- **Each driver runs in its own subprocess** — a crash does not affect the main program or other drivers
- Drivers are written in Python, inheriting from the `BaseDriver` base class
- The main program communicates with drivers via **stdin/stdout binary frames** (IPC)
- Subprocesses are automatically restarted on crash

## Directory Structure

### Development Environment

```
SmartPlayBuddy/
├── src/smartplaybuddy/
│   ├── drivers/           ← Driver framework + plugin directory
│   │   ├── __init__.py
│   │   ├── base.py        ← BaseDriver base class
│   │   ├── host.py        ← Subprocess driver runner
│   │   ├── registry.py    ← Registry + subprocess management
│   │   ├── keyboard/      ← Keyboard driver plugin
│   │   │   ├── manifest.json
│   │   │   ├── driver.py
│   │   │   ├── requirements.txt
│   │   │   └── packages/  ← Dependency install directory
│   │   ├── mouse/         ← Mouse driver plugin
│   │   └── screen/        ← Screen capture driver plugin
│   └── client.py
└── ...
```

### Packaged (Directory Mode)

```
SmartPlayBuddy/
├── SmartPlayBuddy.exe
├── _internal/             ← PyInstaller bundled Python environment
├── drivers/               ← Driver plugin directory (hot-swappable)
│   ├── keyboard/
│   ├── mouse/
│   ├── screen/
│   └── custom driver/
└── runtime/
    └── python.exe         ← Standalone Python runtime (for installing dependencies)
```

## Plugin Specification

Each driver plugin is a directory containing the following files:

| File | Required | Description |
|------|----------|-------------|
| `manifest.json` | ✅ | Plugin metadata |
| `driver.py` | ✅ | Driver code (configurable entry filename) |
| `requirements.txt` | Optional | Third-party dependency declaration |
| `packages/` | Auto-generated | Dependency install directory (isolated) |

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

| Field | Required | Description |
|-------|----------|-------------|
| `appid` | ✅ | Unique application identifier (reverse domain format) |
| `versionCode` | ✅ | Version number (semantic version string) |
| `versionName` | ✅ | Version name (for display) |
| `name` | ✅ | Unique driver identifier (used for registration and scheduling) |
| `description` | ☐ | Driver description |
| `entry` | ☐ | Entry file, defaults to `driver.py` |
| `requirements` | ☐ | Dependency file, defaults to `requirements.txt` |
| `actions` | ☐ | List of actions this driver handles, defaults to `[name]` |

### driver.py

Driver code must inherit `BaseDriver` and implement the `operate` method:

```python
from drivers.base import BaseDriver


class MyDriver(BaseDriver):
    name = "my_driver"
    versionCode = "1.0.0"
    versionName = "v1.0.0"
    description = "My custom driver"

    def start(self):
        """Called when the driver starts, for resource initialization."""
        pass

    def operate(self, command: str, params: dict):
        """
        Handle operation commands.

        Args:
            command: Action name (corresponds to actions in manifest)
            params:  Full parameter dictionary

        Returns:
            Result dictionary. Include __data__ and __mime__ keys for binary data.
        """
        return {"status": "ok", "result": None}

    def stop(self):
        """Called when the driver stops, for resource cleanup."""
        pass
```

> **Note**: Each driver file can only contain one `BaseDriver` subclass.

### BaseDriver Interface

```python
from abc import ABC, abstractmethod
from typing import Any


class BaseDriver(ABC):
    name: str = ""              # Driver name
    versionCode: str = "1.0.0"  # Version number
    versionName: str = "v1.0.0" # Version name
    description: str = ""       # Description

    @abstractmethod
    def operate(self, command: str, params: dict) -> Any: ...

    def start(self): pass   # Optional: initialization
    def stop(self): pass    # Optional: cleanup
```

### Streaming Driver Extension Interface

Drivers that support high-frequency data streams (e.g., screen capture) must additionally implement the following methods:

```python
class StreamingDriver(BaseDriver):

    def is_streaming(self) -> bool:
        """Returns whether there is an active stream."""
        return bool(self._streams)

    def get_active_streams(self) -> dict:
        """Returns all active stream configs: {stream_id: config}."""
        return dict(self._streams)

    def capture_frames(self) -> list:
        """
        Capture one frame of data. Called periodically by host.py in streaming mode.

        Returns a list of frame result dicts, each must contain:
        - stream_id: Stream identifier
        - __data__: Binary frame data (bytes)
        - __mime__: MIME type (e.g., "image/jpeg")
        - result: Frame metadata (e.g., {"format": "jpeg", "width": 1920, "height": 1080})
        """
        return []
```

## IPC Communication Protocol

The main program communicates with driver subprocesses via **stdin/stdout** using a **binary frame** format.

### Frame Format

```
┌──────┬───────────┬───────────┬────────────┬────────────┐
│ type │ json_len  │ bin_len   │ json_bytes │ bin_bytes  │
│ 1B   │ 4B (LE)   │ 4B (LE)   │ Variable   │ Variable   │
└──────┴───────────┴───────────┴────────────┴────────────┘
```

- `type`: Message type (`0` = normal message)
- `json_len`: Byte length of the JSON portion
- `bin_len`: Byte length of the binary data portion (can be 0)
- Total frame header size is fixed at 9 bytes (`<BII` format)

### Main Program → Driver

**Execute operation:**

```
Frame header(type=0) + {"command":"operate", "cmd":"mouse", "params":{"operate":"move_to", "x":"0.5", "y":"0.5"}}
```

**Stop driver:**

```
Frame header(type=0) + {"command":"stop"}
```

### Driver → Main Program

**Ready signal (sent first after startup):**

```
Frame header(type=0) + {"status":"ready"}
```

**Normal response:**

```
Frame header(type=0) + {"status":"ok", "result":null}
```

**Response with binary data (e.g., screenshot):**

```
Frame header(type=0, json_len=N, bin_len=M) + {"status":"ok", "result":{"format":"jpeg","width":1920,"height":1080}} + [JPEG bytes]
```

**Streaming frame:**

```
Frame header(type=0) + {"status":"ok", "type":"stream", "stream_id":"xxx", "result":{...}} + [frame binary data]
```

**Error response:**

```
Frame header(type=0) + {"status":"error", "message":"Error description"}
```

## Workflow

```
1. Main program starts
    └→ DriverRegistry initializes

2. Receives command (e.g., Action="mouse")
    └→ DriversDict["mouse"]
    └→ Registry.get_callable("mouse")
    ├→ First call: scans the drivers/ directory
    ├→ Finds mouse/manifest.json
    ├→ Installs dependencies (pip install --target packages/)
    ├→ Launches subprocess (no-window mode)
    └→ Returns callable object

3. Subprocess starts
    └→ host.py → run_driver()
    ├→ Loads driver.py
    ├→ Instantiates BaseDriver subclass
    ├→ Calls start()
    ├→ Sends {"status":"ready"}
    └→ Enters communication loop

4. IPC Communication
    Main program → Frame header + {"command":"operate", "cmd":"mouse", "params":{...}}
    Subprocess   ← Frame header + {"status":"ok", "result":{...}}

5. Streaming (e.g., screen capture)
    ├→ Main program sends start_stream command
    ├→ Driver enters streaming mode
    ├→ host.py periodically calls capture_frames()
    ├→ Each frame sent via IPC (JSON metadata + binary frame data)
    ├→ Main program registers callbacks to forward frame data to server
    └→ Stops upon receiving stop_stream command

6. Main program exits
    └→ registry.shutdown() → stops all subprocesses
```

## Crash Recovery

- When a driver subprocess crashes, the main program auto-detects and restarts it
- After restart, the operation is automatically retried once (`_operate_with_recovery`)
- If the retry still fails, an error response is returned

## Dependency Management

- Each driver can declare its own third-party dependencies in `requirements.txt`
- On first load, the main program automatically runs `pip install --target packages/` to install dependencies into the driver's local directory
- Each driver's dependencies are **isolated** from one another, preventing version conflicts
- If the `packages/` directory already exists and is non-empty, installation is skipped
- When packaged with `runtime/python.exe`, dependencies can be auto-installed; otherwise they must be pre-installed

## Hot-Plugging

Drivers support runtime loading and replacement:

```python
from smartplaybuddy.drivers import registry

# Rescan the driver directory
registry.rescan()

# Reload a specific driver
registry.reload("mouse")

# Stop all drivers
registry.shutdown()
```

To replace a driver:
1. Modify or replace the plugin files under the `drivers/` directory
2. Call `registry.reload("driver_name")` or `registry.rescan()` to reload

## Developing a New Driver

Example: creating a serial port device driver.

**1. Create plugin directory**

```
drivers/serial_device/
```

**2. Write manifest.json**

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

**3. Write driver.py**

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

**4. Declare dependencies**

`requirements.txt`:

```requirements.txt
pyserial
```

Place it in the `drivers/` directory and it's ready to use — no main program modifications needed.
