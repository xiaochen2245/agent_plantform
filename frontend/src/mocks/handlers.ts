import { http as mswHttp, HttpResponse } from "msw";
import type { AppInfo, ConversationSummary, MeInfo, UploadedFile } from "../types";

/**
 * 契约 mock 实现（docs/api-contract.md）。
 * 覆盖：auth / apps / conversations（含 v2 消息详情）/ chat 流式。
 * admin 端点（契约 v2）已由真实后端提供，mock 不再覆盖 —— 管理页相关测试
 * 在测试内自建 handlers（见 pages/Admin.test.tsx）。
 * 种子账号：admin@company.com / admin123（管理员）；user@company.com / user123（普通员工，e2e 守卫用例）
 */

const MOCK_JWT = "mock-access-token";
const MOCK_REFRESH = "mock-refresh-token";

/**
 * mock 会话标记（localStorage）：MSW v2 的 handler 跑在页面上下文，
 * SW 返回的 Set-Cookie 不落 document.cookie jar，模块状态又随整页重载丢失。
 * 因此登录态落在 localStorage（跨重载持久；Playwright 每 context 独立隔离）。
 */
const SESSION_KEY = "mock_session";

function currentMockUser(): MeInfo | null {
  try {
    const email = globalThis.localStorage?.getItem(SESSION_KEY);
    if (email === ME_ADMIN.email) return ME_ADMIN;
    if (email === ME_USER.email) return ME_USER;
  } catch {
    // 无 localStorage 的边缘环境（非浏览器）忽略，退回 cookie 判定
  }
  return null;
}

const ME_ADMIN: MeInfo = {
  id: 1,
  email: "admin@company.com",
  name: "张明",
  roles: ["USER", "PLATFORM_ADMIN"],
  dept_id: null,
};

/** 普通员工账号：e2e 管理守卫用例（非 PLATFORM_ADMIN 访问 /admin 被弹回）。 */
const ME_USER: MeInfo = {
  id: 2,
  email: "user@company.com",
  name: "李雷",
  roles: ["USER"],
  dept_id: null,
};

const APPS: AppInfo[] = [
  { id: 1, name: "IT 运维助手", description: "解答服务器、网络与账号问题", mode: "chat", inputs_schema: null },
  { id: 2, name: "报销政策问答", description: "差旅与报销规则查询", mode: "chat", inputs_schema: null },
  { id: 3, name: "代码评审助手", description: "MR 预审与规范检查", mode: "agent", inputs_schema: null },
  // 契约 v3：工作流模式应用（必填输入 business_card）
  {
    id: 4,
    name: "名片生成助手",
    description: "输入名片信息，生成排版名片",
    mode: "workflow",
    inputs_schema: [{ name: "business_card", label: "名片内容", type: "paragraph", required: true }],
  },
];

const CONVERSATIONS: ConversationSummary[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    title: "VPN 连接失败怎么办",
    message_count: 6,
    updated_at: new Date().toISOString(),
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    title: "如何申请会议室投影权限",
    message_count: 4,
    updated_at: new Date(Date.now() - 3600_000).toISOString(),
  },
];

/* ── 会话消息详情：契约 v2 已入约（GET /api/conversations/{id}/messages）── */

/** 动态会话存储：chat/send 新建的会话登记于此（reload 恢复 / 多轮串联 e2e 依赖，对齐契约 v5/v2） */
interface DynMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  files?: UploadedFile[] | null;
  reasoning?: string | null;
}
const dynamicConvs = new Map<string, { appId: number; summary: ConversationSummary; messages: DynMessage[] }>();
let dynMsgSeq = 1000;
let dynConvSeq = 0;

