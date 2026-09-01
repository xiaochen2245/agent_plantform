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
