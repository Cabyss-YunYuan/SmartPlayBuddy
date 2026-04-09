# 数据格式

## 客户端认证

### 说明

客户端在 WebSocket 连接建立后，发送的第一条消息必须为认证请求。服务端在收到合法认证信息前，不会处理任何其他业务消息。

### 请求参数
| 参数名           | 必填 | 类型     | 备注    |
|---------------|----|--------|-------|
| url           | ☐  | string | 服务器地址 |
| user          | ☑  | json   | 用户信息  |
| user.username | ☑  | string | 用户名   |
| user.password | ☑  | string | 密码    |
| user.type     | ☑  | string | 设备类型  |
| user.device   | ☑  | string | 设备名称  |
| user.lang     | ☐  | string | 语言    |

### 响应示例
```json
{"code":2001,"message":"登录成功","data":{"uid":"10001","nickname":"testuser"},"timestamp":1775539321}
```

## 客户端操作

### 请求参数

| 参数名       | 必填 | 类型     | 备注             |
|-----------|----|--------|----------------|
| requestId | ☑  | string | 请求ID（系统生成）     |
| type      | ☑  | string | 操作类型（business） |
| workspace | ☑  | string | 工作空间           |
| action    | ☐  | string | 操作             |
| data      | ☐  | json   | 数据             |
| timestamp | ☑  | int    | 时间戳（系统生成）      |
