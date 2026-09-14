# Mod 控制台接入

本教程面向第三方创作者，帮助你把自己的Mod网页控制台接入 SmartPlayBuddy 平台，
与后端服务进行双向实时通信。

你只需要做两件事：

1. 在你的页面里通过 ES Module 引入官方 SDK；
2. 用 `SmtplayWSBridge` 收发消息。

平台会把你的页面加载进一个 `iframe`，并负责在 SDK 与后端 WebSocket 之间转发数据。
你**不需要**关心握手、来源识别、Base64 编码、连接管理等任何底层细节，SDK 已全部封装。

---

## 一、运行模型

```
┌─────────────────────────────────────────────────────────┐
│  SmartPlayBuddy 平台页面                                   │
│                                                           │
│   ┌───────────────────┐        postMessage                │
│   │  你的 Mod (iframe) │ <──────────────────────────────> │
│   │  SmtplayWSBridge   │        （SDK 信封 + 标记）        │
│   └───────────────────┘                                   │
│            │                                              │
│            │ 平台桥接 (useWSBridge)                        │
│            ▼                                              │
│      WebSocket  ⇄  后端服务                                │
└─────────────────────────────────────────────────────────┘
```

- 你的页面运行在 `iframe` 中，`sandbox` 属性为 `allow-scripts allow-same-origin allow-forms`。
- 你调用 `send()` 发出的消息，会经平台转发到后端 WebSocket。
- 后端下发的消息，会经平台投递到你的 `recv()` 回调。
- **只有被平台嵌入时消息才能真正送达后端**；独立打开页面时 `send()` 的消息不会被任何人接收。

---

## 二、引入 SDK

SDK 以标准 ES Module 形式托管在平台的 `/sdk/` 路径下，并已开启 CORS，
因此**任意来源**的页面都可以直接 `import`，无需下载或打包到自己项目里。

```html
<script type="module">
  import { SmtplayWSBridge, Message } from 'http://smtplay.cabyss.cn:8080/sdk/SmtplayWSBridge.js'

  // 开始使用……
</script>
```

> 请将上面的域名/端口替换为你所在环境的平台地址。`Message` 也由该入口一并导出。

可用的两个模块：

| 模块 | 说明 |
| --- | --- |
| `SmtplayWSBridge.js` | 通信桥，负责握手、收发、二进制拆包 |
| `Message.js` | 消息数据结构，负责字段封装与 Base64 编解码 |

---

## 三、快速开始

```html
<script type="module">
  import { SmtplayWSBridge, Message } from 'http://smtplay.cabyss.cn:8080/sdk/SmtplayWSBridge.js'

  // 1. 创建桥（构造时会自动向平台握手）
  const smtplay = new SmtplayWSBridge()

  // 2. 判断是否被平台嵌入
  if (smtplay.is_embedded()) {
    console.log('已连接平台，可以收发消息')
  } else {
    console.log('当前独立运行，消息无法送达后端')
  }

  // 3. 注册接收回调
  smtplay.recv((msg) => {
    console.log('收到消息：', msg.type, msg.action, msg.data)
  })

  // 4. 发送一条消息
  const msg = new Message('system', 'ping', { text: 'hello' })
  smtplay.send(msg)
</script>
```

---

## 四、核心 API

### 4.1 `SmtplayWSBridge`

通信桥。**必须单例使用**——一个页面只应创建一个实例。

> 平台对每个页面只认一个会话标记，重复 `new` 会让前一个实例的标记立即失效、彻底失联。
> SDK 已做保护：重复创建会打印告警并返回原实例。

| 成员 | 类型 | 说明 |
| --- | --- | --- |
| `new SmtplayWSBridge()` | 构造函数 | 创建桥并自动向平台握手。握手早于任何 `send()`，无需手动处理时序 |
| `send(message)` | 方法 | 发送消息。参数为 `Message` 实例，或可被 `Message.fromRaw` 解析的对象/JSON 字符串 |
| `recv(fn)` | 方法 | 注册接收回调，回调参数为 `Message` 实例。返回一个「取消注册」的函数 |
| `is_embedded()` | 方法 | 是否被平台嵌入（`window.parent !== window`） |

