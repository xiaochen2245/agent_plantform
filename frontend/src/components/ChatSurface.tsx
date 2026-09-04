import { useEffect, useRef, useState } from "react";
import { Empty, Spin, Tag } from "antd";
import { RobotOutlined } from "@ant-design/icons";
import Composer from "./Composer";
import Markdown from "./Markdown";
import type { RagRef } from "../api/rag";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  /** 引用卡片全字段（契约 v2，不再截断为字符串）。 */
  refs?: RagRef[];
}

export interface ChatStreamHandlers {
  onDelta: (text: string) => void;
  onRefs: (refs: RagRef[]) => void;
  /** 流建立时注册 cancel 句柄，供 onStop 中止。 */
  onStart?: (cancel: () => void) => void;
}

/** #38 会话持久化挂点：load 仅挂载/换 key 时调一次；save 每轮完成后调。 */
export interface ChatPersistence {
  load: () => Promise<ChatTurn[]>;
  save: (turns: ChatTurn[]) => Promise<void>;
}

export interface ChatSurfaceProps {
  /** 顶栏标题（应用名） */
  title: string;
  description?: string;
  /** 空态引导文案 */
  placeholder: string;
  /** 各应用自己的流式后端：接收用户问题+历史，通过 handlers 回吐增量与引用 */
  streamAnswer: (query: string, history: ChatTurn[], handlers: ChatStreamHandlers) => Promise<void>;
  /** 可选：接入后多轮持久化（RagKnowledge/Review/Compare 同一接口） */
  persistence?: ChatPersistence;
  /** 可选：点击引用卡片回调（如打开切片抽屉溯源）。 */
  onOpenRef?: (ref: RagRef) => void;
}

/**
 * 通用对话面板（全高，复用旧对话布局样式与 Composer）。
 * 知识库问答 / 文档审查问答 / 智能比对问答 共用同一交互骨架。
 */
export default function ChatSurface({ title, description, placeholder, streamAnswer, persistence, onOpenRef }: ChatSurfaceProps) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let alive = true;
    if (persistence) {
      void persistence.load().then((restored) => {
        if (alive && restored.length) setTurns(restored);
      }).catch(() => { /* 恢复失败降级为空会话，不打断使用 */ });
    }
    return () => { alive = false; };
    // persistence 仅在挂载时消费（换会话由父层用 key 重挂）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    let refs: RagRef[] = [];
    cancelRef.current = null;
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
        onStart: (cancel) => { cancelRef.current = cancel; },
      });
    } catch {
      setTurns((cur) => {
        const copy = cur.slice(0, -1);
        return copy.length && copy[copy.length - 1].role === "user" ? copy : copy;
      });
    } finally {
      setBusy(false);
      cancelRef.current = null;
      if (!acc) {
        // 无任何增量：移除空气泡
        setTurns((cur) => (cur[cur.length - 1]?.role === "assistant" && !cur[cur.length - 1].content ? cur.slice(0, -1) : cur));
      } else if (persistence) {
        const finalTurns = [...history, { role: "assistant" as const, content: acc }];
        try {
          await persistence.save(finalTurns);
        } catch {
          // 保存失败不提示打断对话（下次轮次会再次全量同步）
        }
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
                    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {t.refs.map((r, k) => (
                        <Tag
                          key={k}
                          color="geekblue"
                          style={{ cursor: onOpenRef ? "pointer" : "default", fontSize: 12, maxWidth: "100%" }}
                          onClick={() => onOpenRef?.(r)}
                        >
                          {r.document_name ?? `来源${k + 1}`}
                          {r.similarity != null && ` · ${(r.similarity * 100).toFixed(0)}%`}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>
              ),
            )}
          </div>
        )}
      </div>
      <Composer disabled={false} streaming={busy} onSend={(q) => void send(q)} onStop={() => cancelRef.current?.()} />
    </div>
  );
}
