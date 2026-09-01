import { describe, expect, it } from "vitest";
import { createSSEParser, parseSSEEvent, type SSEEvent } from "./sse";

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

  it("非 JSON data 不抛异常", () => {
    expect(parseSSEEvent({ event: "message", data: "not-json" })).toEqual({
      event: "message",
      data: {},
    });
  });
});
