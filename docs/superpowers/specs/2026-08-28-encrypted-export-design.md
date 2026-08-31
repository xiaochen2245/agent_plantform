# 加密导出特性 — 设计文档

| 项目 | 值 |
|---|---|
| 日期 | 2026-08-28 |
| 状态 | 待复审 |
| 范围 | 会话导出（单/批量） + 附件下载 + 审计日志导出，统一支持"可选加密" |
| 浏览器目标 | Chrome / Edge / Firefox（Windows 客户端，**不支持 Safari**） |

---

## 1. 背景与目标

当前 MVP 设计文档里只定义了文件**上传**链路，没有任何"把数据下载到本地"的通道。员工在以下场景需要把数据从平台带走：

1. **会话导出**：把单次或多次对话导出为 JSON / Markdown，用于本地存档、交接、外部引用。
2. **附件下载**：重新下载对话中的文件——既包括员工自己上传过的附件，也包括 Agent 在对话中生成的产物（PDF、图片等，通过 Dify `message_file` 事件落库的文件）。
3. **审计日志导出**：管理员按时间窗口导出 `audit_logs`，用于合规报送。

这些场景涉及两类敏感信息：
- 员工上传的合同、设计稿等业务文件
- 对话中的内部讨论、客户信息、未公开产品决策

**目标**：让员工在下载上述任何一项时，可以勾选"加密下载"，由用户输入密码派生密钥，浏览器侧加密后落地为 `.enc` 文件。同时提供一个无需登录的 `/decrypt` 页面，让员工自己解开文件。

---

## 2. 设计原则

| 原则 | 体现 |
|---|---|
| **密码永不离开客户端** | 派生、加密、解密全部在浏览器用 Web Crypto API；后端不存密码、不存派生密钥 |
| **服务端不重复造轮子** | 后端只负责"流式返回原始数据"，加密动作一律前端做 |
| **离线可解** | 提供 `/decrypt` 公共页面，员工不依赖任何 CLI 工具 |
| **单一加密抽象** | 所有下载共用一个 `useEncryptedDownload` hook，UI 层只关心"加不加密" |
| **可观测** | 所有下载（含加密/非加密）都写 `audit_logs` |
| **可恢复的兼容扩展** | envelope 头带 `v: 1` 版本字段；未来算法升级走 v2，不破坏旧文件 |

---

## 3. 整体架构

```
┌────────────────────────────────────────────────────────────────┐
│ 浏览器 (React SPA)                                              │
│                                                                 │
│  ┌──────────────────────┐   ┌────────────────────────────┐    │
│  │ <DownloadButton>     │   │ <PasswordDialog>             │    │
│  │ - url                │   │ - 密码输入                   │    │
│  │ - filename           │──>│ - 强度提示 (zxcvbn)          │    │
│  │ - encrypt: bool      │   │ - 确认                       │    │
│  │ - batch?: bool       │   │ - 弱密码警告                │    │
│  └──────────┬───────────┘   └────────────┬───────────────┘    │
│             │                            │                     │
│             ▼                            ▼                     │
│  ┌──────────────────────────────────────────────────────┐     │
│  │ useEncryptedDownload(url, filename, { encrypt })      │     │
│  │   - fetch(url, { credentials: 'include' })            │     │
│  │   - if encrypt: deriveKey(password) → Penumbra        │     │
│  │   - penumbra.save([stream], filename)                │     │
│  └──────────────────────────┬───────────────────────────┘     │
│                             │ ReadableStream                   │
└─────────────────────────────┼─────────────────────────────────┘
                              │ HTTPS
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ FastAPI 后端                                                    │
│   GET  /api/conversations/{id}/export?format=json|md           │
│   POST /api/conversations/export-batch                          │
│         body: {conv_ids: [...], format: "json"|"md"}           │
│         resp: zip 字节流                                       │
│   POST /api/admin/audit-logs/export                            │
│         body: {from, to, format: "json"|"csv"}                 │
│   GET  /api/files/{file_id}/download                            │
└──────────────────────────┬─────────────────────────────────────┘
                           │
                           ▼
                  Postgres 16 / 本地文件存储
```

---

## 4. 加密文件格式 (.enc)

为了让员工（用我们的 `/decrypt` 页面或其他工具）能解开文件，**必须有一个明确的、文档化的文件格式**：

```
┌───────────────────────────────────────────────────────────────┐
│ 文件头（JSON，UTF-8，单行无换行）                               │
│ {                                                              │
│   "v": 1,                  // 格式版本号 (int, 当前仅支持 1)   │
│   "alg": "AES-256-GCM",    // 算法标识 (allow-list)            │
│   "kdf": "PBKDF2-SHA256",  // 密钥派生函数 (allow-list)        │
│   "iter": 100000,          // PBKDF2 迭代次数 (允许 100k–200k) │
│   "salt": "<base64>",      // 16 字节随机盐                     │
│   "iv": "<base64>",        // 12 字节 GCM IV (运行时校验)      │
│   "ivLen": 12,             // 显式 IV 长度，防 Penumbra 升级   │
│   "authTag": "<base64>",   // 16 字节 GCM 认证标签             │
│   "origName": "conv-xxx.json",  // 原始文件名（解密后用）      │
│   "mime": "application/json",   // 原始 MIME                    │
│   "createdAt": "2026-08-28T10:30:00Z"                         │
│ }                                                              │
├───────────────────────────────────────────────────────────────┤
│ \n（一个换行符作为分隔符）                                      │
├───────────────────────────────────────────────────────────────┤
│ 加密数据（Penumbra 内部流，二进制）                              │
└───────────────────────────────────────────────────────────────┘
```

### 关键决策

1. **JSON 头 + 二进制体**：人类可读、可校验；前 4KB 内可一眼看出格式版本与算法，方便日后升级。
2. **单行 JSON 头**：用 `JSON.stringify(obj, null, 0)` 输出，不带缩进/换行，避免破坏分隔。
3. **authTag 内嵌**：Penumbra 的 AES-GCM 把 authTag 单独从加密流剥离出来，必须随头一起保存。
4. **`ivLen` 显式字段**：防止 Penumbra 未来升级变更默认 IV 大小导致 envelope 与解密端不一致——解密时按 `ivLen` 字段取，不靠假设。
5. **后缀统一 `.enc`**：双击默认不知道用什么打开（这正是想要的行为），员工必须有意识地到 `/decrypt` 页面上传。
6. **`origName` + `mime`**：解密时给到前端，前端**用 `secure_filename`-等价规则清洗**后作为下载文件名（防路径穿越、CRLF 注入、bidi override）。
7. **版本字段 `v: 1`**：未来升级走 v2（可能换 Argon2 + XChaCha20），旧 v1 文件仍可解开。

