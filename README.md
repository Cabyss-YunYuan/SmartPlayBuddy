# SmartPlayBuddy

> 🌐 Language: **English** | [简体中文](docs/zh-CN/README.md)

SmartPlayBuddy is a WebSocket-based remote device control system. With a plugin-based driver architecture, it supports local device operations such as keyboard, mouse, screen capture, and provides streaming data transmission capabilities.

## Features

- **Plugin-based Drivers** — Keyboard, mouse, screen capture and other drivers are loaded as plugins, supporting hot-plugging and crash isolation
- **Streaming** — Supports high-frequency data streams such as screen capture, efficiently transmitted via the text + binary dual-frame protocol
- **Mod Extensions** — Provides a Mod base class for third-party custom business logic
- **Internationalization** — Built-in i18n module with multi-language hot-reload support
- **JWT Authentication** — Server identity verification via JWT tokens

## Architecture Overview

```


┌─────────────────────────────────────────────────────┐
│                      Server                         │
│         JWT Auth / Device Mgmt / Msg Routing        │
└──────────────────────┬──────────────────────────────┘
                       │ WebSocket
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │  Client  │ │   Mod    │ │  Client  │
    │ (Device) │ │  (Logic) │ │ (Device) │
    └────┬─────┘ └──────────┘ └──────────┘
         │
    ┌────┴──────────────────────┐
    │      DriverRegistry       │
    │  ┌────────┬──────┬──────┐ │
    │  │Keyboard│Mouse │Screen│ │
    │  │ Driver │Driver│Driver│ │
    │  └────┬───┴──┬───┴──┬───┘ │
    │      │IPC   │IPC   │IPC   │
    │ Subproc  Subproc  Subproc │
    └───────────────────────────┘
```
## Project Structure

```


SmartPlayBuddy/
├── src/smartplaybuddy/
│   ├── client.py          ← Main client module (device side)
│   ├── mod.py             ← Mod entry point
│   ├── config/            ← Global config (server URL, version)
│   ├── drivers/           ← Driver framework + plugin directory
│   │   ├── base.py        ← BaseDriver base class
│   │   ├── host.py        ← Subprocess driver runner
│   │   ├── registry.py    ← Driver registry + subprocess management
│   │   ├── keyboard/      ← Keyboard driver
│   │   ├── mouse/         ← Mouse driver
│   │   └── screen/        ← Screen capture driver
│   ├── i18n/              ← Internationalization module
│   │   ├── translator.py  ← Translator
│   │   └── locales/       ← Language packs
│   ├── log/               ← Logging module
│   ├── user/              ← User authentication (JWT login)
│   └── ws/                ← WebSocket connector
│       ├── connector.py   ← Connection base class (text+binary dual-frame protocol)
│       ├── message/       ← Message protocol definitions
│       └── logic/         ← System message handling
├── docs/                  ← Documentation
└── pyproject.toml         ← Project configuration
```
## Quick Start

### Requirements

- Python >= 3.10
- Windows (screen capture driver depends on DirectX)

### Installation

```bash
pip install -e .
```
### Run Client (Device Side)

```bash
smtplay
```
After the client starts, it will:
1. Auto-login or open browser for JWT authentication
2. Scan and load local drivers
3. Connect to the server and wait for commands

## Documentation

- [Data Format](docs/data-format.md) — WebSocket message protocol
- [Driver System](docs/driver.md) — Driver development guide
- [Mod Development](docs/mods/mod-development.md) — Mod extension development guide

## Tech Stack

| Component              | Technology                  |
|------------------------|-----------------------------|
| Communication Protocol | WebSocket (websockets)      |
| Driver IPC             | stdin/stdout binary frames  |
| Screen Capture         | dxcam + OpenCV              |
| Keyboard/Mouse Control | pyautogui                   |
| Authentication         | JWT (keyring token storage) |
| Internationalization   | Custom i18n module          |

> This project is currently under development, stay tuned...
