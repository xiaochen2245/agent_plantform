/** 契约 v6：store 思考增量累加。sendChatStream 以 mock 驱动，不起网络。 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatStore } from "./chat";
import type { SendChatHandlers } from "../api/sse";

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
