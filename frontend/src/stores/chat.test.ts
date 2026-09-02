/** 契约 v6：store 思考增量累加。sendChatStream 以 mock 驱动，不起网络。 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatStore } from "./chat";
import type { SendChatHandlers } from "../api/sse";

const { httpGet } = vi.hoisted(() => ({ httpGet: vi.fn() }));
vi.mock("../api/http", () => ({ http: { get: httpGet } }));

vi.mock("../api/sse", () => ({
  sendChatStream: vi.fn(async (_body: unknown, handlers: SendChatHandlers) => {
    handlers.onReasoning("思考一");
    handlers.onReasoning("思考二");
    handlers.onMessage("答案");
    handlers.onMessageEnd({ total: 5 });
    handlers.onAgentDone({ conversation_id: "conv-real-1" });
  }),
}));

afterEach(() => {
  useChatStore.setState({
    apps: [],
    activeAppId: null,
    messagesByConv: {},
    conversationsByApp: {},
    activeConversationId: null,
    streaming: false,
  });
  vi.clearAllMocks();
});

describe("chat store 思考累加（契约 v6）", () => {
  it("onReasoning 增量累加到 assistant 消息，与正文分开", async () => {
    useChatStore.setState({
      apps: [{ id: 1, name: "IT 运维助手", description: "", mode: "chat" }],
      activeAppId: 1,
    });
    await useChatStore.getState().sendMessage("深思");
    const conv = useChatStore.getState().activeConversationId;
    expect(conv).toBe("conv-real-1"); // 认领 agent_done 回传 id
    const list = useChatStore.getState().messagesByConv["conv-real-1"];
    const assistant = list.find((m) => m.role === "assistant");
    expect(assistant?.reasoning).toBe("思考一思考二");
    expect(assistant?.content).toBe("答案");
    expect(assistant?.status).toBe("done");
  });
});

describe("错误态与中断语义（审查修复 A3/A7）", () => {
  async function seedApp() {
    useChatStore.setState({
      apps: [{ id: 1, name: "IT 运维助手", description: "", mode: "chat" }],
      activeAppId: 1,
    });
  }

  it("A3：error 之后的 agent_done 不覆盖错误态（错误卡与重试保留）", async () => {
    await seedApp();
    const { sendChatStream } = await import("../api/sse");
    vi.mocked(sendChatStream).mockImplementationOnce(async (_b, handlers) => {
      handlers.onError("上游错误", "generic");
      handlers.onAgentDone({ conversation_id: "" });
    });
    await useChatStore.getState().sendMessage("失败");
    const list = useChatStore.getState().messagesOfActive();
    const assistant = list.find((m) => m.role === "assistant");
    expect(assistant?.status).toBe("error");
    expect(assistant?.content).toBe("上游错误");
    expect(assistant?.errorKind).toBe("generic");
  });

  it("A7：AbortError 中断保留部分答案并置 done", async () => {
    await seedApp();
    const { sendChatStream } = await import("../api/sse");
    vi.mocked(sendChatStream).mockImplementationOnce(async (_b, handlers) => {
      handlers.onMessage("部分答案");
      const e = new Error("aborted");
      e.name = "AbortError";
      throw e;
    });
    await useChatStore.getState().sendMessage("随便问");
    const list = useChatStore.getState().messagesOfActive();
    const assistant = list.find((m) => m.role === "assistant");
    expect(assistant?.status).toBe("done");
    expect(assistant?.content).toBe("部分答案");
  });

  it("A8：用户切走后 agent_done 只迁桶不拽回激活", async () => {
    await seedApp();
    const { sendChatStream } = await import("../api/sse");
    vi.mocked(sendChatStream).mockImplementationOnce(async (_b, handlers) => {
      handlers.onMessage("答案");
      // 模拟流中用户切到别的应用
      useChatStore.setState({ activeAppId: null, activeConversationId: null });
      handlers.onAgentDone({ conversation_id: "conv-real-2" });
    });
    await useChatStore.getState().sendMessage("问");
    // 桶已迁移到真实 id，但激活未被抢回
    expect(useChatStore.getState().messagesByConv["conv-real-2"]).toBeTruthy();
    expect(useChatStore.getState().activeConversationId).toBeNull();
  });
});

describe("F2 流式节流合批", () => {
  it("增量先进缓冲，80ms 窗口内合并为一次内容更新（不逐 token 重渲染）", async () => {
    vi.useFakeTimers();
    useChatStore.setState({
      apps: [{ id: 1, name: "IT 运维助手", description: "", mode: "chat" }],
      activeAppId: 1,
    });
    const { sendChatStream } = await import("../api/sse");
    let h!: SendChatHandlers;
    let release!: () => void;
    vi.mocked(sendChatStream).mockImplementationOnce((_b, handlers) => {
      h = handlers;
      return new Promise<void>((res) => {
        release = () => {
          handlers.onAgentDone({ conversation_id: "conv-f2" });
          res();
        };
      });
    });
    const p = useChatStore.getState().sendMessage("节流验证");
    h.onMessage("甲");
    h.onMessage("乙");
    h.onMessage("丙");
    await Promise.resolve(); // 微任务沉淀：缓冲期内不应有内容写入
    const draftList = useChatStore.getState().messagesByConv["__draft__"] ?? [];
    expect(draftList.find((m) => m.role === "assistant")?.content).toBe("");
    vi.advanceTimersByTime(90); // 越过节流窗口 → 一次合批刷入
    const flushed = useChatStore.getState().messagesByConv["__draft__"] ?? [];
    expect(flushed.find((m) => m.role === "assistant")?.content).toBe("甲乙丙");
    release();
    await p;
    expect(useChatStore.getState().activeConversationId).toBe("conv-f2");
    vi.useRealTimers();
  });
});

describe("F3 恢复最近会话", () => {
  it("resumeLatest：列表取最新 → 详情灌桶 → 激活（刷新后多轮不断链）", async () => {
    httpGet.mockImplementation(async (url: string) => {
      if (url === "/conversations") {
        return {
          data: {
            items: [
              { id: "c-latest", title: "上一轮问题", message_count: 2, updated_at: "2026-09-02T01:00:00Z" },
              { id: "c-old", title: "更早", message_count: 2, updated_at: "2026-09-01T01:00:00Z" },
            ],
          },
        };
      }
      return {
        data: {
          messages: [
            { id: 1, role: "user", content: "上一轮问题", created_at: "2026-09-02T01:00:00Z" },
            { id: 2, role: "assistant", content: "上一轮回答", created_at: "2026-09-02T01:00:05Z", reasoning: null },
          ],
        },
      };
    });
    const ok = await useChatStore.getState().resumeLatest(1);
    expect(ok).toBe(true);
    const s = useChatStore.getState();
    expect(s.activeConversationId).toBe("c-latest");
    const list = s.messagesByConv["c-latest"];
    expect(list).toHaveLength(2);
    expect(list[0]).toMatchObject({ role: "user", content: "上一轮问题", status: "done" });
    expect(list[1]).toMatchObject({ role: "assistant", content: "上一轮回答" });
  });

  it("无会话时返回 false 不改状态", async () => {
    httpGet.mockResolvedValueOnce({ data: { items: [] } });
    const ok = await useChatStore.getState().resumeLatest(2);
    expect(ok).toBe(false);
    expect(useChatStore.getState().activeConversationId).toBeNull();
  });
});
