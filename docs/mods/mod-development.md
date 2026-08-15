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

        Args:
            msg: Message object with the following fields:
                - Type: Message type (command/response/stream/error/system)
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
        await self.conn.send(self.Message(
            Type="command",
            Action="keyboard",
            To="client:123:my-pc",
            Data={
                "operate": "tap",
                "key": "a",
            },
        ).to_json())

        # Send an error response
        await self.Error.error("Processing failed", To=msg.From, RequestID=msg.RequestID)
```

> **Tip**: `msg.From` is already in the full routing identifier format and can be used directly in the `To` field to reply.

### System Functions

```python
class MyMod(Mod):
    async def main(self, msg) -> None:
        # Heartbeat check
        await self.System.ping()
```

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
| `Type` | str | Message type: `command` / `response` / `stream` / `error` / `system` |
| `Action` | str | Operation action |
| `From` | str \| None | Sender identifier |
| `To` | str \| None | Target identifier |
| `RequestID` | str \| None | Request ID |
| `Data` | Any | Business data (auto Base64-decoded and JSON-parsed) |
| `Timestamp` | int | Millisecond timestamp |
| `BinaryData` | bytes \| None | Binary data (auto-filled after pairing with text frame) |
| `Binary` | bool | Whether binary data is included |

## Complete Example: Message Forwarding Mod

```python
from smartplaybuddy.mod import Mod


class ForwardMod(Mod):
    """Forward keyboard commands from device A to device B."""

    async def main(self, msg) -> None:
        if msg.Type == "command" and msg.Action == "keyboard":
            # msg.From is in "client:123:A" format, can be used directly for replies
            # Forward to target device "client:123:B"
            await self.conn.send(self.Message(
                Type="command",
                Action="keyboard",
                To="client:123:B",
                Data=msg.Data,
            ).to_json())

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
- For sending binary data, refer to the dual-frame protocol in [Data Format](../data-format.md)
