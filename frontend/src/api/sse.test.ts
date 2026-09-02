import { afterEach, describe, expect, it, vi } from "vitest";
import { createSSEParser, parseSSEEvent, sendChatStream, type SSEEvent } from "./sse";

describe("createSSEParser", () => {
  it("按 \\n\\n 切帧并解析 event/data", () => {
    const events: SSEEvent[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push('event: message\ndata: {"answer":"你好"}\n\nevent: agent_done\ndata: {}\n\n');
    expect(events).toEqual([
      { event: "message", data: '{"answer":"你好"}' },
      { event: "agent_done", data: "{}" },
    ]);
  });

  it("跨 chunk 的半行缓冲：帧被 TCP 边界切开也能还原", () => {
    const events: SSEEvent[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push('event: message\nda');
    parser.push('ta: {"answer":"增量文本"}\n');
    parser.push('\nevent: message_end\ndata: {"metadata":{"usage":{"total":128}}}\n\n');
    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({ event: "message", data: '{"answer":"增量文本"}' });
    expect(events[1].event).toBe("message_end");
  });

  it("无 event 行时默认为 message", () => {
    const events: SSEEvent[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push('data: {"answer":"x"}\n\n');
    expect(events[0].event).toBe("message");
  });

  it("end() 时冲刷残留在缓冲中的尾帧", () => {
    const events: SSEEvent[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push('event: error\ndata: {"message":"boom"}');
    parser.end();
    expect(events).toEqual([{ event: "error", data: '{"message":"boom"}' }]);
  });

  it("兼容 CRLF 行尾", () => {
    const events: SSEEvent[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push('event: message\r\ndata: {"answer":"crlf"}\n\n');
    expect(events[0]).toEqual({ event: "message", data: '{"answer":"crlf"}' });
  });
});

describe("parseSSEEvent", () => {
  it("解析四类契约事件", () => {
    expect(parseSSEEvent({ event: "message", data: '{"answer":"a"}' })).toEqual({
      event: "message",
      data: { answer: "a" },
    });
    expect(parseSSEEvent({ event: "message_end", data: '{"metadata":{"usage":{"total":9}}}' })).toMatchObject({
      event: "message_end",
    });
    expect(parseSSEEvent({ event: "error", data: '{"message":"失败"}' })).toEqual({
      event: "error",
      data: { message: "失败" },
    });
    expect(parseSSEEvent({ event: "agent_done", data: "{}" })).toEqual({
      event: "agent_done",
      data: {},
    });
  });

  it("未知事件类型静默忽略（容忍 Dify 版本差异）", () => {
    expect(parseSSEEvent({ event: "workflow_started", data: "{}" })).toBeNull();
  });

  it("契约 v6：reasoning 事件解析", () => {
    expect(parseSSEEvent({ event: "reasoning", data: '{"content":"思考中"}' })).toEqual({
      event: "reasoning",
      data: { content: "思考中" },
    });
  });

  it("非 JSON data 不抛异常", () => {
    expect(parseSSEEvent({ event: "message", data: "not-json" })).toEqual({
      event: "message",
      data: {},
    });
  });
});

describe("sendChatStream 错误分类", () => {
  const originalFetch = globalThis.fetch;

  function stubFetch(status: number, body: unknown) {
    globalThis.fetch = (async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      })) as typeof fetch;
  }

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("HTTP 403 → onError 携带 unauthorized 分类（detail 透传）", async () => {
    stubFetch(403, { detail: "Not authorized for this app" });
    const onError = vi.fn();
    await sendChatStream({ app_id: 1, query: "hi", conversation_id: "" }, {
      onMessage: () => undefined,
      onReasoning: () => undefined,
      onMessageEnd: () => undefined,
      onError,
      onAgentDone: () => undefined,
    });
    expect(onError).toHaveBeenCalledWith("Not authorized for this app", "unauthorized");
  });

  it("HTTP 500 → onError 携带 generic 分类", async () => {
    stubFetch(500, { detail: "Internal error" });
    const onError = vi.fn();
    await sendChatStream({ app_id: 1, query: "hi", conversation_id: "" }, {
      onMessage: () => undefined,
      onReasoning: () => undefined,
      onMessageEnd: () => undefined,
      onError,
      onAgentDone: () => undefined,
    });
    expect(onError).toHaveBeenCalledWith("Internal error", "generic");
  });
});

describe("usage 归一化（A6）", () => {
  it("契约形状 total 直接透传", async () => {
    const { normalizeUsage } = await import("./sse");
    expect(normalizeUsage({ total: 128 })).toEqual({ total: 128 });
  });

  it("真机 Dify 的 total_tokens 映射为 total", async () => {
    const { normalizeUsage } = await import("./sse");
    expect(normalizeUsage({ total_tokens: 4424 })).toEqual({ total: 4424 });
  });

  it("缺失时归零（不再渲染 tokens: undefined）", async () => {
    const { normalizeUsage } = await import("./sse");
    expect(normalizeUsage(undefined)).toEqual({ total: 0 });
  });
});
