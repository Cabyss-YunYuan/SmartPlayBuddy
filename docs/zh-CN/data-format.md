# 数据格式

## 概述

客户端与服务端通过 **WebSocket** 进行通信，认证通过连接时 HTTP Headers 携带 JWT 完成。

通信采用 **text + binary 双帧协议**：当消息包含二进制数据时，先发一个 text 帧（JSON 元数据，标记 `binary: true`），紧跟一个 binary 帧（原始二进制数据）。

## 消息结构

所有消息使用统一的 JSON 结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✅ | 消息类型：`command` / `response` / `stream` / `error` / `system` / `event` / `query` |
| action | string | ✅ | 操作动作（如 `keyboard`、`mouse`、`screen`） |
| from | string | ☐ | 发送方标识（**服务端自动填充**，格式：`{type}:{userId}:{deviceName}`） |
| to | string | ☐ | 目标方标识（**路由关键字段**，格式：`{type}:{userId}:{deviceName}`） |
| requestId | string | ☐ | 请求唯一 ID（雪花算法），响应时原样返回 |
| data | string | ☐ | 业务数据，Base64 编码的 JSON 字符串 |
| binary | bool | ☐ | 为 `true` 时表示后续有一个 binary 帧携带二进制数据 |
| timestamp | int64 | ✅ | 毫秒级时间戳 |

### 消息路由

服务端根据 `to` 字段进行消息路由：

1. 客户端发送消息时，`to` 填写目标设备的完整标识（如 `client:123:my-pc`）
2. 服务端自动填充 `from` 为发送方的标识（如 `mod:123:my-mod`）
3. 转发前清空 `to` 字段，推送到目标连接

**标识格式**：`{deviceType}:{userId}:{deviceName}`

| 部分 | 说明 |
|------|------|
| `deviceType` | `client`（设备端）或 `mod`（逻辑端） |
| `userId` | 用户 ID（由 JWT 认证确定） |
| `deviceName` | 设备名称（claim 时指定，为空则服务端自动生成 UUID） |

> **注意**：同一用户下同名设备只允许一个在线连接。新连接 claim 同名设备时，若已有会话存在，服务端将拒绝新连接。

### Data 字段编解码

- **发送时**：`data` 字段内容为 JSON → UTF-8 字节 → Base64 字符串
- **接收时**：Base64 解码 → 尝试 JSON 解析 → 失败则保留原始字符串

### 二进制数据

当消息需要携带二进制数据（如截图）时：

1. 发送 text 帧：JSON 中 `data` 包含元数据（如格式、分辨率），并在 `data` 对象内标记 `__binary__: true`，同时顶层设置 `binary: true`
2. 紧随其后发送 binary 帧：原始二进制数据

接收端会将两个帧自动配对还原。

## 消息类型

### command — 指令

客户端向服务端发送业务指令，或服务端向客户端下发操作命令。

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

`data` 解码后为：`{"operate": "tap", "key": "a"}`

### response — 响应

对 `command` 的响应，携带相同 `requestId`。

**普通响应：**

```json
{
  "type": "response",
  "action": "keyboard",
  "requestId": "1234567890",
  "data": "eyJzdGF0dXMiOiAib2siLCAicmVzdWx0IjogbnVsbH0=",
  "timestamp": 1700000000001
}
```

**带二进制数据的响应（如截图）：**

```
← text:  {"type":"response","action":"screen","requestId":"xxx","binary":true,"data":"eyJfX2JpbmFyeV9fIjp0cnVlLCJmb3JtYXQiOiJqcGVnIiwid2lkdGgiOjE5MjAsImhlaWdodCI6MTA4MH0="}
← binary: [JPEG 二进制数据]
```
### stream — 数据流

流式传输消息，用于屏幕捕获等高频场景。结构与 `response` 类似，但 `type` 为 `stream`。

```
← text:  {"type":"stream","action":"screen","data":"eyJfX2JpbmFyeV9fIjp0cnVlLCJmb3JtYXQiOiJqcGVnIiwid2lkdGgiOjE5MjAsImhlaWdodCI6MTA4MH0=","binary":true}
← binary: [JPEG 帧数据]
```

流的生命周期：
1. 发送 `command` + `operate: "start_stream"` 启动流
2. 服务端持续收到 `stream` 类型的帧并转发到目标
3. 发送 `command` + `operate: "stop_stream"` 停止流
4. 通配符 `action: "*"` + `operate: "stop_stream"` 可停止所有流

**目标离线处理**：若流的目标设备离线，服务端会向发送方回传 `command` + `action: "*"` + `operate: "stop_stream"` 通知停止流。

### error — 错误

```json
{
  "type": "error",
  "action": "error",
  "requestId": "1234567890",
  "data": "eyJtZXNzYWdlIjogIkRyaXZlciBlcnJvciJ9",
  "timestamp": 1700000000002
}
```

### event — 事件

客户端上报的事件消息（如设备状态变更等），服务端负责转发。

### query — 查询

客户端向服务端或其他客户端发起的查询请求。

### system — 系统消息

## 设备注册

客户端连接后通过 `session/claim` 消息向服务端注册设备信息：

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

| `type` 值 | 说明 |
|-----------|------|
| `client` | 设备端，提供本地驱动操作 |
| `mod` | 逻辑端，处理业务逻辑 |

**注册行为：**

| 行为 | 说明 |
|------|------|
| 设备标识 | 服务端生成完整标识：`{type}:{userId}:{deviceName}` |
| 名称为空 | 服务端自动生成 UUID 作为 `deviceName` |
| 名称冲突 | 同名设备已在线时，新连接被拒绝并收到 error |
| 断开清理 | 连接断开时服务端自动清除注册信息 |
| IP 记录 | 服务端自动记录客户端 IP 到设备信息中 |
