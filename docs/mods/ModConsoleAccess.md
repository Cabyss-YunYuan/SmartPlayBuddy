# Mod Console Access

This tutorial is for third-party creators. It helps you integrate your Mod's web console with the SmartPlayBuddy platform and communicate bidirectionally with the backend service in real time.

You only need to do two things:

1. Import the official SDK into your page via ES Module;
2. Use `ModBridge` to send and receive messages.

The platform will load your page into an `iframe` and handle forwarding data between the SDK and the backend WebSocket. You **do not** need to worry about handshaking, origin identification, Base64 encoding, connection management, or any other low-level details—the SDK encapsulates all of it.

---

## 1. Runtime Model

```
┌───────────────────────────────────────────────────────────┐
│  SmartPlayBuddy Platform Page                             │
│                                                           │
│   ┌───────────────────┐        postMessage                │
│   │ Your Mod (iframe) │ <──────────────────────────────>  │
│   │ ModBridge          │        (Envelope + nonce marker)  │
│   └───────────────────┘                                   │
│            │                                              │
│            │ Platform bridge                              │
│            ▼                                              │
│      WebSocket  ⇄  Backend Service                        │
└───────────────────────────────────────────────────────────┘
```

- Your page runs inside an `iframe` with the `sandbox` attribute set to `allow-scripts allow-same-origin allow-forms`.
- Messages you send via `send()` are forwarded by the platform to the backend WebSocket.
- Messages pushed down by the backend are delivered to your `recv()` callback via the platform.
- **Messages can only actually reach the backend when the page is embedded by the platform**; when the page is opened standalone, messages sent via `send()` will not be received by anyone.

---

## 2. Importing the SDK

The SDK is hosted as a standard ES Module under the platform's `/sdk/` path with CORS enabled, so pages from **any origin** can `import` it directly—no need to download or bundle it into your own project.

```html
<script type="module">
  import { ModBridge, Message } from 'https://smtplay.cabyss.cn/sdk/ModBridge.js'

  // Start using...
</script>
```

> Replace the domain above with your platform address for your environment. `Message` is also exported from the same entry point.

Two modules are available:

| Module | Description |
| --- | --- |
| `ModBridge.js` | Communication bridge, handles handshaking, sending/receiving, binary frame splitting |
| `Message.js` | Message data structure, handles field encapsulation and Base64 encoding/decoding |

---

## 3. Quick Start

```html
<script type="module">
  import { ModBridge, Message } from 'https://smtplay.cabyss.cn/sdk/ModBridge.js'

  // 1. Create the bridge (automatically handshakes with the platform on construction)
  const smtplay = new ModBridge()

  // 2. Check whether embedded by the platform
  if (smtplay.is_embedded()) {
    console.log('Connected to platform, ready to send and receive messages')
  } else {
    console.log('Currently running standalone, messages cannot reach the backend')
  }

  // 3. Register a receive callback
  smtplay.recv((msg) => {
    console.log('Message received:', msg.type, msg.action, msg.data)
  })

  // 4. Send a message
  const msg = new Message('system', 'ping', { text: 'hello' })
  smtplay.send(msg)
</script>
```

---

## 4. Core API

### 4.1 `ModBridge`

The communication bridge. **Must be used as a singleton**—only one instance should be created per page.

> The platform recognizes only one nonce marker per page. Creating a duplicate `new` instance will immediately invalidate the previous instance's marker and completely disconnect it. The SDK already includes protection: duplicate creation will print a warning and return the original instance.

| Member | Type | Description |
| --- | --- | --- |
| `new ModBridge()` | Constructor | Creates the bridge and automatically handshakes with the platform. The handshake occurs before any `send()`, so no manual timing handling is needed |
| `send(message)` | Method | Sends a message. The argument is a `Message` instance, or an object/JSON string that can be parsed by `Message.fromRaw` |
| `recv(fn)` | Method | Registers a receive callback. The callback argument is a `Message` instance. Returns an "unregister" function |
| `is_embedded()` | Method | Whether embedded by the platform (`window.parent !== window`) |
| `targetDevice` | Property | Current target device address (e.g., `client:123:ROG-Strix-G614JV`). Automatically updated by the SDK when the platform pushes a selection. `null` when not set |

**Unregistering a receiver:**

```javascript
const off = smtplay.recv((msg) => { /* ... */ })
off() // No longer receives messages afterward
```

### 4.2 `Message`

> For details, refer to [DataFormat](../DataFormat.md)

