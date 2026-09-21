# Mod Development Guide

## Overview

A Mod is the **logic extension side** of SmartPlayBuddy. Unlike the Client (device side), a Mod does not directly control local hardware. Instead, it acts as an independent business logic node connected to the server, receiving messages and executing custom processing logic.

**Typical Use Cases:**
- Automation scripts (e.g., scheduled tasks, conditional triggers)
- Message forwarding and filtering
- Game assistant logic
- Multi-device coordinated control

## Architecture

```
┌──────────────────┐
│     Server       │
│  Msg Routing/Auth│
└────────┬─────────┘
         │ WebSocket
    ┌────┴────┐
    │         │
┌───┴───┐ ┌──┴────┐
│Client │ │  Mod  │
│Device │ │Logic  │
│KBD/Mouse│ │Custom │
└───────┘ │Logic  │
          └───────┘
```

- Mods share the same WebSocket connection framework as Clients (`Connector`)
- Mods register with the server as `type: "mod"`
- The server can route Client messages to a Mod for processing

## Quick Start

### Minimal Mod

```python
from smartplaybuddy.mod import Mod


class MyMod(Mod):
    async def main(self, msg) -> None:
        """Handle each received message."""
        print(f"Received: type={msg.Type}, action={msg.Action}")


if __name__ == "__main__":
    from smartplaybuddy.mod import main
    main(MyMod)
```

### Run

```bash
python my_mod.py
```

After the Mod starts, it will:
1. Auto-login (JWT authentication)
2. Connect to the server as `type: "mod"`
3. Enter the message loop, calling `main()` for each message

## Mod Base Class

```python
class Mod(ws.Connector):
    """Mod base class. Developers inherit and implement the main() method to handle messages."""

    async def main(self, msg) -> None:
        """
        Handle received messages. Called for every message.

        Note: system and session messages are handled by framework decorators
        before reaching main(). You will not receive them here.

        Args:
            msg: Message object with the following fields:
                - Type: Message type (command/response/stream/error/request/event/system/session)
                - Action: Operation action
                - From: Sender identifier
                - To: Target identifier
                - RequestID: Request ID
                - Data: Business data (auto-decoded)
                - Timestamp: Timestamp
        """
        pass
```

## Available Interfaces

Mod inherits all capabilities from `Connector`:

### Sending Messages

When sending a message to a specific device, the `To` field must contain the target device's full routing identifier (`{type}:{userId}:{deviceName}`):

```python
from smartplaybuddy.mod import Mod


class MyMod(Mod):
    async def main(self, msg) -> None:
        # Send a keyboard command to a specific Client
        await self.send(self.Message(
            Type="command",
            Action="keyboard",
            To="client:123:my-pc",
            Data={
                "operate": "tap",
                "key": "a",
            },
        ))

        # Send an error response
        await self.Error.error("Processing failed", To=msg.From, RequestID=msg.RequestID)
```

> **Tip**: `send()` accepts `Message` objects directly.

> **Tip**: `msg.From` is already in the full routing identifier format and can be used directly in the `To` field to reply.

### Cross-UID Authorization (`permit`)

When your Mod needs to send commands to a Client of a **different user** (cross-UID), you must first obtain authorization. The `permit()` context manager handles the full authorization lifecycle automatically:

```python
from smartplaybuddy.mod import Mod
import asyncio

TARGET = "client:123:my-pc"


class MyMod(Mod):
    async def main(self, msg) -> None:
        pass


async def run():
    mod = MyMod(url="wss://smtplay.cabyss.cn/ws",
                status={"device": {"type": "mod", "deviceName": "my-mod"}})
    await mod.wait_ready()  # Wait for connection to establish

    # Request authorization → wait for approval → activate event lock
    async with mod.permit(TARGET, "Keyboard test") as rid:
        # Send commands with the authorization RID
        await mod.send(mod.Message(
            Type="command", Action="keyboard", To=TARGET, RequestID=rid,
            Data={"operate": "tap", "key": "a"},
        ))

    # Exiting async with automatically releases the event lock
```

> **Note**: Use `await mod.wait_ready()` to wait for the connection to be fully established (WebSocket handshake + claim completed). Do **not** use `await mod.connection` — it wraps the infinite reconnection loop and will block forever.

**How `permit()` works:**

1. Sends a `request/permit` to the target Client
2. The target user sees an authorization prompt and approves/rejects
3. On approval, sends an `event/activate` to lock the authorization
4. The returned `rid` (Request ID) is the authorization credential — all commands must carry it
5. When the `async with` block exits (normally or due to exception), automatically sends `event/release` to unlock

> **Note**: Commands to the **same user** (same UID) do not require `permit()`. Only cross-UID operations need authorization.

### System Functions

```python
class MyMod(Mod):
    async def main(self, msg) -> None:
        # Heartbeat check
        await self.System.ping()
```

> **Note**: `ping`/`pong` heartbeat is handled automatically by the framework decorator. You typically don't need to call it manually.

### Connection Lifecycle

```python
class MyMod(Mod):

    def on_close(self):
        """Callback when the connection is closed, useful for resource cleanup."""
        print("Connection closed")
```

## Message Object

| Field | Type | Description |
|-------|------|-------------|
| `Type` | str | Message type: `command` / `response` / `stream` / `error` / `request` / `event` / `query` / `system` / `session` |
| `Action` | str | Operation action |
| `From` | str \| None | Sender identifier |
| `To` | str \| None | Target identifier |
| `RequestID` | str \| None | Request ID |
| `Data` | Any | Business data (auto Base64-decoded and JSON-parsed) |
| `Timestamp` | int | Millisecond timestamp |
| `BinaryData` | bytes \| None | Binary data (auto-filled after pairing with text frame) |
| `Binary` | bool | Whether binary data is included |

> **Note**: `system` and `session` type messages are intercepted by framework decorators (`system_dispatch`, `session_dispatch`) and do not reach `main()`.

## Complete Example: Message Forwarding Mod

```python
from smartplaybuddy.mod import Mod


class ForwardMod(Mod):
    """Forward keyboard commands from device A to device B."""

    async def main(self, msg) -> None:
        if msg.Type == "command" and msg.Action == "keyboard":
            # msg.From is in "client:123:A" format, can be used directly for replies
            # Forward to target device "client:123:B"
            await self.send(self.Message(
                Type="command",
                Action="keyboard",
                To="client:123:B",
                Data=msg.Data,
            ))

    def on_close(self):
        pass


if __name__ == "__main__":
    from smartplaybuddy.mod import main
    main(ForwardMod)
```

## Notes

- Mod and Client use the **same JWT account** to log in, but register with different types (`mod` vs `client`)
- The same account can run multiple Mod instances simultaneously
- The `main()` method is asynchronous and supports `await` operations
- The `Data` field in messages is auto-decoded (Base64 → Dict | Str | None) and ready to use
- For sending binary data, refer to the dual-frame protocol in [Data Format](../DataFormat.md)
- `system` and `session` messages are handled by framework decorators before reaching `main()`

## Console Access
See [ModConsoleAccess](ModConsoleAccess.md) for details.
