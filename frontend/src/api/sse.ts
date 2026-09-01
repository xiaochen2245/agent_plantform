import type { ChatSendRequest, ChatSSEEvent, MessageErrorKind } from "../types";

export interface SSEEvent {
  event: string;
  data: string;
}

/**
 * 增量 SSE 解析器：按 \\n\\n 切帧，跨 chunk 缓冲半行。
 * 独立成纯函数便于单测（见 sse.test.ts）。
 */
export function createSSEParser(onEvent: (e: SSEEvent) => void) {
  let buffer = "";

  function handleFrame(frame: string) {
    let event = "message";
    const dataLines: string[] = [];
    for (const rawLine of frame.split("\n")) {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length === 0 && frame.trim() === "") return;
    onEvent({ event, data: dataLines.join("\n") });
  }

  return {
    push(chunk: string) {
      buffer += chunk;
      let sep: number;
      // 帧分隔符 \n\n（兼容 \r\n\r\n）
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        handleFrame(frame);
      }
    },
    end() {
      if (buffer.trim() !== "") {
        handleFrame(buffer);
      }
      buffer = "";
    },
  };
}

export function parseSSEEvent(e: SSEEvent): ChatSSEEvent | null {
  let data: unknown = {};
  if (e.data) {
    try {
      data = JSON.parse(e.data);
    } catch {
      data = {};
    }
  }
  switch (e.event) {
    case "message":
      return { event: "message", data: data as { answer: string } };
    case "reasoning":
      return { event: "reasoning", data: data as { content: string } };
    case "message_end":
      return {
        event: "message_end",
        data: data as { metadata: { usage: { total: number } } },
      };
    case "error":
      return { event: "error", data: data as { message: string } };
    case "agent_done":
      return { event: "agent_done", data: {} };
    default:
      // 未知事件类型：静默忽略（契约 C2 裁决：解析器必须容忍未知事件）
      return null;
  }
}

export interface SendChatHandlers {
  onMessage: (delta: string) => void;
  /** 契约 v6：思考增量（仅上游返回思考内容时出现） */
  onReasoning: (delta: string) => void;
  onMessageEnd: (usage: { total: number }) => void;
  onError: (message: string, kind?: MessageErrorKind) => void;
  onAgentDone: (data: { conversation_id?: string }) => void;
}

/**
 * 发送对话消息并消费 SSE 流（fetch + ReadableStream）。
 * 返回 assistant 落库所需信息由调用方在 onAgentDone 后处理。
 */
export async function sendChatStream(
  body: ChatSendRequest,
  handlers: SendChatHandlers,
  signal?: AbortSignal
): Promise<void> {
  // 相对路径在浏览器 fetch 中合法；Node/jsdom（测试）无 base URL 会抛错，
  // 显式锚定 origin 两边行为一致
  const url = new URL("/api/chat/send", window.location.origin).toString();
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = `请求失败（HTTP ${resp.status}）`;
    try {
      const data = (await resp.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* 非 JSON 错误体，用默认文案 */
    }
    // 契约 v2：未授权 App 调用返回 403 {"detail":"Not authorized for this app"}，重试无意义
    handlers.onError(detail, resp.status === 403 ? "unauthorized" : "generic");
    return;
  }

  const parser = createSSEParser((e) => {
    const parsed = parseSSEEvent(e);
    if (!parsed) return;
    switch (parsed.event) {
      case "message":
        handlers.onMessage(parsed.data.answer ?? "");
        break;
      case "reasoning":
        handlers.onReasoning(parsed.data.content ?? "");
        break;
      case "message_end":
        handlers.onMessageEnd(parsed.data.metadata?.usage ?? { total: 0 });
        break;
      case "error":
        handlers.onError(parsed.data.message || "回答生成失败，请重试");
        break;
      case "agent_done":
        handlers.onAgentDone(parsed.data ?? {});
        break;
    }
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true }));
  }
  parser.push(decoder.decode());
  parser.end();
}