**取消接收：**

```javascript
const off = smtplay.recv((msg) => { /* ... */ })
off() // 之后不再收到消息
```

### 4.2 `Message`

> 详情可参考[DataFormat](../DataFormat.md)

消息数据结构。

```javascript
new Message(type, action, data?, opts?)
```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | `string` | 是 | 消息类型 |
| `action` | `string` | 是 | 具体动作 |
| `data` | `any` | 否 | 业务数据。可以是对象、字符串、`Uint8Array`/`ArrayBuffer` 等，发送时自动 Base64 编码 |
| `opts` | `object` | 否 | 见下表 |

`opts` 可选字段：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `from` | `string` | `null` | 发送方标识 |
| `to` | `string` | `null` | 目标标识（如指定接收设备） |
| `requestId` | `string` | 雪花算法自动生成 | 请求 ID，不传则自动生成，可用于请求-响应配对 |
| `timestamp` | `number` | `Date.now()` | 毫秒时间戳，不传则自动填充 |
| `binary` | `boolean` | `false` | 标记本条消息后续跟随一个二进制帧 |

实例属性：

| 属性 | 说明 |
| --- | --- |
| `msg.data` | 业务数据。接收时已自动 Base64 解码（JSON 字符串会进一步解析为对象） |
| `msg.binaryData` | `ArrayBuffer`，仅当消息携带二进制帧时有值 |
| `msg.requestId` / `msg.timestamp` / `msg.from` / `msg.to` | 同构造参数 |

**静态方法与序列化：**

| 方法 | 说明 |
| --- | --- |
| `Message.fromRaw(raw)` | 从原始 JSON（字符串或已解析对象）反序列化为 `Message`，`data` 自动解码 |
| `msg.toJSON()` | 序列化为线格式普通对象，`data` 自动 Base64 编码 |

---

## 五、发送消息

### 5.1 结构化发送（推荐）

```javascript
// data 可以是对象
smtplay.send(new Message('game', 'start', { level: 1, mode: 'coop' }))

// 也可以是纯文本
smtplay.send(new Message('system', 'ping', 'hello'))

// 指定目标
smtplay.send(new Message('game', 'sync', { pos: [1, 2] }, { to: 'device-001' }))
```

`data` 无需手动编码，SDK 会自动 Base64。接收方拿到的 `msg.data` 也已自动解码。

### 5.2 从 JSON 字符串发送

```javascript
const raw = '{"type":"system","action":"ping","data":{"text":"hi"}}'
smtplay.send(Message.fromRaw(raw))
```

> 注意：`Message.fromRaw` 会把 `data` 当作已编码的线格式处理。日常业务请优先用 `new Message(...)` 构造，避免手动 Base64。

---

## 六、接收消息

```javascript
smtplay.recv((msg) => {
  console.log(`[${msg.requestId}] ${msg.type}/${msg.action}`)
  console.log('data:', msg.data)          // 已自动解码
  console.log('ts:', msg.timestamp)

  if (msg.binaryData) {
    console.log('binary:', msg.binaryData.byteLength, 'bytes')
  }
})
```

SDK 只会把**属于本会话**的消息投递给你；iframe 内浏览器扩展注入脚本
（沉浸式翻译、Vue DevTools、钱包类等）产生的 `postMessage` 噪音已被自动过滤，无需你判断来源。

---

## 七、二进制数据

需要传输图片、音频、二进制块等大数据时，使用 `binary` 标记 + `binaryData`。

**发送二进制：**

```javascript
const buffer = new Uint8Array([1, 2, 3, 4]).buffer // ArrayBuffer

const msg = new Message('asset', 'upload', { name: 'sprite.png' }, { binary: true })
msg.binaryData = buffer

smtplay.send(msg)
```

