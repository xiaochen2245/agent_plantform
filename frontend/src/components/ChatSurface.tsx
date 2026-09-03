import { useEffect, useRef, useState } from "react";
import { Empty, Spin, Tag } from "antd";
import { RobotOutlined } from "@ant-design/icons";
import Composer from "./Composer";
import Markdown from "./Markdown";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  refs?: string[];
}

export interface ChatStreamHandlers {
  onDelta: (text: string) => void;
  onRefs: (refs: string[]) => void;
}

export interface ChatSurfaceProps {
  /** 顶栏标题（应用名） */
  title: string;
  description?: string;
  /** 空态引导文案 */
  placeholder: string;
  /** 各应用自己的流式后端：接收用户问题+历史，通过 handlers 回吐增量与引用 */
  streamAnswer: (query: string, history: ChatTurn[], handlers: ChatStreamHandlers) => Promise<void>;
}

/**
 * 通用对话面板（全高，复用旧对话布局样式与 Composer）。
 * 知识库问答 / 文档审查问答 / 智能比对问答 共用同一交互骨架。
 */
export default function ChatSurface({ title, description, placeholder, streamAnswer }: ChatSurfaceProps) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: busy ? "auto" : "smooth" });
    } else {
      el.scrollTop = el.scrollHeight;
    }
  }, [turns, busy]);

  const send = async (query: string) => {
    if (busy) return;
    const history = [...turns, { role: "user" as const, content: query }];
    setTurns([...history, { role: "assistant", content: "" }]);
    setBusy(true);
    let acc = "";
    let refs: string[] = [];
    try {
      await streamAnswer(query, turns, {
        onDelta: (t) => {
          acc += t;
          setTurns((cur) => {
            const copy = [...cur];
            copy[copy.length - 1] = { role: "assistant", content: acc, refs };
            return copy;
          });
        },
        onRefs: (r) => {
          refs = r;
          setTurns((cur) => {
            const copy = [...cur];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { ...last, refs: r };
            return copy;
          });
        },
      });
    } catch {
      setTurns((cur) => {
        const copy = cur.slice(0, -1);
        return copy.length && copy[copy.length - 1].role === "user" ? copy : copy;
      });
    } finally {
      setBusy(false);
      if (!acc) {
        // 无任何增量：移除空气泡
        setTurns((cur) => (cur[cur.length - 1]?.role === "assistant" && !cur[cur.length - 1].content ? cur.slice(0, -1) : cur));
      }
    }
  };

  return (
    <div className="chat-main">
      <div className="chat-topbar">
        <RobotOutlined style={{ color: "var(--teal)", fontSize: 18 }} />
        <span className="agent-name">{title}</span>
        {description && <div className="agent-desc" style={{ flex: 1, minWidth: 0 }}>{description}</div>}
      </div>
      <div className="chat-thread" ref={threadRef}>
        {busy && turns.length === 1 ? (
          <div style={{ display: "flex", justifyContent: "center", marginTop: 120 }}><Spin /></div>
        ) : turns.length === 0 ? (
          <div className="empty-guide">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ color: "var(--text-muted)" }}>{placeholder}</span>} />
          </div>
        ) : (
          <div className="thread-col">
            {turns.map((t, i) =>
              t.role === "user" ? (
                <div key={i} className="msg-user">{t.content}</div>
              ) : (
                <div key={i} className="msg-assistant">
                  <Markdown content={t.content || "…"} />
                  {t.refs && t.refs.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      {t.refs.slice(0, 3).map((r, k) => (
                        <Tag key={k} style={{ fontSize: 12 }}>来源{k + 1}: {r.slice(0, 50)}</Tag>
                      ))}
                    </div>
                  )}
                </div>
              ),
            )}
          </div>
        )}
      </div>
      <Composer disabled={false} streaming={busy} onSend={(q) => void send(q)} onStop={() => {}} />
    </div>
  );
}
