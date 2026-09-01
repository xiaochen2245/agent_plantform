import { HistoryOutlined, PlayCircleOutlined } from "@ant-design/icons";
import { Button, Empty, Segmented, Spin, Tooltip, message } from "antd";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchConversationMessages, type ConversationMessage } from "../api/conversations";
import MessageItem from "../components/MessageItem";
import { useChatStore } from "../stores/chat";
import type { ChatMessage } from "../types";

/** 相对时间：今天显 HH:mm，7 天内显“N 天前”，更早显日期。 */
function relativeTime(iso: string): string {
  const t = dayjs(iso);
  const now = dayjs();
  if (t.isSame(now, "day")) return t.format("HH:mm");
  const days = now.diff(t, "day");
  if (days < 7) return `${days} 天前`;
  return t.format("MM-DD");
}

function toChatMessage(m: ConversationMessage): ChatMessage {
  return {
    id: m.id,
    conversationId: m.id, // 回放场景无活跃会话语义，占位即可
    role: m.role,
    content: m.content,
    status: "done",
    createdAt: dayjs(m.created_at).valueOf(),
  };
}

/** 历史会话：左列会话列表（agent 筛选），右侧只读回放，无 composer。 */
export default function History() {
  const navigate = useNavigate();
  const apps = useChatStore((s) => s.apps);
  const appsLoading = useChatStore((s) => s.appsLoading);
  const loadApps = useChatStore((s) => s.loadApps);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const conversationsByApp = useChatStore((s) => s.conversationsByApp);
  const messagesByConv = useChatStore((s) => s.messagesByConv);
  const resumeConversation = useChatStore((s) => s.resumeConversation);

  const [appFilter, setAppFilter] = useState<number | "all">("all");
  const [listLoading, setListLoading] = useState(false);
  const [selected, setSelected] = useState<{ appId: number; convId: string; title: string } | null>(null);
  const [replay, setReplay] = useState<ChatMessage[] | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);

  useEffect(() => {
    if (apps.length === 0) void loadApps();
  }, [apps.length, loadApps]);

  useEffect(() => {
    let cancelled = false;
    const appIds = appFilter === "all" ? apps.map((a) => a.id) : [appFilter];
    if (appIds.length === 0) return;
    setListLoading(true);
    Promise.all(appIds.map((id) => loadConversations(id)))
      .catch(() => message.error("会话列表加载失败"))
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [appFilter, apps, loadConversations]);

  const items =
    appFilter === "all"
      ? apps.flatMap((a) => (conversationsByApp[String(a.id)] ?? []).map((c) => ({ ...c, appId: a.id, appName: a.name })))
      : (conversationsByApp[String(appFilter)] ?? []).map((c) => ({
          ...c,
          appId: appFilter,
          appName: apps.find((a) => a.id === appFilter)?.name ?? "",
        }));

  async function openConversation(appId: number, convId: string, title: string) {
    setSelected({ appId, convId, title });
    setReplayLoading(true);
    setReplay(null);
    // 本地缓存优先（当前会话在线程里有完整消息）；否则拉详情端点（mock 提供；真实后端未建 → 空态兜底）
    const local = messagesByConv[convId];
    if (local && local.length > 0) {
      setReplay(local);
      setReplayLoading(false);
      return;
    }
    try {
      const msgs = await fetchConversationMessages(convId);
      setReplay(msgs.map(toChatMessage));
    } catch {
      setReplay([]); // TODO(契约缺口)：真实后端暂无 /api/conversations/:id/messages，展示空态
    } finally {
      setReplayLoading(false);
    }
  }

  return (
    <div className="history-page">
      <div className="page-header">
        <HistoryOutlined style={{ color: "var(--teal)" }} />
        <h2 className="font-display">历史会话</h2>
        <div style={{ flex: 1 }} />
        <Segmented
          value={appFilter === "all" ? "全部" : appFilter}
          onChange={(v) => setAppFilter(v === "全部" ? "all" : (v as number))}
          options={[{ label: "全部", value: "全部" }, ...apps.map((a) => ({ label: a.name, value: a.id }))]}
        />
      </div>

      <div className="history-body">
        <div className="history-list">
          {appsLoading || listLoading ? (
            <div style={{ display: "flex", justifyContent: "center", marginTop: 60 }}>
              <Spin />
            </div>
          ) : items.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史会话" style={{ marginTop: 60 }} />
          ) : (
            items.map((c) => (
              <div
                key={`${c.appId}-${c.id}`}
                className={`history-item${selected?.convId === c.id ? " selected" : ""}`}
                onClick={() => void openConversation(c.appId, c.id, c.title)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") void openConversation(c.appId, c.id, c.title);
                }}
              >
                <div className="title">{c.title}</div>
                <div className="meta">
                  <span className="app-tag">{c.appName}</span>
                  <span>{c.message_count} 条</span>
                  <span>{relativeTime(c.updated_at)}</span>
                </div>
              </div>
            ))
          )}
        </div>

        <div className="history-replay">
          {!selected ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择左侧会话查看回放" style={{ marginTop: 120 }} />
          ) : (
            <>
              <div className="replay-topbar">
                <div className="title">{selected.title}</div>
                <Tooltip title="回到对话页继续该会话">
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    onClick={() => {
                      resumeConversation(selected.appId, selected.convId);
                      navigate("/");
                    }}
                  >
                    继续对话
                  </Button>
                </Tooltip>
              </div>
              <div className="chat-thread">
                {replayLoading ? (
                  <div style={{ display: "flex", justifyContent: "center", marginTop: 80 }}>
                    <Spin />
                  </div>
                ) : replay === null || replay.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    style={{ marginTop: 80 }}
                    description={
                      <span style={{ color: "var(--text-muted)", fontSize: 12.5 }}>
                        暂无回放数据（消息详情端点待后端提供）
                      </span>
                    }
                  />
                ) : (
                  <div className="thread-col">
                    {replay.map((m) => (
                      <MessageItem key={m.id} message={m} onRetry={() => undefined} streaming={false} />
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