/** 动态会话持久化（mock 模式）：页面 reload 会重建 JS 上下文，恢复链路（F3）依赖会话数据存活 */
const DYN_LS_KEY = "mock_dyn_convs";
function persistDyn() {
  try {
    globalThis.localStorage?.setItem(DYN_LS_KEY, JSON.stringify([...dynamicConvs.values()]));
  } catch {
    /* 存储不可用（node 环境）则跳过 */
  }
}
function restoreDyn() {
  if (dynamicConvs.size > 0) return;
  try {
    const raw = globalThis.localStorage?.getItem(DYN_LS_KEY);
    if (!raw) return;
    for (const c of JSON.parse(raw) as Array<{ appId: number; summary: ConversationSummary; messages: DynMessage[] }>) {
      dynamicConvs.set(c.summary.id, c);
    }
  } catch {
    /* 解析失败忽略 */
  }
}
const CONVERSATION_MESSAGES: Record<string, Array<{ id: number; role: "user" | "assistant"; content: string; created_at: string; files?: UploadedFile[] | null }>> = {
  "11111111-1111-1111-1111-111111111111": [
    {
      id: 1,
      role: "user",
      content: "VPN 连接失败怎么办",
      created_at: new Date().toISOString(),
      // 契约 v4：附件随消息带回（回放展示用示例）
      files: [
        { file_id: "f_mock_vpn_log", name: "vpn-错误日志.txt", size: 2048, mime: "text/plain" },
        { file_id: "f_mock_screenshot", name: "报错截图.png", size: 153600, mime: "image/png" },
      ],
    },
    {
      id: 2,
      role: "assistant",
      content:
        "排查建议：\n· 先确认客户端到网关的连通性（ping 网关地址）；\n· 检查账号是否被临时锁定（连续 5 次失败会锁定 15 分钟）；\n· 若均正常，请联系 IT 服务台提交工单并附错误截图。",
      created_at: new Date().toISOString(),
    },
  ],
};

function hasCookie(request: Request, name: string): boolean {
  const header = request.headers.get("cookie") ?? "";
  return header
    .split(";")
    .map((part) => part.trim().split("=")[0])
    .includes(name);
}

function authCookiePairs(): [string, string][] {
  return [
    ["Set-Cookie", `access_token_cookie=${MOCK_JWT}; Path=/; SameSite=Strict`],
    ["Set-Cookie", `refresh_token_cookie=${MOCK_REFRESH}; Path=/; SameSite=Strict`],
  ];
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 上传文件 mock 计数器（确定性 file_id，便于测试断言） */
let mockFileSeq = 0;

/** 极简 multipart 解析（仅取第一个 file 字段的 filename/content-type/字节长度）。
 * 不用 request.formData()：jsdom 环境下 undici 的 formData 解析器与
 * 手编码/跨 realm 字节流存在兼容问题；mock 只需 name/size/type，自解析足够。
 */
function parseMultipartFile(contentType: string, body: ArrayBuffer): { name: string; type: string; size: number } | null {
  const m = /boundary=(.+)$/.exec(contentType.trim());
  if (!m) return null;
  const boundary = `--${m[1].trim()}`;
  const bytes = new Uint8Array(body);
  const decoder = new TextDecoder("utf-8", { fatal: false });
  // 头部可能含中文文件名（多字节），全部定位按字节进行，仅头部切片解码为文本
  const headerEnd = utf8IndexOf(bytes, "\r\n\r\n", 0);
  if (headerEnd === -1 || utf8IndexOf(bytes, boundary, 0) === -1) return null;
  const headers = decoder.decode(bytes.subarray(0, headerEnd));
  const nameMatch = /filename="([^"]*)"/.exec(headers);
  const typeMatch = /content-type:\s*([^\r\n]+)/i.exec(headers);
  const contentStart = headerEnd + 4;
  const endBytes = utf8IndexOf(bytes, `${boundary}--`, contentStart);
  if (endBytes === -1) return null;
  const size = Math.max(0, endBytes - contentStart - 2); // 末尾 \r\n 不计入
  return { name: nameMatch?.[1] ?? "file", type: (typeMatch?.[1] ?? "application/octet-stream").trim(), size };
}

