# 数据格式

## 概述

客户端与服务端通过 WebSocket 进行通信，认证通过连接时 HTTP Headers 携带 JWT 完成。

所有消息使用统一结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ☑ | 消息类型：`command` / `response` / `system` |
| action | string | ☑ | 操作动作 |
| from | string | ☐ | 发送方标识（服务端自动填充） |
| to | string | ☐ | 目标方标识（客户端间消息使用） |
| requestId | string | ☐ | 请求ID（UUID），由客户端生成，响应时原样返回 |
| data | string | ☐ | 业务数据，Base64 编码的 JSON 字符串 |
| timestamp | int64 | ☑ | 毫秒级时间戳 |

## 客户端操作（command）

客户端向服务端发送业务指令时，`type` 固定为 `command`。

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| type | ☑ | 固定为 `command` |
| action | ☑ | 操作动作，如 `addDevice`、`operateDevice`、`removeDevice` |
| requestId | ☑ | 请求唯一ID，由客户端生成（UUID） |
| data | ☑ | Base64 编码的业务数据 |
| timestamp | ☑ | 当前时间戳（毫秒） |