Message data structure.

```javascript
new Message(type, action, data?, opts?)
```

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `type` | `string` | Yes | Message type |
| `action` | `string` | Yes | Specific action |
| `data` | `any` | No | Business data. Can be an object, string, `Uint8Array`/`ArrayBuffer`, etc. Automatically Base64-encoded on send |
| `opts` | `object` | No | See table below |

Optional `opts` fields:

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `from` | `string` | `null` | Sender identifier |
| `to` | `string` | `null` | Target identifier (e.g., specify receiving device) |
| `requestId` | `string` | Auto-generated by Snowflake algorithm | Request ID. If not provided, auto-generated. Can be used for request-response pairing |
| `timestamp` | `number` | `Date.now()` | Millisecond timestamp. If not provided, auto-filled |
| `binary` | `boolean` | `false` | Marks that this message is followed by a binary frame |

Instance properties:

| Property | Description |
| --- | --- |
| `msg.type` / `msg.action` | Message type and action |
| `msg.data` | Business data. Automatically Base64-decoded on receive (JSON strings are further parsed into objects) |
| `msg.binaryData` | `ArrayBuffer`, only has a value when the message carries a binary frame |
| `msg.requestId` / `msg.timestamp` / `msg.from` / `msg.to` | Same as constructor parameters |

**Static methods and serialization:**

| Method | Description |
| --- | --- |
| `Message.fromRaw(raw)` | Deserializes from raw JSON (string or parsed object) into a `Message`. `data` is automatically decoded |
| `msg.toJSON()` | Serializes to a wire-format plain object. `data` is automatically Base64-encoded |

---

## 5. Sending Messages

### 5.1 Structured Sending (Recommended)

```javascript
// data can be an object
smtplay.send(new Message('game', 'start', { level: 1, mode: 'coop' }))

// It can also be plain text
smtplay.send(new Message('system', 'ping', 'hello'))

// Specify a target
smtplay.send(new Message('game', 'sync', { pos: [1, 2] }, { to: 'mod:123:device-001' }))
```

`data` does not need manual encoding—the SDK automatically Base64-encodes it. The receiver's `msg.data` is also automatically decoded.

### 5.2 Sending from a JSON String

```javascript
const raw = '{"type":"system","action":"ping","data":{"text":"hi"}}'
smtplay.send(Message.fromRaw(raw))
```

> Note: `Message.fromRaw` treats `data` as already in encoded wire format. For everyday business use, prefer constructing with `new Message(...)` to avoid manual Base64.

---

## 6. Receiving Messages

```javascript
smtplay.recv((msg) => {
  console.log(`[${msg.requestId}] ${msg.type}/${msg.action}`)
  console.log('data:', msg.data)          // Already automatically decoded
  console.log('ts:', msg.timestamp)

  if (msg.binaryData) {
    console.log('binary:', msg.binaryData.byteLength, 'bytes')
  }
})
```

The SDK only delivers messages **belonging to this session** to you. `postMessage` noise generated by browser extension injection scripts inside the iframe (Immersive Translate, Vue DevTools, wallet-type extensions, etc.) is automatically filtered out—you don't need to check the origin yourself.

---

## 7. Platform Messages & Target Device

### 7.1 Target Device (`targetDevice`)

When the user selects a target device in the platform console, the platform pushes the device address to the mod. The SDK automatically updates the `smtplay.targetDevice` property and also triggers the `recv` callback.

**Reading the current target device:**

```javascript
// Read at any time, no need to wait for a message
const target = smtplay.targetDevice
if (target) {
  console.log('Current target device:', target) // e.g., "client:123:ROG-Strix-G614JV"
} else {
  console.log('No target device set')
}
```

**Listening for target device changes:**

```javascript
smtplay.recv((msg) => {
  if (msg.type === 'system' && msg.action === 'target_device') {
    const addr = smtplay.targetDevice // SDK has already updated it
    if (addr) {
      console.log('Target device set:', addr)
    } else {
      console.log('Target device cleared')
    }
    return
  }

  // ... handle other WS messages
})
```

> The `targetDevice` value format is `{type}:{userId}:{deviceName}`, which can be used directly as the `to` field in messages.
> The user can manually clear the target device in the platform console, in which case `targetDevice` becomes `null`.

---

## 8. Binary Data

When you need to transmit large data such as images, audio, or binary chunks, use the `binary` flag + `binaryData`.

**Sending binary:**

