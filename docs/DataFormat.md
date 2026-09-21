# Data Format

## Overview

The client and server communicate via **WebSocket**, with authentication handled through JWT in the HTTP Headers during connection.

Communication uses the **text + binary dual-frame protocol**: when a message contains binary data, a text frame is sent first (JSON metadata, marked with `binary: true`), followed immediately by a binary frame (raw binary data).

## Message Structure

All messages use a unified JSON structure:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| type | string | ✅ | Message type: `command` / `response` / `stream` / `error` / `request` / `event` / `query` / `system` / `session` |
| action | string | ✅ | Operation action (e.g., `keyboard`, `mouse`, `screen`) |
| from | string | ☐ | Sender identifier (**auto-filled by server**, format: `{type}:{userId}:{deviceName}`) |
| to | string | ☐ | Target identifier (**key routing field**, format: `{type}:{userId}:{deviceName}`) |
| requestId | string | ☐ | Unique request ID (snowflake algorithm), returned as-is in responses |
| data | string | ☐ | Business data, Base64-encoded JSON string |
| binary | bool | ☐ | When `true`, indicates a binary frame follows |
| timestamp | int64 | ☐ | Millisecond timestamp |

### Message Routing

The server routes messages based on the `to` field:

1. When sending a message, the client sets `to` to the target device's full identifier (e.g., `client:123:my-pc`)
2. The server auto-fills `from` with the sender's identifier (e.g., `mod:123:my-mod`)
3. If sending a message to a web client session, `to` should be filled with the complete identifier of the target device (e.g., `client:123:my-pc:session`).

**Identifier Format**: `{deviceType}:{userId}:{deviceName}:{session}`

| Part | Description |
|------|-------------|
| `deviceType` | `client` (device side) or `mod` (logic side) |
| `userId` | User ID (determined by JWT authentication) |
| `deviceName` | Device name (specified during claim; auto-generated UUID if empty) |
| `session` | Session identifier (optional) |

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
  "to": "client:123:my-pc",
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
1. Send `command` + `operate: "start-stream"` to start the stream
2. Server continuously receives `stream` type frames and forwards to the target
3. Send `command` + `action: "stop-stream"` + `data: {"stream_id": "xxx"}` to stop a specific stream
4. Send `command` + `action: "stop-stream"` + `data: {"stream_id": "*"}` to stop all streams

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

### request — Request

Request messages sent to another party that expect a response (e.g., authorization requests).

### event — Event

Event messages reported by the client (e.g., device status changes, authorization activation/release).

### query — Query

Read-only query messages from client to server or other clients (e.g., querying active streams). Unlike `command`, queries do not modify state.

### system — System Message

System-level protocol messages such as `ping`/`pong` for keepalive and `speed_test` for bandwidth measurement. Handled automatically by the framework decorator before reaching business logic.

### session — Session Management

Session management messages for device claim and status queries (e.g., `session/claim`, `session/status`). Handled automatically by the framework decorator.