SDK 会先发文本帧（元数据），再紧跟一个二进制帧，二者由平台按序转发。

**接收二进制：**

当 `msg.binary` 为真时，SDK 会等待随后的二进制帧到齐后，
把 `ArrayBuffer` 挂到 `msg.binaryData` 上，再触发一次 `recv` 回调：

```javascript
smtplay.recv((msg) => {
  if (msg.binaryData) {
    // msg.data 是元数据，msg.binaryData 是二进制内容
    const blob = new Blob([msg.binaryData])
    console.log('收到二进制资产：', msg.data.name, blob.size)
  }
})
```

---

## 八、请求-响应配对

利用 `requestId` 关联请求与响应：

```javascript
function request(type, action, data) {
  return new Promise((resolve) => {
    const req = new Message(type, action, data)
    const off = smtplay.recv((msg) => {
      if (msg.requestId === req.requestId) {
        off()
        resolve(msg)
      }
    })
    smtplay.send(req)
  })
}

// 使用
const resp = await request('game', 'queryState', { id: 42 })
console.log('响应数据：', resp.data)
```

> `requestId` 由雪花算法自动生成、全局唯一，你也可以在 `opts.requestId` 中自定义。

---

## 九、完整示例

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>My Mod</title>
</head>
<body>
  <div id="status">初始化中…</div>
  <button id="sendBtn">发送 ping</button>
  <ul id="log"></ul>

  <script type="module">
    import { SmtplayWSBridge, Message } from 'http://smtplay.cabyss.cn:8080/sdk/SmtplayWSBridge.js'

    const smtplay = new SmtplayWSBridge()
    const statusEl = document.getElementById('status')
    const logEl = document.getElementById('log')

    statusEl.textContent = smtplay.is_embedded() ? '已连接平台' : '独立运行（消息不会送达）'

    function log(text) {
      const li = document.createElement('li')
      li.textContent = new Date().toLocaleTimeString() + '  ' + text
      logEl.appendChild(li)
    }

    smtplay.recv((msg) => {
      log(`↓ ${msg.type}/${msg.action}  data=${JSON.stringify(msg.data)}`)
    })

    document.getElementById('sendBtn').addEventListener('click', () => {
      const msg = new Message('system', 'ping')
      smtplay.send(msg)
      log(`↑ ${msg.type}/${msg.action}  [${msg.requestId}]`)
    })
  </script>
</body>
</html>
```

---

## 十、常见问题（FAQ）

**Q1：消息发出去后端收不到？**
- 确认页面是被平台以 `iframe` 嵌入的（`is_embedded()` 返回 `true`）。独立打开时消息无处可去。
- 确认平台侧连接状态为「已连接」。

**Q2：`import` 报跨域/加载失败？**
- SDK 已开启 CORS，请检查引入地址是否为正确的平台 `/sdk/` 路径，且使用 `type="module"`。

**Q3：可以创建多个 `SmtplayWSBridge` 吗？**
- 不可以。一个页面一个实例，重复创建会返回原实例并告警。

**Q4：`data` 需要自己 Base64 吗？**
- 不需要。`send` 时 SDK 自动编码，`recv` 时自动解码。你始终操作原始数据。

**Q5：`type` / `action` 有哪些取值？**
- 由你与后端服务约定。SDK 不限制取值，透传即可。

---

## 十一、接入清单

- [ ] 页面使用 `type="module"` 引入 `SmtplayWSBridge.js`
- [ ] 全局只创建一个 `SmtplayWSBridge` 实例
- [ ] 通过 `is_embedded()` 判断运行环境并给出提示
- [ ] 用 `recv()` 注册接收回调
- [ ] 用 `new Message(type, action, data)` + `send()` 发送消息
- [ ] 如需传输大数据，使用 `binary` + `binaryData`
- [ ] 与后端约定好 `type` / `action` 语义

完成以上步骤，你的 Mod 即可与 SmartPlayBuddy 平台正常通信。祝创作顺利！
