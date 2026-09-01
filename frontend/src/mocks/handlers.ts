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