function utf8IndexOf(haystack: Uint8Array, needle: string, from: number): number {
  const needleBytes = new TextEncoder().encode(needle);
  outer: for (let i = from; i <= haystack.length - needleBytes.length; i++) {
    for (let j = 0; j < needleBytes.length; j++) if (haystack[i + j] !== needleBytes[j]) continue outer;
    return i;
  }
  return -1;
}

/** chat/send 捕获的最近一次请求体（附件断言用：测试读 lastChatSendBody）。 */
export let lastChatSendBody: { query?: string; inputs?: Record<string, string>; files?: string[] } | null = null;

/** 模拟 AI 回答：按段切分为流式增量。 */
function mockAnswer(query: string): string[] {
  const first = `关于「${query.slice(0, 18)}」，以下是检索到的要点：`;
  const body = [
    "· 依据《运维知识库》相关条目，此类问题通常与网络策略或账号状态相关；",
    "· 建议先确认客户端到网关的连通性，再检查账号是否被临时锁定；",
    "· 若以上均正常，请联系 IT 服务台提交工单并附上错误截图。",
  ];
  const tail = "如需更具体的排查步骤，请补充你所在部门与使用的网络环境。";
  return [first + "\n\n", ...body.map((line) => line + "\n"), "\n" + tail];
}