### envelope 验证 allow-list（`/decrypt` 强制）

解密前**必须**校验以下字段，拒绝任何不在白名单的值：

| 字段 | 允许值 | 拒绝行为 |
|---|---|---|
| `v` | `1`（整数） | 任何 v<1、v>1、字符串、NaN、缺失 → 报错 |
| `alg` | `"AES-256-GCM"` | 其他值 → 报错 |
| `kdf` | `"PBKDF2-SHA256"` | 其他值 → 报错 |
| `iter` | `100000–200000` | 范围外 → 报错（防 DoS：太低不安全，太高卡死浏览器） |
| `mime` | 沿用上传时白名单（pdf/docx/txt/md/png/jpeg） | 其他 → 警告但仍可解（mime 只是提示） |
| `ivLen` | `12`（整数） | 其他 → 报错 |
| `salt` / `iv` / `authTag` | 长度正确 + base64 解码成功 | 解码失败 → 报错 |

### 文件大小上限

- **前端硬上限 500 MB**：超过拒绝并提示分批
- **后端硬上限 5 GB**：nginx `client_max_body_size` + FastAPI 流式检查双重保险
- 单文件加密：受 Penumbra 流式能力支持，**理论上无限**（走 Web Streams）
- 实际限制：浏览器内存。前端设置 500 MB 上限

---

## 5. 加密原语

### 5.1 算法选择

| 环节 | 算法 | 理由 |
|---|---|---|
| 对称加密 | **AES-256-GCM** | Web Crypto API 原生支持；Penumbra 默认；提供机密性 + 完整性 |
| 密钥派生 | **PBKDF2-SHA256, 100,000 轮** | Web Crypto 原生支持；无需 WASM；UX 友好 |
| 随机数 | `crypto.getRandomValues()` | 浏览器原生 CSPRNG |
| 密码强度评估 | **zxcvbn** | DropBox 开源库，纯前端，无网络调用 |

### 5.2 派生参数（含运行时校验）

```ts
// deriveKey 显式返回 {keyBytes, salt}，salt 由调用方传给 envelopeWrap
async function deriveKey(password: string): Promise<{
  keyBytes: Uint8Array;
  salt: Uint8Array;
  iter: number;
}> {
  const iter = 100_000;
  const salt = crypto.getRandomValues(new Uint8Array(16));

  const baseKey = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(password),
    'PBKDF2',
    false,
    ['deriveKey']
  );
  const aesKey = await crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' },
    baseKey,
    { name: 'AES-GCM', length: 256 },
    true,                           // extractable: 必须 true 才能喂给 Penumbra
    ['encrypt']
  );
  const keyBytes = new Uint8Array(await crypto.subtle.exportKey('raw', aesKey));
  return { keyBytes, salt, iter };
}
```

### 5.3 IV 长度运行时断言（防 Penumbra 升级不一致）

```ts
async function envelopeWrap(
  header: EnvelopeHeader,
  encrypted: PenumbraEncryptedFile,
): Promise<{ stream: ReadableStream; size: number | null }> {
  // Penumbra 的 getDecryptionInfo 返回的 IV 必须是 12 字节
  if (header.ivLen !== 12) throw new Error('Invalid IV length');
  const ivBytes = base64ToBytes(header.iv);
  if (ivBytes.byteLength !== 12) throw new Error('IV length mismatch');

  const headerBytes = new TextEncoder().encode(JSON.stringify(header) + '\n');
  const bodySize = encrypted.size ?? null;

  // 用 TransformStream 拼接，避免把整个加密流读进内存
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const reader = encrypted.stream.getReader();

  (async () => {
    try {
      await writer.write(headerBytes);    // 先写头
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        await writer.write(value);
      }
      await writer.close();
    } catch (e) {
      // 任何错误：abort writer + 释放 reader lock，避免内存泄漏
      await writer.abort(e);
      reader.releaseLock();
      throw e;
    }
    reader.releaseLock();
  })();

  return { stream: readable, size: bodySize !== null ? headerBytes.length + bodySize : null };
}
```

### 5.4 PBKDF2 100k 轮的理由（明确权衡）

- 浏览器跑 PBKDF2 100k 轮大约 200-500ms（机器性能相关）
- 解密时员工每输一次密码都要付这笔开销——再高 UX 会变差
- **OWASP 2023 推荐 PBKDF2-SHA256 ≥ 600,000**（针对离线攻击的高熵密码场景）
- **本设计保留 100k 的理由**：
  1. 加密发生在下载瞬间，员工耐心阈值低
  2. 通过**强密码策略（5.5）**+ 警告而非更高迭代数来补偿
  3. v2 升级路径：未来可换成 Argon2id（需要 WASM 库如 `argon2-browser`），同时把迭代数提到 600k+
- **接受的风险**：8 位纯数字密码可在数小时内被离线 GPU 穷举；这就是为什么强制最小 12 位 + 复合字符（见 5.5）

### 5.5 密码强度要求（前端 `<PasswordDialog>` 强制）

| 项 | 要求 |
|---|---|
| 长度 | **≥ 12 字符**（不再用 8） |
| 字符类 | 至少包含 2 类（小写/大写/数字/符号） |
| zxcvbn 分数 | **≥ 2**（"reasonable"）允许通过；分数 < 2 时强制显示警告 |
| 弱密码告警 | 任何分数 ≤ 2 都弹红字警告"该密码可在离线攻击下被破解" |

**绝不强制高强度**——员工会忘，但弱密码必须给警告。

### 5.6 明确不做密码找回 / 密钥托管

忘了密码 = 文件解不开。这是产品决策，写进：
- `<PasswordDialog>` 顶部提示
- `/decrypt` 页面顶部红色警告
- 用户首次使用功能时的 onboarding tooltip

