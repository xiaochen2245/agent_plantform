import { http as mswHttp, HttpResponse } from "msw";
import type { AppInfo, ConversationSummary, MeInfo } from "../types";

/**
 * 契约 v1 的 mock 实现（docs/api-contract.md）。
 * 种子账号：admin@company.com / admin123
 */

const MOCK_JWT = "mock-access-token";
const MOCK_REFRESH = "mock-refresh-token";

const ME: MeInfo = {
  id: 1,
  email: "admin@company.com",
  name: "张明",
  roles: ["USER", "PLATFORM_ADMIN"],
  dept_id: null,
};

const APPS: AppInfo[] = [
  { id: 1, name: "IT 运维助手", description: "解答服务器、网络与账号问题", mode: "chat" },
  { id: 2, name: "报销政策问答", description: "差旅与报销规则查询", mode: "chat" },
  { id: 3, name: "代码评审助手", description: "MR 预审与规范检查", mode: "agent" },
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

// TODO(契约缺口)：契约 v1 无 GET /api/conversations/:id/messages；
// 以下回放数据仅 mock 模式提供，真实后端就绪后应补入契约再对接。
const CONVERSATION_MESSAGES: Record<string, Array<{ id: string; role: "user" | "assistant"; content: string; created_at: string }>> = {
  "11111111-1111-1111-1111-111111111111": [
    { id: "m1", role: "user", content: "VPN 连接失败怎么办", created_at: new Date().toISOString() },
    {
      id: "m2",
      role: "assistant",
      content:
        "排查建议：\n· 先确认客户端到网关的连通性（ping 网关地址）；\n· 检查账号是否被临时锁定（连续 5 次失败会锁定 15 分钟）；\n· 若均正常，请联系 IT 服务台提交工单并附错误截图。",
      created_at: new Date().toISOString(),
    },
  ],
};

// ── mock-only：admin 端点（契约 v1 不含，见 api/admin.ts 的 TODO）──
interface MockAdminUser {
  id: number;
  name: string;
  email: string;
  dept: string;
  roles: string[];
  enabled: boolean;
  authorized_app_ids: number[];
}

const ADMIN_USERS: MockAdminUser[] = [
  { id: 1, name: "张明", email: "admin@company.com", dept: "信息技术部", roles: ["USER", "PLATFORM_ADMIN"], enabled: true, authorized_app_ids: [1, 2, 3] },
  { id: 2, name: "李霞", email: "lixia@company.com", dept: "财务部", roles: ["USER"], enabled: true, authorized_app_ids: [2] },
  { id: 3, name: "王强", email: "wangqiang@company.com", dept: "研发中心", roles: ["USER"], enabled: true, authorized_app_ids: [1, 3] },
  { id: 4, name: "赵敏", email: "zhaomin@company.com", dept: "人力资源部", roles: ["USER"], enabled: true, authorized_app_ids: [] },
  { id: 5, name: "陈晨", email: "chenchen@company.com", dept: "市场部", roles: ["USER"], enabled: false, authorized_app_ids: [1] },
];

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
      return new HttpResponse(null, {
        status: 200,
        headers: authCookiePairs(),
      });
    }
    return HttpResponse.json({ detail: "Invalid credentials" }, { status: 401 });
  }),

  mswHttp.post("/api/auth/refresh", ({ request }) => {
    if (hasCookie(request, "refresh_token_cookie")) {
      return new HttpResponse(null, {
        status: 200,
        headers: authCookiePairs(),
      });
    }
    return HttpResponse.json({ detail: "Invalid refresh token" }, { status: 401 });
  }),

  mswHttp.post("/api/auth/logout", () => {
    return new HttpResponse(null, {
      status: 200,
      headers: [
        ["Set-Cookie", "access_token_cookie=; Path=/; SameSite=Strict; Max-Age=0"],
        ["Set-Cookie", "refresh_token_cookie=; Path=/; SameSite=Strict; Max-Age=0"],
      ] as [string, string][],
    });
  }),

  mswHttp.get("/api/auth/me", ({ request }) => {
    if (hasCookie(request, "access_token_cookie")) {
      return HttpResponse.json(ME);
    }
    return HttpResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }),

  mswHttp.get("/api/apps/me", () => {
    return HttpResponse.json({ apps: APPS });
  }),

  mswHttp.get("/api/conversations", ({ request }) => {
    const appId = new URL(request.url).searchParams.get("app_id") ?? "1";
    return HttpResponse.json({ items: CONVERSATIONS.filter(() => appId === "1") });
  }),

  // mock-only：会话消息回放（契约缺口，见上方 TODO）
  mswHttp.get("/api/conversations/:id/messages", ({ params }) => {
    const id = String(params.id);
    const items = CONVERSATION_MESSAGES[id];
    if (!items) return HttpResponse.json({ detail: "Not found" }, { status: 404 });
    return HttpResponse.json({ items });
  }),

  // ── mock-only：admin 端点 ──
  mswHttp.get("/api/admin/stats", () => {
    return HttpResponse.json({
      employees: ADMIN_USERS.length,
      enabled: ADMIN_USERS.filter((u) => u.enabled).length,
      admins: ADMIN_USERS.filter((u) => u.roles.includes("PLATFORM_ADMIN")).length,
    });
  }),

  mswHttp.get("/api/admin/users", () => {
    return HttpResponse.json({ users: ADMIN_USERS });
  }),

  mswHttp.put("/api/admin/users/:id/authorizations", async ({ request, params }) => {
    const id = Number(params.id);
    const body = (await request.json()) as { app_ids?: number[] };
    const user = ADMIN_USERS.find((u) => u.id === id);
    if (!user) return HttpResponse.json({ detail: "Not found" }, { status: 404 });
    user.authorized_app_ids = body.app_ids ?? [];
    return HttpResponse.json({ ok: true });
  }),

  mswHttp.post("/api/chat/send", async ({ request }) => {
    const body = (await request.json()) as { query?: string };
    const query = body.query ?? "";
    const encoder = new TextEncoder();

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const send = (event: string, data: unknown) =>
          controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));

        try {
          if (query.includes("失败")) {
            await delay(300);
            send("error", { message: "回答生成失败，请重试" });
            controller.close();
            return;
          }
          for (const seg of mockAnswer(query)) {
            send("message", { answer: seg });
            await delay(80);
          }
          send("message_end", { metadata: { usage: { total: 128 } } });
          send("agent_done", {});
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
];