export const handlers = [
  mswHttp.post("/api/auth/login", async ({ request }) => {
    const body = (await request.json()) as { email?: string; password?: string };
    if (body.email === "admin@company.com" && body.password === "admin123") {
      globalThis.localStorage?.setItem(SESSION_KEY, body.email);
      return new HttpResponse(null, { status: 200, headers: authCookiePairs() });
    }
    if (body.email === "user@company.com" && body.password === "user123") {
      globalThis.localStorage?.setItem(SESSION_KEY, body.email);
      return new HttpResponse(null, { status: 200, headers: authCookiePairs() });
    }
    globalThis.localStorage?.removeItem(SESSION_KEY);
    return HttpResponse.json({ detail: "Invalid credentials" }, { status: 401 });
  }),

  mswHttp.post("/api/auth/refresh", ({ request }) => {
    if (currentMockUser() || hasCookie(request, "refresh_token_cookie")) {
      return new HttpResponse(null, { status: 200, headers: authCookiePairs() });
    }
    return HttpResponse.json({ detail: "Invalid refresh token" }, { status: 401 });
  }),

  mswHttp.post("/api/auth/logout", () => {
    globalThis.localStorage?.removeItem(SESSION_KEY);
    return new HttpResponse(null, {
      status: 200,
      headers: [
        ["Set-Cookie", "access_token_cookie=; Path=/; SameSite=Strict; Max-Age=0"],
        ["Set-Cookie", "refresh_token_cookie=; Path=/; SameSite=Strict; Max-Age=0"],
      ] as [string, string][],
    });
  }),

  mswHttp.get("/api/auth/me", ({ request }) => {
    const sessionUser = currentMockUser();
    if (sessionUser) return HttpResponse.json(sessionUser);
    if (hasCookie(request, "access_token_cookie")) return HttpResponse.json(ME_ADMIN);
    return HttpResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }),

  mswHttp.get("/api/apps/me", () => {
    return HttpResponse.json({ apps: APPS });
  }),

  mswHttp.get("/api/conversations", ({ request }) => {
    const appId = new URL(request.url).searchParams.get("app_id") ?? "1";
    // 动态（本页新建，最新在前）+ 静态种子，仅 app 1（与原语义一致）
    restoreDyn();
    const items =
      appId === "1"
        ? [...dynamicConvs.values()].map((c) => c.summary).concat(CONVERSATIONS)
        : [];
    return HttpResponse.json({ items });
  }),

  // 会话消息详情（契约 v2）
  mswHttp.get("/api/conversations/:id/messages", ({ params }) => {
    const id = String(params.id);
    restoreDyn();
    const dyn = dynamicConvs.get(id);
    if (dyn) return HttpResponse.json({ messages: dyn.messages });
    const messages = CONVERSATION_MESSAGES[id];
    if (!messages) return HttpResponse.json({ detail: "Not found" }, { status: 404 });
    return HttpResponse.json({ messages });
  }),

  // 附件上传（契约 v4）：与后端同等校验（20MB / MIME 白名单）
  mswHttp.post("/api/chat/files", async ({ request }) => {
    const parsed = parseMultipartFile(request.headers.get("content-type") ?? "", await request.arrayBuffer());
    if (!parsed || !parsed.name) {
      return HttpResponse.json({ detail: "file field is required" }, { status: 400 });
    }
    const { name, type, size } = parsed;
    if (size > 20 * 1024 * 1024) {
      return HttpResponse.json({ detail: "file exceeds 20MB limit" }, { status: 413 });
    }
    const allowed = [
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "text/plain",
      "text/markdown",
      "image/png",
      "image/jpeg",
    ];
    const lower = name.toLowerCase();
    const extAllowed = lower.endsWith(".txt") || lower.endsWith(".md");
    if (!extAllowed && !allowed.includes(type)) {
      return HttpResponse.json({ detail: "unsupported file type" }, { status: 400 });
    }
    mockFileSeq += 1;
    const uploaded: UploadedFile = {
      file_id: `f_mock_${mockFileSeq}`,
      name,
      size,
      mime: type || "text/plain",
    };
    await delay(50);
    return HttpResponse.json(uploaded, { status: 201 });
  }),

  mswHttp.post("/api/chat/send", async ({ request }) => {
    const body = (await request.json()) as {
      query?: string;
      inputs?: Record<string, string>;
      files?: string[];
      conversation_id?: string;
    };
    lastChatSendBody = body;
    const query = body.query ?? "";
    const encoder = new TextEncoder();

    // 契约 v5：会话 id 语义 —— 非空且已知则续用（多轮串联），否则新建并登记
    const convId =
      body.conversation_id && dynamicConvs.has(body.conversation_id)
        ? body.conversation_id
        : `dyn-conv-${Date.now()}-${++dynConvSeq}`;
    let conv = dynamicConvs.get(convId);
    if (!conv) {
      conv = {
        appId: 1,
        summary: {
          id: convId,
          title: (query || Object.values(body.inputs ?? {}).join(" ")).slice(0, 20),
          message_count: 0,
          updated_at: new Date().toISOString(),
        },
        messages: [],
      };
      dynamicConvs.set(convId, conv);
    }
    conv.messages.push({
      id: ++dynMsgSeq,
      role: "user",
      content: query || JSON.stringify(body.inputs ?? {}),
      created_at: new Date().toISOString(),
    });
    persistDyn();
    let acc = ""; // 累计 assistant 全文，message_end 时落动态会话（回放/恢复用）

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const send = (event: string, data: unknown) =>
          controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));

        try {
          if (body.inputs && Object.keys(body.inputs).length > 0) {
            // 契约 v3：workflow 应用按表单生成（mock 简单返回）
            send("message", { answer: "已按表单内容生成结果：" });
            acc += "已按表单内容生成结果：";
            await delay(80);
            send("message", { answer: query });
            acc += query;
            await delay(80);
          } else if (query.includes("失败")) {
            await delay(300);
            send("error", { message: "回答生成失败，请重试" });
            // 对齐真机行为：错误后仍发 agent_done（A3 错误态保留；v5 带 conversation_id）
            conv.summary.message_count = conv.messages.length;
            persistDyn();
            send("agent_done", { conversation_id: convId });
            controller.close();
            return;
          }
          for (const seg of mockAnswer(query)) {
            send("message", { answer: seg });
            acc += seg;
            await delay(80);
          }
          if (body.files && body.files.length > 0) {
            // 契约 v4：附件已随消息送达（mock 回执提一句，便于联调观察）
            const receipt = `（已收到 ${body.files.length} 个附件）\n`;
            send("message", { answer: receipt });
            acc += receipt;
            await delay(60);
          }
          send("message_end", { metadata: { usage: { total: 128 } } });
          conv.messages.push({
            id: ++dynMsgSeq,
            role: "assistant",
            content: acc,
            created_at: new Date().toISOString(),
          });
          conv.summary.message_count = conv.messages.length;
          conv.summary.updated_at = new Date().toISOString();
          persistDyn();
          send("agent_done", { conversation_id: convId });
          controller.close();
        } catch {
          controller.error(new Error("mock stream aborted"));
        }
      },
    });

    return new HttpResponse(stream, {
      headers: { "Content-Type": "text/event-stream" },
    });
  }),

  /* ── 知识库（契约 v7）：真实后端透传 Dify，mock 提供种子形状 ── */

  mswHttp.get("/api/kb/datasets", () =>
    HttpResponse.json({
      total: 1,
      has_more: false,
      page: 1,
      limit: 20,
      data: [
        {
          id: "ds-eco-1",
          name: "General Mode-ECO 1",
          document_count: 2,
          word_count: 356,
          indexing_technique: "economy",
          created_at: 1788300000,
        },
      ],
    })
  ),

  mswHttp.get("/api/kb/datasets/:id/documents", () =>
    HttpResponse.json({
      total: 2,
      has_more: false,
      page: 1,
      limit: 100,
      data: [
        {
          id: "doc-1",
          name: "报销政策.pdf",
          word_count: 210,
          hit_count: 3,
          indexing_status: "completed",
          error: null,
          enabled: true,
          created_at: 1788300100,
        },
        {
          id: "doc-2",
          name: "运维手册.md",
          word_count: 146,
          hit_count: 0,
          indexing_status: "indexing",
          error: null,
          enabled: true,
          created_at: 1788300200,
        },
      ],
    })
  ),

  mswHttp.post("/api/kb/datasets/:id/retrieve", async ({ request }) => {
    const body = (await request.json()) as { query?: string };
    const q = body.query ?? "";
    return HttpResponse.json({
      query: { content: q },
      records: [
        {
          score: 0.912,
          segment: {
            content: `「${q}」相关：差旅报销需在出差结束后 7 天内提交。`,
            document: { id: "doc-1", name: "报销政策.pdf" },
          },
        },
      ],
    });
  }),

  /* 契约 v9：库级管理（建/删/授权/审计）与目录 */

  mswHttp.post("/api/kb/datasets", async ({ request }) => {
    const body = (await request.json()) as { name?: string; indexing_technique?: string };
    return HttpResponse.json(
      {
        id: "ds-new-1",
        name: body.name ?? "",
        document_count: 0,
        word_count: 0,
        indexing_technique: body.indexing_technique ?? "high_quality",
        created_at: 1788300000,
      },
      { status: 201 }
    );
  }),

  mswHttp.delete("/api/kb/datasets/:id", () => new HttpResponse(null, { status: 204 })),

  mswHttp.get("/api/kb/datasets/:id/grants", () =>
    HttpResponse.json({
      items: [{ principal_type: "role", principal_id: 2, name: "员工" }],
    })
  ),

  mswHttp.post("/api/kb/datasets/:id/grants", () => HttpResponse.json({}, { status: 201 })),

  mswHttp.delete("/api/kb/datasets/:id/grants/:t/:pid", () =>
    new HttpResponse(null, { status: 204 })
  ),

  mswHttp.get("/api/kb/audit", () =>
    HttpResponse.json({
      total: 1,
      items: [
        {
          id: 1,
          user: "张明",
          action: "dataset_create",
          dataset_id: "ds-eco-1",
          detail: "{\"name\": \"General Mode-ECO 1\"}",
          created_at: "2026-09-02T09:00:00Z",
        },
      ],
    })
  ),
];