---

## 6. 前端模块设计

### 6.1 目录结构

```
frontend/src/
├── lib/crypto/                          ← 新增
│   ├── envelope.ts                      # 读写 .enc 头 + TransformStream 拼接
│   ├── deriveKey.ts                     # PBKDF2 派生
│   ├── encryptStream.ts                 # 包装 Penumbra.encrypt
│   ├── decryptFile.ts                   # 解密用于 /decrypt 页面
│   ├── sanitizeFilename.ts              # secure_filename 等价实现
│   └── envelope.test.ts                 # 单元测试
│
├── hooks/
│   └── useEncryptedDownload.ts          ← 新增：统一下载入口
│
├── components/
│   ├── DownloadButton.tsx               ← 新增：通用下载按钮（支持单/批）
│   └── PasswordDialog.tsx               ← 新增：密码输入弹窗
│
├── pages/
│   ├── History.tsx                      ← 改造：列表行加 checkbox + <DownloadButton>
│   ├── Chat.tsx                         ← 改造：顶部加"导出此对话"
│   ├── Admin/
│   │   └── AuditLogs.tsx                ← 改造或新建
│   └── Decrypt.tsx                      ← 新增：公共解密页面
│
└── api/
    └── exports.ts                       ← 新增：调用导出端点
```

### 6.2 `<DownloadButton>` 组件

```tsx
interface DownloadButtonProps {
  url: string;                           // 后端端点
  filename: string;                      // 默认文件名（不含扩展名）
  mimeType?: string;                     // 用于 envelope.origName 后缀
  variant?: 'primary' | 'default' | 'link';
  size?: 'small' | 'middle' | 'large';
  label?: string;                        // 按钮文字，默认 "下载"
}

// UI: 一个按钮 + 旁边一个 🔒 checkbox "加密"
// 点按钮 → 弹 PasswordDialog（如勾选了加密）→ 触发下载
```

行为细节：
- **未勾选加密**：直接 fetch → blob → `<a download>` 触发浏览器下载
- **勾选加密**：fetch → 流式 encrypt → `.enc` 文件
- **下载过程中**：按钮 disabled，显示进度（Penumbra 自带 `penumbra-progress` 事件）
- **失败**：toast 错误

### 6.3 `<PasswordDialog>` 组件

```tsx
interface PasswordDialogProps {
  open: boolean;
  filename: string;
  onConfirm: (password: string) => void;
  onCancel: () => void;
}

// UI:
// - 标题: "为文件加密"
// - 副标题: 文件名 + 红色警告"忘记密码将无法恢复"
// - 密码输入框（type=password）
// - 确认密码输入框
// - 实时 zxcvbn 强度条 + 文字（弱/中/强）
// - 弱密码（zxcvbn < 2）时红字警告"该密码可在离线攻击下被破解"
// - 按钮: 取消 / 加密下载（弱密码时按钮变红但仍可点）
```

### 6.4 `useEncryptedDownload` hook

```ts
function useEncryptedDownload() {
  return useCallback(async (opts: {
    url: string;
    filename: string;           // 不含扩展名
    mimeType?: string;
    encrypt: boolean;
    password?: string;          // 仅 encrypt=true 时需要
  }) => {
    // 1. fetch 流（带 credentials 让 cookie 自动带上）
    const res = await fetch(opts.url, { credentials: 'include' });
    if (!res.ok) throw new DownloadError(res.status, await res.text());

    // res.body 在某些错误响应路径可能为 null（如 204 / opaque redirect）
    if (!res.body) throw new DownloadError(0, 'Empty response body');

    const auditMeta = {
      url: opts.url,
      filename: opts.filename,
      encrypted: opts.encrypt,
      bytes: Number(res.headers.get('content-length') ?? 0),
    };

    // 2. 不加密 → 直接存
    if (!opts.encrypt) {
      await penumbra.save(
        [{ stream: res.body, size: auditMeta.bytes }],
        opts.filename
      );
      await logClientAudit(auditMeta);
      return;
    }

    // 3. 加密：deriveKey 显式返回 salt，envelopeWrap 必须收到
    const { keyBytes, salt, iter } = await deriveKey(opts.password!);
    const [encrypted] = await penumbra.encrypt(
      { key: keyBytes },
      { stream: res.body, size: auditMeta.bytes }
    );
    const decryptionInfo = await penumbra.getDecryptionInfo(encrypted);

    // 4. 组装 envelope：JSON 头 + \n + 加密流
    const header: EnvelopeHeader = {
      v: 1,
      alg: 'AES-256-GCM',
      kdf: 'PBKDF2-SHA256',
      iter,
      salt: bytesToBase64(salt),
      iv: decryptionInfo.iv,            // base64 string from Penumbra
      ivLen: 12,                        // 显式记录，防升级不一致
      authTag: decryptionInfo.authTag,
      origName: sanitizeFilename(opts.filename),
      mime: opts.mimeType ?? 'application/octet-stream',
      createdAt: new Date().toISOString(),
    };
    const enveloped = await envelopeWrap(header, encrypted);

    // 5. 保存为 .enc + 立刻清内存里的密码/key
    await penumbra.save([enveloped], `${opts.filename}.enc`);
    keyBytes.fill(0);
    opts.password = '';   // ⚠️ 仅清本 hook 持有的字符串副本。调用方若保留了对原 password 字符串的引用需自行清
    await logClientAudit({ ...auditMeta, encrypted: true });
  }, []);
}
```

### 6.5 `/decrypt` 页面

公共路由，**不需要登录**：

```tsx
// 路由：/decrypt
// UI:
//   1. 顶部红色警告:"本页面不验证文件来源。请仅解密你信任的文件。本页面完全在浏览器内运行，不上传任何内容。"
//   2. 拖拽上传 .enc 文件
//   3. 文件读入 → 解析 envelope 头（按 allow-list 校验字段）
//   4. 校验失败 → 显示具体哪个字段不合法，停止解密
//   5. 校验通过 → 显示 envelope 头信息（origName, mime, createdAt, alg, iter）
//   6. 密码输入框
//   7. "解密并下载"按钮
//   8. 进度条（Penumbra 解密是流式的）
//   9. 完成后浏览器触发下载，文件名 = envelope.origName（再过一遍 sanitizeFilename）
```

