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

        注意：system 和 session 类型消息由框架装饰器在到达 main() 前处理完毕，
        你不会在 main() 中收到它们。

        Args:
            msg: Message 对象，包含以下字段：
                - Type: 消息类型 (command/response/stream/error/request/event/system/session)
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
        await self.send(self.Message(
            Type="command",
            Action="keyboard",
            To="client:123:my-pc",
            Data={
                "operate": "tap",
                "key": "a",
            },
        ))

        # 发送错误响应
        await self.Error.error("处理失败", To=msg.From, RequestID=msg.RequestID)
```

> **提示**：`send()` 直接接受 `Message` 对象。

> **提示**：`msg.From` 已经是完整的路由标识格式，可直接用于 `To` 字段回复消息。

### 跨用户授权（`permit`）

当你的 Mod 需要向**其他用户**的 Client 发送指令（跨 UID）时，必须先获得授权。`permit()` 上下文管理器自动处理完整的授权生命周期：

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
    await mod.wait_ready()  # 等待连接建立

    # 请求授权 → 等待审批 → 激活事件锁
    async with mod.permit(TARGET, "键盘测试") as rid:
        # 携带授权 RID 发送指令
        await mod.send(mod.Message(
            Type="command", Action="keyboard", To=TARGET, RequestID=rid,
            Data={"operate": "tap", "key": "a"},
        ))

    # 退出 async with 自动释放事件锁
```

> **注意**：使用 `await mod.wait_ready()` 等待连接完全建立（WebSocket 握手 + claim 完成）。不要使用 `await mod.connection`——它包装了无限重连循环，`await` 会永远阻塞。

**`permit()` 的工作流程：**

1. 向目标 Client 发送 `request/permit` 请求
2. 目标用户看到授权提示，选择批准或拒绝
3. 批准后，发送 `event/activate` 激活事件锁
4. 返回的 `rid`（Request ID）即为授权凭证——后续所有指令都必须携带它
5. 当 `async with` 块退出（正常退出或异常），自动发送 `event/release` 释放锁

> **注意**：向**同用户**（相同 UID）发送指令不需要 `permit()`，仅跨 UID 操作需要授权。

### 系统功能

```python
class MyMod(Mod):
    async def main(self, msg) -> None:
        # 心跳检测
        await self.System.ping()
```

> **注意**：`ping`/`pong` 心跳由框架装饰器自动处理，通常无需手动调用。

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
| `Type` | str | 消息类型：`command` / `response` / `stream` / `error` / `request` / `event` / `system` / `session` |
| `Action` | str | 操作动作 |
| `From` | str \| None | 发送方标识 |
| `To` | str \| None | 目标方标识 |
| `RequestID` | str \| None | 请求 ID |
| `Data` | Any | 业务数据（已自动 Base64 解码和 JSON 解析） |
| `Timestamp` | int | 毫秒级时间戳 |
| `BinaryData` | bytes \| None | 二进制数据（与 text 帧配对后自动填充） |
| `Binary` | bool | 是否包含二进制数据 |

> **注意**：`system` 和 `session` 类型消息由框架装饰器（`system_dispatch`、`session_dispatch`）拦截处理，不会到达 `main()`。

## 完整示例：消息转发 Mod

```python
from smartplaybuddy.mod import Mod


class ForwardMod(Mod):
    """将来自 A 设备的键盘指令转发到 B 设备。"""

    async def main(self, msg) -> None:
        if msg.Type == "command" and msg.Action == "keyboard":
            # msg.From 格式为 "client:123:A"，可直接用于回复
            # 转发到目标设备 "client:123:B"
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

## 注意事项

- Mod 和 Client 使用**相同的 JWT 账号**登录，但注册类型不同（`mod` vs `client`）
- 同一账号可以同时运行多个 Mod 实例
- `main()` 方法是异步的，支持 `await` 操作
- 消息中的 `Data` 字段已经过自动解码（Base64 → Dict | Str | None），可直接使用
- 如需发送二进制数据，参考 [数据格式](../DataFormat.md) 中的双帧协议
- `system` 和 `session` 消息由框架装饰器在到达 `main()` 前处理完毕

## 控制台接入
详见[Mod 控制台接入](ModConsoleAccess.md)。
