# Data Format

## Overview

The client and server communicate via **WebSocket**, with authentication handled through JWT in the HTTP Headers during connection.

Communication uses the **text + binary dual-frame protocol**: when a message contains binary data, a text frame is sent first (JSON metadata, marked with `binary: true`), followed immediately by a binary frame (raw binary data).

## Message Structure

All messages use a unified JSON structure:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| type | string | ✅ | Message type: `command` / `response` / `stream` / `error` / `system` / `event` / `query` |
| action | string | ✅ | Operation action (e.g., `keyboard`, `mouse`, `screen`) |
| from | string | ☐ | Sender identifier (**auto-filled by server**, format: `{type}:{userId}:{deviceName}`) |
| to | string | ☐ | Target identifier (**key routing field**, format: `{type}:{userId}:{deviceName}`) |
| requestId | string | ☐ | Unique request ID (snowflake algorithm), returned as-is in responses |
| data | string | ☐ | Business data, Base64-encoded JSON string |
| binary | bool | ☐ | When `true`, indicates a binary frame follows |
| timestamp | int64 | ✅ | Millisecond timestamp |

### Message Routing

The server routes messages based on the `to` field:

1. When sending a message, the client sets `to` to the target device's full identifier (e.g., `client:123:my-pc`)
2. The server auto-fills `from` with the sender's identifier (e.g., `mod:123:my-mod`)
3. Before forwarding, the server clears the `to` field and pushes to the target connection

**Identifier Format**: `{deviceType}:{userId}:{deviceName}`

| Part | Description |
|------|-------------|
| `deviceType` | `client` (device side) or `mod` (logic side) |
| `userId` | User ID (determined by JWT authentication) |
| `deviceName` | Device name (specified during claim; auto-generated UUID if empty) |

> **Note**: Only one online connection is allowed per device name under the same user. When a new connection claims a device name that is already online, the server rejects the new connection.

### Data Field Encoding/Decoding

- **Sending**: `data` field content is JSON → UTF-8 bytes → Base64 string
- **Receiving**: Base64 decode → attempt JSON parse → retain original string on failure

### Binary Data

When a message needs to carry binary data (e.g., screenshots):

1. Send text frame: JSON `data` contains metadata (e.g., format, resolution), with `__binary__: true` inside the `data` object, and `binary: true` at the top level
2. Immediately follow with a binary frame: raw binary data

The receiving end automatically pairs the two frames.

## Message Types

### command — Command

Business commands sent from client to server, or operation commands from server to client.

```json
{
  "type": "command",
  "action": "keyboard",
  "to": "device-abc",
  "requestId": "1234567890",
  "data": "eyJvcGVyYXRlIjogInRhcCIsICJrZXkiOiAiYSJ9",
  "timestamp": 1700000000000
}
```
Decoded `data`: `{"operate": "tap", "key": "a"}`

### response — Response

Response to a `command`, carrying the same `requestId`.

**Normal response:**

```json
{
  "type": "response",
  "action": "keyboard",
  "requestId": "1234567890",
  "data": "eyJzdGF0dXMiOiAib2siLCAicmVzdWx0IjogbnVsbH0=",
  "timestamp": 1700000000001
}
```
**Response with binary data (e.g., screenshot):**

```


← text:  {"type":"response","action":"screen","requestId":"xxx","binary":true,"data":"eyJfX2JpbmFyeV9fIjp0cnVlLCJmb3JtYXQiOiJqcGVnIiwid2lkdGgiOjE5MjAsImhlaWdodCI6MTA4MH0="}
← binary: [JPEG binary data]
```
### stream — Data Stream

Streaming messages for high-frequency scenarios like screen capture. Structure is similar to `response`, but `type` is `stream`.

```


← text:  {"type":"stream","action":"screen","data":"eyJfX2JpbmFyeV9fIjp0cnVlLCJmb3JtYXQiOiJqcGVnIiwid2lkdGgiOjE5MjAsImhlaWdodCI6MTA4MH0=","binary":true}
← binary: [JPEG frame data]
```
Stream lifecycle:
1. Send `command` + `operate: "start_stream"` to start the stream
2. Server continuously receives `stream` type frames and forwards to the target
3. Send `command` + `operate: "stop_stream"` to stop the stream
4. Wildcard `action: "*"` + `operate: "stop_stream"` stops all streams

**Target offline handling**: If the stream target goes offline, the server sends back a `command` with `action: "*"` + `operate: "stop_stream"` to notify the sender to stop the stream.

### error — Error

```json
{
  "type": "error",
  "action": "error",
  "requestId": "1234567890",
  "data": "eyJtZXNzYWdlIjogIkRyaXZlciBlcnJvciJ9",
  "timestamp": 1700000000002
}
```
### event — Event

Event messages reported by the client (e.g., device status changes), forwarded by the server.

### query — Query

Query requests sent from the client to the server or other clients.

### system — System Message

## Device Registration

After connecting, the client registers device information via a `session/claim` message:

```json
{
  "type": "session",
  "action": "claim",
  "data": {
    "device": {
      "type": "client",
      "deviceName": "my-pc",
      "deviceInfo": "",
      "platform": "Windows-11-...",
      "machine": "AMD64",
      "appVersion": "v0.1.0",
      "screenResolution": "1920x1080"
    }
  }
}
```
| `type` Value | Description |
|--------------|-------------|
| `client` | Device side, provides local driver operations |
| `mod` | Logic side, handles business logic |

**Registration Behavior:**

| Behavior | Description |
|----------|-------------|
| Device Identifier | Server generates full identifier: `{type}:{userId}:{deviceName}` |
| Empty Name | Server auto-generates a UUID as `deviceName` |
| Name Conflict | When a device with the same name is already online, the new connection is rejected and receives an error |
| Disconnect Cleanup | Server auto-clears registration info when connection drops |
| IP Recording | Server auto-records the client's IP in device info |