**关键**：
- 解密端**重新校验 envelope allow-list**（alg/kdf/iter 范围/ivLen）
- 密码错误 → 解密抛错（GCM authTag 校验失败），UI 显示"密码错误或文件损坏"
- 不做任何服务端调用——纯前端静态页面

### 6.6 批量导出 UI

History 页面表格左侧加 checkbox 列，列表上方加 "导出选中 (N)" 按钮。点击后弹 `<DownloadButton>`，URL 指向 `POST /api/conversations/export-batch`，filename 形如 `conversations-{N}-{date}.zip`。

---

## 7. 后端模块设计

### 7.1 新增端点

#### `GET /api/conversations/{id}/export?format=json|md`

流式返回单个对话导出。

```python
@router.get("/conversations/{conv_id}/export")
async def export_conversation(
    conv_id: UUID,
    format: Literal["json", "md"],
    user: User = Depends(current_user),
):
    conv = await get_conversation_or_404(conv_id)
    if conv.user_id != user.id and not user.is_platform_admin:
        raise HTTPException(403)

    state = {"bytes_sent": 0, "audit_written": False}

    async def stream():
        try:
            if format == "json":
                yield '{"conversation": {'
                yield f'"id": "{conv.id}", "title": {json.dumps(conv.title)}, '
                yield '"messages": ['
                first = True
                async for msg in iter_messages(conv_id):
                    if not first: yield ','
                    first = False
                    chunk = json.dumps({
                        "role": msg.role,
                        "content": msg.content,
                        "created_at": msg.created_at.isoformat(),
                        "files": msg.files,
                    }, ensure_ascii=False)
                    state["bytes_sent"] += len(chunk.encode('utf-8'))
                    yield chunk
                yield ']}}'
            else:  # md
                head = f"# {conv.title}\n\n_导出时间: {datetime.now().isoformat()}_\n\n"
                state["bytes_sent"] += len(head.encode('utf-8'))
                yield head
                async for msg in iter_messages(conv_id):
                    body = f"## {'用户' if msg.role == 'user' else 'Agent'}\n\n{msg.content}\n\n"
                    state["bytes_sent"] += len(body.encode('utf-8'))
                    yield body
        except asyncio.CancelledError:
            # 客户端断开 → Starlette 把 CancelledError 注入 generator
            # 此时 BackgroundTask 不会执行，所以 audit 必须在这里写
            if not state["audit_written"]:
                await audit_log(user.id, "conversation.export", conv_id, metadata={
                    "format": format,
                    "bytes_sent": state["bytes_sent"],
                    "completed": False,
                    "reason": "client_disconnect",
                })
                state["audit_written"] = True
            raise
        except Exception as e:
            if not state["audit_written"]:
                await audit_log(user.id, "conversation.export", conv_id, metadata={
                    "format": format,
                    "bytes_sent": state["bytes_sent"],
                    "completed": False,
                    "reason": type(e).__name__,
                    "error": str(e)[:200],
                })
                state["audit_written"] = True
            raise

    async def on_success():
        # 仅在 stream 正常返回时执行（BackgroundTask 语义）
        if not state["audit_written"]:
            await audit_log(user.id, "conversation.export", conv_id, metadata={
                "format": format,
                "bytes": state["bytes_sent"],
                "completed": True,
            })
            state["audit_written"] = True

    return StreamingResponse(
        stream(),
        media_type="application/json" if format == "json" else "text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{conv_id}.{format}"'},
        background=BackgroundTask(on_success),
    )
```

**审计语义保证**（所有流式端点统一）：
- ✅ 客户端 abort → `CancelledError` 在 generator 内被捕获 → audit `completed=false, reason="client_disconnect"`
- ✅ 服务端异常 → `Exception` 捕获 → audit `completed=false, reason=<ExceptionType>`
- ✅ 正常完成 → stream 正常 return → `BackgroundTask.on_success` 执行 → audit `completed=true`
- ✅ `audit_written` flag 保证**恰好写一次**（三种路径互斥）

#### `POST /api/conversations/export-batch`

批量导出多个对话为 zip。**所有 audit 在 on_success/on_error 一次性写**（不写在循环里）。

```python
import asyncio
import zipstream
from starlette.background import BackgroundTask

@router.post("/conversations/export-batch")
async def export_conversations_batch(
    body: BatchExportRequest,  # {conv_ids: list[UUID], format: "json"|"md"}
    user: User = Depends(current_user),
):
    if len(body.conv_ids) > 100:
        raise HTTPException(400, "Maximum 100 conversations per batch")

    # 鉴权：所有对话必须属于当前用户（或用户是 PLATFORM_ADMIN）
    for cid in body.conv_ids:
        conv = await get_conversation_or_404(cid)
        if conv.user_id != user.id and not user.is_platform_admin:
            raise HTTPException(403, f"No access to conversation {cid}")

    batch_id = uuid4()
    state = {
        "bytes_sent": 0,
        "convs_processed": 0,
        "audit_written": False,
    }

    async def stream():
        try:
            z = zipstream.ZipFile(mode='w', compression=zipstream.ZIP_STORED)
            for cid in body.conv_ids:
                conv = await get_conversation(cid)
                entry_name = sanitize_filename(f"{conv.title or cid}.{body.format}")
                with z.open(entry_name, 'w') as entry:
                    async for chunk in render_conversation(conv, body.format):
                        entry.write(chunk)
                        state["bytes_sent"] += len(chunk)
                state["convs_processed"] += 1
            for chunk in z:
                yield chunk
        except asyncio.CancelledError:
            if not state["audit_written"]:
                # 整个批量算一次 audit；标记为不完整
                await audit_log(user.id, "conversation.export_batch", None, metadata={
                    "batch_id": str(batch_id),
                    "format": body.format,
                    "requested": len(body.conv_ids),
                    "processed": state["convs_processed"],
                    "bytes_sent": state["bytes_sent"],
                    "completed": False,
                    "reason": "client_disconnect",
                })
                state["audit_written"] = True
            raise
        except Exception as e:
            if not state["audit_written"]:
                await audit_log(user.id, "conversation.export_batch", None, metadata={
                    "batch_id": str(batch_id),
                    "format": body.format,
                    "requested": len(body.conv_ids),
                    "processed": state["convs_processed"],
                    "bytes_sent": state["bytes_sent"],
                    "completed": False,
                    "reason": type(e).__name__,
                    "error": str(e)[:200],
                })
                state["audit_written"] = True
            raise

    async def on_success():
        if not state["audit_written"]:
            await audit_log(user.id, "conversation.export_batch", None, metadata={
                "batch_id": str(batch_id),
                "format": body.format,
                "requested": len(body.conv_ids),
                "processed": state["convs_processed"],
                "bytes": state["bytes_sent"],
                "completed": True,
            })
            state["audit_written"] = True

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="conversations.zip"'},
        background=BackgroundTask(on_success),
    )
```

