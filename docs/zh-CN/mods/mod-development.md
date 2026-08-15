# Mod 开发指南

## 概述

Mod 是 SmartPlayBuddy 的**逻辑扩展端**。与 Client（设备端）不同，Mod 不直接控制本地硬件，而是作为独立的业务逻辑节点连接到服务端，接收消息并执行自定义处理逻辑。

**典型应用场景：**
- 自动化脚本（如定时任务、条件触发操作）
- 消息转发与过滤
- 游戏辅助逻辑
- 多设备协调控制

## 架构

```
┌──────────────────┐
│     服务端        │
│  消息路由 / 认证  │
└────────┬─────────┘
         │ WebSocket
    ┌────┴────┐
    │         │
┌───┴───┐ ┌──┴────┐
│Client │ │  Mod  │
│设备端 │ │逻辑端 │
│键鼠屏幕│ │自定义 │
└───────┘ │逻辑   │
          └───────┘
```

- Mod 与 Client 共用同一套 WebSocket 连接框架（`Connector`）
- Mod 以 `type: "mod"` 注册到服务端
- 服务端可将 Client 的消息路由到 Mod 处理

## 快速开始

### 最简 Mod

```python
from smartplaybuddy.mod import Mod


class MyMod(Mod):
    async def main(self, msg) -> None:
        """处理接收到的每条消息。"""
        print(f"收到消息: type={msg.Type}, action={msg.Action}")


if __name__ == "__main__":
    from smartplaybuddy.mod import main
    main(MyMod)
```

### 运行

```bash
python my_mod.py
```

Mod 启动后会：
1. 自动登录（JWT 认证）
2. 以 `type: "mod"` 身份连接服务端
3. 进入消息循环，每条消息调用 `main()` 处理

## Mod 基类

```python
class Mod(ws.Connector):
    """Mod 基类，开发者继承并实现 main() 方法处理消息。"""

    async def main(self, msg) -> None:
        """
        处理接收到的消息。每条消息都会调用此方法。

        Args:
            msg: Message 对象，包含以下字段：
                - Type: 消息类型 (command/response/stream/error/system)
                - Action: 操作动作
                - From: 发送方标识
                - To: 目标方标识
                - RequestID: 请求 ID
                - Data: 业务数据（已自动解码）
                - Timestamp: 时间戳
        """
        pass
```

## 可用接口

Mod 继承了 `Connector` 的所有能力：

### 发送消息

向指定设备发送消息时，`To` 字段需填写目标设备的完整路由标识（`{type}:{userId}:{deviceName}`）：

```python
from smartplaybuddy.mod import Mod


class MyMod(Mod):
    async def main(self, msg) -> None:
        # 向指定 Client 发送键盘指令
        await self.conn.send(self.Message(
            Type="command",
            Action="keyboard",
            To="client:123:my-pc",
            Data={
                "operate": "tap",
                "key": "a",
            },
        ).to_json())

        # 发送错误响应
        await self.Error.error("处理失败", To=msg.From, RequestID=msg.RequestID)
```

> **提示**：`msg.From` 已经是完整的路由标识格式，可直接用于 `To` 字段回复消息。

### 系统功能

```python
class MyMod(Mod):
    async def main(self, msg) -> None:
        # 心跳检测
        await self.System.ping()
```

### 连接生命周期

```python
class MyMod(Mod):

    def on_close(self):
        """连接断开时的回调，可用于清理资源。"""
        print("连接已断开")
```

## Message 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `Type` | str | 消息类型：`command` / `response` / `stream` / `error` / `system` |
| `Action` | str | 操作动作 |
| `From` | str \| None | 发送方标识 |
| `To` | str \| None | 目标方标识 |
| `RequestID` | str \| None | 请求 ID |
| `Data` | Any | 业务数据（已自动 Base64 解码和 JSON 解析） |
| `Timestamp` | int | 毫秒级时间戳 |
| `BinaryData` | bytes \| None | 二进制数据（与 text 帧配对后自动填充） |
| `Binary` | bool | 是否包含二进制数据 |

## 完整示例：消息转发 Mod

```python
from smartplaybuddy.mod import Mod


class ForwardMod(Mod):
    """将来自 A 设备的键盘指令转发到 B 设备。"""

    async def main(self, msg) -> None:
        if msg.Type == "command" and msg.Action == "keyboard":
            # msg.From 格式为 "client:123:A"，可直接用于回复
            # 转发到目标设备 "client:123:B"
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

## 注意事项

- Mod 和 Client 使用**相同的 JWT 账号**登录，但注册类型不同（`mod` vs `client`）
- 同一账号可以同时运行多个 Mod 实例
- `main()` 方法是异步的，支持 `await` 操作
- 消息中的 `Data` 字段已经过自动解码（Base64 → Dict | Str | None），可直接使用
- 如需发送二进制数据，参考 [数据格式](../data-format.md) 中的双帧协议