```javascript
const buffer = new Uint8Array([1, 2, 3, 4]).buffer // ArrayBuffer

const msg = new Message('asset', 'upload', { name: 'sprite.png' }, { binary: true })
msg.binaryData = buffer

smtplay.send(msg)
```

The SDK will first send a text frame (metadata), immediately followed by a binary frame. Both are forwarded in order by the platform.

**Receiving binary:**

When `msg.binary` is true, the SDK waits for the subsequent binary frame to arrive, attaches the `ArrayBuffer` to `msg.binaryData`, and then triggers the `recv` callback once:

```javascript
smtplay.recv((msg) => {
  if (msg.binaryData) {
    // msg.data is metadata, msg.binaryData is the binary content
    const blob = new Blob([msg.binaryData])
    console.log('Binary asset received:', msg.data.name, blob.size)
  }
})
```

---

## 9. Request-Response Pairing

Use `requestId` to associate requests with responses:

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

// Usage
const resp = await request('game', 'queryState', { id: 42 })
console.log('Response data:', resp.data)
```

> `requestId` is automatically generated by the Snowflake algorithm and is globally unique. You can also customize it in `opts.requestId`.

---

## 10. Complete Example

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>My Mod</title>
</head>
<body>
  <div id="status">Initializing…</div>
  <div id="target">Target device: none</div>
  <button id="sendBtn">Send ping</button>
  <ul id="log"></ul>

  <script type="module">
    import { ModBridge, Message } from 'https://smtplay.cabyss.cn/sdk/ModBridge.js'

    const smtplay = new ModBridge()
    const statusEl = document.getElementById('status')
    const targetEl = document.getElementById('target')
    const logEl = document.getElementById('log')

    statusEl.textContent = smtplay.is_embedded() ? 'Connected to platform' : 'Running standalone (messages will not be delivered)'

    function log(text) {
      const li = document.createElement('li')
      li.textContent = new Date().toLocaleTimeString() + '  ' + text
      logEl.appendChild(li)
    }

    smtplay.recv((msg) => {
      if (msg.type === 'system' && msg.action === 'target_device') {
        const addr = smtplay.targetDevice
        targetEl.textContent = 'Target device: ' + (addr || 'none')
        log(`🎯 Target device changed: ${addr || 'cleared'}`)
        return
      }
      log(`↓ ${msg.type}/${msg.action}  data=${JSON.stringify(msg.data)}`)
    })

    document.getElementById('sendBtn').addEventListener('click', () => {
      const to = smtplay.targetDevice || undefined
      const msg = new Message('system', 'ping', null, { to })
      smtplay.send(msg)
      log(`↑ ${msg.type}/${msg.action}  [${msg.requestId}]`)
    })
  </script>
</body>
</html>
```

---

## 11. FAQ

**Q1: Messages are sent but the backend doesn't receive them?**
- Confirm the page is embedded by the platform as an `iframe` (`is_embedded()` returns `true`). When opened standalone, messages have nowhere to go.
- Confirm the platform-side connection status is "Connected".

**Q2: `import` reports a CORS/loading failure?**
- The SDK has CORS enabled. Check that the import address is the correct platform `/sdk/` path and that `type="module"` is used.

**Q3: Can I create multiple `ModBridge` instances?**
- No. One instance per page. Duplicate creation returns the original instance and logs a warning.

**Q4: Do I need to Base64-encode `data` myself?**
- No. The SDK automatically encodes on `send` and automatically decodes on `recv`. You always work with the original data.

**Q5: What values can `type` / `action` take?**
- As agreed between you and the backend service. The SDK does not restrict the values—it passes them through as-is.

**Q6: How is `targetDevice` set?**
- It is pushed by the platform console and automatically updated by the SDK. You only need to read `smtplay.targetDevice`—no manual setup required.

---

## 12. Integration Checklist

- [ ] The page imports `ModBridge.js` using `type="module"`
- [ ] Only one `ModBridge` instance is created globally
- [ ] Use `is_embedded()` to determine the runtime environment and provide a hint
- [ ] Register a receive callback with `recv()`
- [ ] Send messages with `new Message(type, action, data)` + `send()`
- [ ] For large data transmission, use `binary` + `binaryData`
- [ ] Agree on `type` / `action` semantics with the backend
- [ ] To send messages to a specific device, use `smtplay.targetDevice` to get the platform-pushed target address

Once the above steps are complete, your Mod can communicate normally with the SmartPlayBuddy platform. Happy creating!