**关键修正（对照第二轮 review）**：
- ✅ audit **不写在 zip 循环内**，统一在 stream 完成后通过 `on_success` 写
- ✅ 客户端 abort 走 `CancelledError` 分支，写一条 batch 级 audit（含 `processed` vs `requested` 区分进度）
- ✅ 不再 per-conversation 写 audit——批量审计是**一次动作**，不是 N 次独立动作；如需 per-conv 追踪，依赖 webhook 兜底（在原 chat 链路里已有，不重复）

#### `POST /api/admin/audit-logs/export`

管理员按时间窗口导出（带 CSV 注入防护 + 三态 audit 模式）。

```python
import asyncio
import csv
import io
from starlette.background import BackgroundTask

@router.post("/admin/audit-logs/export")
async def export_audit_logs(
    body: AuditExportRequest,  # {from: datetime, to: datetime, format: "json"|"csv"}
    user: User = Depends(require_platform_admin),
):
    # CSV 注入防护：前缀危险字符
    def sanitize_csv_cell(value: str) -> str:
        if not isinstance(value, str):
            value = str(value)
        if value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
            value = "'" + value
        return value

    state = {"bytes_sent": 0, "rows_sent": 0, "audit_written": False}

    async def stream():
        try:
            if body.format == "json":
                yield '{"logs":['
                first = True
                async for log in iter_audit_logs(body.from_, body.to_):
                    if not first: yield ','
                    first = False
                    chunk = json.dumps({
                        "id": log.id,
                        "user_id": log.user_id,
                        "action": log.action,
                        "resource_type": log.resource_type,
                        "resource_id": log.resource_id,
                        "ip": log.ip,
                        "user_agent": log.user_agent,
                        "metadata": log.metadata,
                        "created_at": log.created_at.isoformat(),
                    }, default=str, ensure_ascii=False)
                    state["bytes_sent"] += len(chunk.encode('utf-8'))
                    state["rows_sent"] += 1
                    yield chunk
                yield ']}'
            else:  # csv
                buf = io.StringIO()
                writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
                writer.writerow([
                    "id", "user_id", "action", "resource_type",
                    "resource_id", "ip", "user_agent", "created_at",
                ])
                line = buf.getvalue()
                state["bytes_sent"] += len(line.encode('utf-8'))
                yield line
                buf.seek(0); buf.truncate()
                async for log in iter_audit_logs(body.from_, body.to_):
                    writer.writerow([
                        log.id, log.user_id, log.action,
                        log.resource_type, log.resource_id,
                        sanitize_csv_cell(log.ip or ""),
                        sanitize_csv_cell(log.user_agent or ""),
                        log.created_at.isoformat(),
                    ])
                    line = buf.getvalue()
                    state["bytes_sent"] += len(line.encode('utf-8'))
                    state["rows_sent"] += 1
                    yield line
                    buf.seek(0); buf.truncate()
        except asyncio.CancelledError:
            if not state["audit_written"]:
                await audit_log(user.id, "audit_logs.export", metadata={
                    "from": body.from_.isoformat(),
                    "to": body.to_.isoformat(),
                    "format": body.format,
                    "bytes_sent": state["bytes_sent"],
                    "rows": state["rows_sent"],
                    "completed": False,
                    "reason": "client_disconnect",
                })
                state["audit_written"] = True
            raise
        except Exception as e:
            logger.exception("audit log export failed")
            if not state["audit_written"]:
                await audit_log(user.id, "audit_logs.export", metadata={
                    "from": body.from_.isoformat(),
                    "to": body.to_.isoformat(),
                    "format": body.format,
                    "bytes_sent": state["bytes_sent"],
                    "rows": state["rows_sent"],
                    "completed": False,
                    "reason": type(e).__name__,
                    "error": str(e)[:200],
                })
                state["audit_written"] = True
            raise

    async def on_success():
        if not state["audit_written"]:
            await audit_log(user.id, "audit_logs.export", metadata={
                "from": body.from_.isoformat(),
                "to": body.to_.isoformat(),
                "format": body.format,
                "bytes": state["bytes_sent"],
                "rows": state["rows_sent"],
                "completed": True,
            })
            state["audit_written"] = True

    return StreamingResponse(
        stream(),
        media_type="application/json" if body.format == "json" else "text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-logs.{body.format}"'},
        background=BackgroundTask(on_success),
    )
```

**关键变化（对照 reviewer 反馈）**：
- ✅ 用 Python `csv` 模块处理引号转义，不再手拼逗号
- ✅ `sanitize_csv_cell` 前缀 `=` `+` `-` `@` 防公式注入
- ✅ CSV header 包含 `user_agent`（reviewer 指出遗漏的列）
- ✅ Audit 在响应完成后写，含 `bytes` 和 `completed`

#### `GET /api/files/{file_id}/download`

```python
import asyncio
from starlette.background import BackgroundTask

@router.get("/files/{file_id}/download")
async def download_file(file_id: str, user: User = Depends(current_user)):
    file_meta = await get_file_meta(file_id)
    if not can_access_file(user, file_meta):
        raise HTTPException(403)

    state = {"bytes_sent": 0, "audit_written": False}

    async def stream():
        try:
            async with aiofiles.open(file_meta.path, 'rb') as f:
                while chunk := await f.read(64 * 1024):
                    state["bytes_sent"] += len(chunk)
                    yield chunk
        except asyncio.CancelledError:
            if not state["audit_written"]:
                await audit_log(user.id, "file.download", file_id, metadata={
                    "name": file_meta.name,
                    "size": file_meta.size,
                    "bytes_sent": state["bytes_sent"],
                    "mime": file_meta.mime,
                    "completed": False,
                    "reason": "client_disconnect",
                })
                state["audit_written"] = True
            raise
        except Exception as e:
            if not state["audit_written"]:
                await audit_log(user.id, "file.download", file_id, metadata={
                    "name": file_meta.name,
                    "size": file_meta.size,
                    "bytes_sent": state["bytes_sent"],
                    "mime": file_meta.mime,
                    "completed": False,
                    "reason": type(e).__name__,
                    "error": str(e)[:200],
                })
                state["audit_written"] = True
            raise

    async def on_success():
        if not state["audit_written"]:
            await audit_log(user.id, "file.download", file_id, metadata={
                "name": file_meta.name,
                "size": file_meta.size,
                "bytes_sent": state["bytes_sent"],
                "mime": file_meta.mime,
                "completed": True,
            })
            state["audit_written"] = True

    # 下载文件名用 sanitize_filename 清洗
    safe_name = sanitize_filename(file_meta.name)
    return StreamingResponse(
        stream(),
        media_type=file_meta.mime,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        background=BackgroundTask(on_success),     # ✅ 接 background 参数
    )
```

### 7.2 共享 helper 函数签名（typed contract）

```python
# app/audit/service.py
async def audit_log(
    user_id: int,
    action: str,                                # e.g. "conversation.export"
    resource_id: str | int | UUID | None = None,
    resource_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,                      # 缺省从 request context 取
    user_agent: str | None = None,
) -> int:                                        # 返回 audit_logs.id

# app/conversations/queries.py
async def iter_messages(
    conversation_id: UUID,
    batch_size: int = 100,
) -> AsyncIterator[MessageORM]: ...

# app/audit/queries.py
async def iter_audit_logs(
    from_: datetime,
    to: datetime,
    batch_size: int = 500,
) -> AsyncIterator[AuditLogORM]: ...

# app/files/service.py
async def get_file_meta(file_id: str) -> FileMetaORM: ...
async def can_access_file(user: User, file_meta: FileMetaORM) -> bool: ...

# app/utils/filename.py
def sanitize_filename(name: str, max_length: int = 200) -> str:
    """
    - 移除路径分隔符 (/ \\)
    - 移除控制字符 (< 0x20, 包括 \r \n \t)
    - 移除 Unicode bidi override
    - 替换连续空白为单下划线
    - 截断到 max_length（保留扩展名）
    - 拒空名 → 返回 "unnamed"
    """
```

### 7.3 文件存储位置

- **MVP 阶段**：本地磁盘 `app/storage/{yyyy}/{mm}/{file_id}.{ext}`，文件名用 UUID 防冲突
- **二期**：换 MinIO

### 7.4 不修改的数据模型

**不需要新增表 / 字段**。所有下载事件通过现有的 `audit_logs` 记录。

### 7.5 服务端大小硬上限

| 层 | 限制 |
|---|---|
| nginx | `client_max_body_size 5g;`（覆盖所有导出端点） |
| FastAPI | `StreamingResponse` 内部累加 `bytes_sent`，超过 5 GB 主动断开 |
| 前端 | `<DownloadButton>` 下载前 GET 请求 HEAD 头检查 Content-Length，超过 500 MB 直接拒绝 |

---

## 8. 安全考量

### 8.1 威胁模型

| 威胁 | 缓解 |
|---|---|
| 密码太弱导致 .enc 可破解 | zxcvbn 分数 < 2 时强警告；最少 12 字符 + 2 类字符；文档明示"弱密码可在数小时内被离线穷举" |
| 派生 key 暴露在 DevTools | 不可避免——必须在浏览器内存中派生。文档明确说明 + 用完立刻 `fill(0)` |
| GCM nonce 复用 | 每次加密生成新 IV（`crypto.getRandomValues`） |
| IV 长度未来不一致 | envelope 头显式记 `ivLen`；解密端按此字段取 |
| 旁路攻击（密码残留内存） | 加密完成后 `keyBytes.fill(0)`、`password = ''`；不存进 localStorage |
| 中间人攻击 | 平台本身走 HTTPS；员工下载 .enc 后离线打开，与平台无关 |
| `/decrypt` 页面被钓鱼利用 | 顶部明确"本页面不验证文件来源"；纯静态、无服务端调用 |
| 服务端审计日志泄露用户下载内容 | 不在 audit_logs 里记内容；只记元数据 |
| 暴力破解 .enc | 单密码每秒试 ~5 次（PBKDF2 100k 轮），攻击者拿到文件可离线穷举。**强密码是唯一防线**（定义见 5.5） |
| 员工把 .enc 发给外部 | 设计上没办法阻止——这是产品功能，不是漏洞 |
| **CSV 公式注入** | Python `csv` 模块 + `sanitize_csv_cell` 前缀 `=` `+` `-` `@`（仅审计 CSV 导出） |
| **origName 文件名注入** | 服务端 `sanitize_filename` + 前端 `sanitizeFilename` 双重清洗，防路径穿越/CRLF/bidi |
| **envelope header 字段值未校验** | `/decrypt` 强制 allow-list（alg/kdf/iter 范围/ivLen/version） |

### 8.2 密码处理红线（写进代码注释）

```ts
// ❌ 永远不要做的事：
// - 把 password 写入 console.log / Sentry / 任何日志
// - 把 password 存进 localStorage / sessionStorage
// - 把派生 key 存进任何持久化存储
// - 在 URL 里带 password
// - 把 password 传给后端
//
// ✅ 只能：
// - 在 React state 里持有（且必须在用完后 clear）
// - 一次性喂给 crypto.subtle.deriveKey
```

### 8.3 CSP 调整（最小化放宽）

`/decrypt` 页面需要 Web Crypto API + Web Worker（Penumbra 用）。在 nginx 配置加：

```nginx
location = /decrypt {
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'" always;
}
```

**重要**：Penumbra **不使用 WASM**，不需要 `wasm-unsafe-eval`。只允许 `worker-src 'self' blob:` 是因为 Penumbra 在 Web Worker 里跑加密。

其他页面继续走严格 CSP，不受影响。

---

## 9. 测试计划

### 9.1 单元测试（vitest，frontend）

| 用例 | 验证 |
|---|---|
| `envelope.test.ts`: wrap → unwrap 往返 | envelope 解析正确，所有字段一致 |
| `envelope.test.ts`: 拒绝 alg="AES-128-CBC" | allow-list 校验 |
| `envelope.test.ts`: 拒绝 kdf="scrypt" | allow-list 校验 |
| `envelope.test.ts`: 拒绝 iter=50000 / iter=300000 | 范围校验 |
| `envelope.test.ts`: 拒绝 v=0 / v=2 / v="1" / v=NaN / 缺失 v | 版本校验 |
| `envelope.test.ts`: 拒绝 ivLen=16 | 防 Penumbra 升级 |
| `envelope.test.ts`: 拒绝 base64 解码失败的 salt/iv/authTag | 解码校验 |
| `sanitizeFilename.test.ts`: `../etc/passwd` → `etc_passwd` | 路径穿越 |
| `sanitizeFilename.test.ts`: `name\r\nX-Evil: 1` → `nameX-Evil 1` | CRLF 注入 |
| `sanitizeFilename.test.ts`: `name‮gpj.exe` → `namegpj.exe` | bidi override |
| `deriveKey.test.ts`: 相同密码+salt+iter → 相同 key | 决定性 |
| `deriveKey.test.ts`: 不同 salt → 不同 key | 隔离 |
| `deriveKey.test.ts`: 返回值包含 `{keyBytes, salt, iter}` | API 契约 |
| `encryptStream.test.ts`: 加密 → 解密 → 原文一致 | 端到端 |
| `decryptFile.test.ts`: 给定错误密码 → 解密失败抛错 | 不静默 |

### 9.2 集成测试（pytest，backend）

| 用例 | 验证 |
|---|---|
| `GET /api/conversations/{id}/export?format=json` 流式返回 | 流式分块正确、字节数对得上、JSON 解析通过 |
| `GET /api/conversations/{id}/export?format=md` | Markdown 格式正确 |
| 用户 A 不能导出用户 B 的对话 | 403 |
| 平台管理员可以导出任何对话 | 200 |
| 审计日志 `conversation.export` 包含 `completed: true` | 区分尝试/完成 |
| **客户端中途断开**：abort 测试 → audit 包含 `completed: false` | finally 块生效 |
| `POST /api/conversations/export-batch` 生成有效 zip | 100 个对话场景 |
| 批量超 100 → 400 | 上限校验 |
| `GET /api/files/{file_id}/download` 流式返回正确字节 | MD5 一致 |
| 用户 A 不能下载用户 B 的附件 | 403 |
| `POST /api/admin/audit-logs/export?format=csv` 流式返回 | 行数 == DB 行数 |
| **CSV 注入**：构造 action="=cmd\|'/c calc'!A1" → 导出后是 `"'=cmd..."` | 公式注入防护 |
| **CSV 转义**：构造 action 含逗号/引号/换行 → 用 csv 模块正确转义 | 引号转义 |
| CSV header 包含 `user_agent` 列 | 列对齐 |
| 审计 CSV 导出时 `audit_logs.export` 写库 | 间接审计 |
| 客户端断开（abort）→ `audit_logs.export` 包含 `completed: false` | 取消语义 |

### 9.3 E2E（Playwright）

| 路径 | 验证 |
|---|---|
| 登录 → History → 单条 → 点下载 → 选加密 → 输密码 → 收到 .enc | 完整闭环 |
| 在 `/decrypt` 上传刚才的 .enc + 同密码 → 拿到原始 JSON | 双向一致 |
| `/decrypt` 上传篡改了 alg 字段的 .enc → 报错"alg 不支持" | allow-list 校验 |
| 改密码再上传 → 解密失败 → UI 显示"密码错误或文件损坏" | 错误处理 |
| 不加密下载 → 收到原格式文件 | 普通路径不退化 |
| History 多选 3 条 → 批量导出 → 收到 zip → 解压得 3 个 JSON | 批量路径 |
| 网络中断半路 → UI 显示错误 + 不写入 .enc | 容错 |
| 弱密码（zxcvbn=1）→ 弹警告但可继续 | 警告 UI |

### 9.4 浏览器兼容（手动）

- Chrome（最新版）
- Edge（最新版）
- Firefox ≥102

每个浏览器跑一次 E2E 加密 + 解密全流程。

### 9.5 安全测试

- `grep -r "password" frontend/src/lib/crypto/` 确认无 console.log
- `grep -r "console.log.*password" frontend/src/` 确认无意外日志
- DevTools 抓 network：确认无 password 上行
- 解密后 key 内存检查（手动，DevTools Memory snapshot 确认不残留）

---

## 10. 部署与配置

### 10.1 新增依赖

`frontend/package.json`：
```json
{
  "dependencies": {
    "@transcend-io/penumbra": "8.1.4",
    "zxcvbn": "4.4.2"
  },
  "devDependencies": {
    "vitest": "1.6.0",
    "@vitest/ui": "1.6.0",
    "jszip": "3.10.1"           // 仅用于解压测试
  }
}
```

**注意**：
- Penumbra **锁定到精确版本**（无 caret），crypto 库不接 minor/patch 自动升级
- `zxcvbn` 只在前端用，密码永远不出浏览器；纯前端库无网络调用
- `jszip` 仅 devDependency（用于单元测试中验证批量导出 zip 内容）

`backend/requirements.txt`：
```
zipstream-ng==1.1.0       # 流式 zip 打包
```

### 10.2 nginx 配置

```nginx
# /decrypt 路由的 CSP 略宽（worker 用）
location = /decrypt {
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'" always;
}

# SSE 已有 X-Accel-Buffering: no 不变

# 导出端点放宽 body 大小（POST /export-batch 接受 conv_ids 列表）
client_max_body_size 1m;     # /api/conversations/export-batch body 很小，1M 足够
# 注：流式 GET 导出不受 client_max_body_size 限制（GET 没 body）
```

### 10.3 不需要的环境变量

- 无新密钥（加密密钥是用户每次派生）
- 无新 DB 表

### 10.4 灰度

- 加密功能默认**开启**（UI 上"加密"复选框默认勾选）
- 通过 Feature Flag（前端 `localStorage` 或后端 `apps.feature_flags`）控制
- 第一周观察：用户拒绝勾选加密的比例、忘记密码的工单量

### 10.5 `/decrypt` 速率限制说明

`/decrypt` 是**静态前端路由**（不命中 `/api/*`），不通过 FastAPI 限流中间件。
- 没有后端处理 → 没有 rate-limit 必要（也无意义）
- 未来如果给 `/decrypt` 加服务（如"解密记录上报"）则需加；当前设计纯前端不上报
- nginx 上 `/decrypt` 不在限速 location block 内，不会被误限

---

## 11. 项目结构变更汇总

```
/mnt/e/program/agent_platform/
├── backend/
│   └── app/
│       ├── chat/
│       │   └── exports.py             ← 新增：会话导出（含单 + 批量）
│       ├── files/
│       │   └── downloads.py           ← 新增：文件下载端点
│       ├── admin/
│       │   └── audit_export.py        ← 新增：审计日志导出（CSV/JSON）
│       ├── audit/service.py           ← 现有：确认 audit_log() 签名（7.2）
│       └── utils/filename.py          ← 新增：sanitize_filename
│
├── frontend/
│   ├── src/
│   │   ├── lib/crypto/                ← 新增模块
│   │   ├── hooks/useEncryptedDownload.ts
│   │   ├── components/
│   │   │   ├── DownloadButton.tsx
│   │   │   └── PasswordDialog.tsx
│   │   ├── pages/
│   │   │   ├── Chat.tsx               ← 改造
│   │   │   ├── History.tsx            ← 改造（多选 + checkbox）
│   │   │   ├── Decrypt.tsx            ← 新增
│   │   │   └── Admin/AuditLogs.tsx
│   │   └── api/exports.ts
│   └── tests/crypto/
│
└── docs/superpowers/
    └── specs/
        ├── 2026-08-28-agent-platform-design.md   (现有)
        └── 2026-08-28-encrypted-export-design.md (本文件)
```

---

## 12. 明确不做（YAGNI）

| 不做 | 理由 |
|---|---|
| ❌ Safari 兼容 | 用户明确说 Windows 客户端 |
| ❌ Argon2 派生 | v1 用 PBKDF2；v2 再升级（届时 envelope 走 v2） |
| ❌ 密码找回 / 密钥托管 | 增加后端复杂度，与"密码不出客户端"原则冲突 |
| ❌ 服务端加密 | 用户密码派生 = 服务端拿不到密码，加密必须在浏览器 |
| ❌ PDF 导出 | v1 只做 JSON + Markdown；PDF 二期 |
| ❌ 加密分享链接 | 涉及一次性 URL 签名，超出本特性范围 |
| ❌ 自动定期加密备份 | 是运维 / 备份策略，不在用户导出范畴 |
| ❌ 文件级增量加密 / 流式分块签名 | 全文件一次加密足够 |
| ❌ Argon2 / 600k PBKDF2 升级 | v2 特性，envelope v2 字段预留 |

---

## 13. 风险与待办

### 风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 员工忘记密码 → 找客服 | 中 | `/decrypt` + 下载时明示"密码无法找回" |
| 弱密码导致文件被穷举（100k 迭代 + 12 字符强制） | 中 | zxcvbn 警告 + 12 字符最小；可被离线 GPU 数小时破解 |
| Penumbra 大文件内存压力 | 低 | 走 Web Streams；500 MB 上限；envelopeWrap 用 TransformStream 拼接避免预读 |
| `extractable: true` 的派生 key 被 DevTools 抓 | 低 | 一次性使用 + 立刻 `fill(0)`；文档明示 |
| `/decrypt` 被用于"解密收到的恶意文件" | 低 | 纯前端、不验证来源；提示用户 |
| Penumbra 升级导致 IV 长度不一致 | 低 | envelope 显式 `ivLen` 字段 |
| CSV 公式注入 | 中 | Python `csv` 模块 + `sanitize_csv_cell` 前缀 |
| origName 文件名注入 | 低 | `sanitize_filename` 服务端 + 前端双重清洗 |
| 后端长时间流被滥用 | 中 | 5 GB 硬上限 + audit 写入 |

### 待办（实施时确认）

- [ ] 确认 `files` 表 / 存储位置（MVP 本地磁盘路径）
- [ ] 确认 `audit_log()` 函数在 `app/audit/service.py` 实际签名是否与 7.2 一致
- [ ] 确认 `iter_messages` / `iter_audit_logs` 是否已有流式实现，没有就加
- [ ] `zipstream-ng` 实际可用性验证（异步流式 zip）
- [ ] `/decrypt` 页面是否需要 i18n（中文 / 英文切换）
- [ ] 大对话（>1000 条消息）流式导出的性能压测
- [ ] 批量 100 个对话同时导出的 zip 内存峰值压测

---

## 14. 实施顺序建议

按依赖关系排序，**自下而上**：

1. **后端 helper + 单会话导出**（不加密，纯流式）—— 验证数据流正确
2. **后端批量导出**（zip 打包 + 流式）
3. **后端审计导出**（CSV 注入防护 + 用户代理字段）
4. **后端文件下载**（带 sanitize_filename）
5. **前端 `lib/crypto/` 模块**（deriveKey、envelopeWrap、sanitizeFilename + 单元测试）
6. **前端 `useEncryptedDownload` hook + envelopeWrap**
7. **前端 `<DownloadButton>` + `<PasswordDialog>`**——UI 拼装
8. **改造 History（多选） + Chat 接入下载**
9. **`/decrypt` 页面**——闭环（加密 → 解密可走通）
10. **审计日志导出 + 附件下载**——复用前面的 hook
11. **E2E + 浏览器兼容测试 + CSV 注入测试**
12. **文档 + Feature Flag 上线**

预计工作量：**前端 4-6 天 + 后端 2-3 天 + 测试 1-2 天**，总计约 **1.5-2 人周**。
