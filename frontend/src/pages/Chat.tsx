import { PlusOutlined, RobotOutlined } from "@ant-design/icons";
import { Button, Empty, Spin } from "antd";
import { useEffect, useRef } from "react";
import AgentSidebar from "../components/AgentSidebar";
import Composer from "../components/Composer";
import MessageItem from "../components/MessageItem";
import { useChatStore } from "../stores/chat";

/** 对话主界面：左侧 Agent 列表 + 居中 760px 线程 + 底部 composer。 */
export default function Chat() {
  const appsLoading = useChatStore((s) => s.appsLoading);
  const loadApps = useChatStore((s) => s.loadApps);
  const activeApp = useChatStore((s) => s.activeApp());
  const messages = useChatStore((s) => s.messagesOfActive());
  const streaming = useChatStore((s) => s.streaming);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const retryLast = useChatStore((s) => s.retryLast);
  const stopStreaming = useChatStore((s) => s.stopStreaming);

  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void loadApps();
  }, [loadApps]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  return (
    <div className="chat-shell">
      <AgentSidebar />

      <main className="chat-main">
        <div className="chat-topbar">
          <RobotOutlined style={{ color: "var(--teal)", fontSize: 18 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="agent-name">{activeApp?.name ?? "选择一个 Agent"}</div>
            {activeApp && <div className="agent-desc">{activeApp.description}</div>}
          </div>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={streaming || !activeApp}
            onClick={() => useChatStore.setState({ activeConversationId: null })}
          >
            新对话
          </Button>
        </div>

        <div className="chat-thread">
          {appsLoading && messages.length === 0 ? (
            <div style={{ display: "flex", justifyContent: "center", marginTop: 120 }}>
              <Spin />
            </div>
          ) : messages.length === 0 ? (
            <div className="empty-guide">
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  activeApp ? (
                    <span style={{ color: "var(--text-muted)" }}>
                      向 <b style={{ color: "var(--ink)" }}>{activeApp.name}</b> 提出你的第一个问题
                    </span>
                  ) : (
                    "选择左侧 Agent 开始对话"
                  )
                }
              />
            </div>
          ) : (
            <div className="thread-col">
              {messages.map((m) => (
                <MessageItem key={m.id} message={m} onRetry={() => void retryLast()} streaming={streaming} />
              ))}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        <Composer
          disabled={!activeApp}
          streaming={streaming}
          onSend={(q) => void sendMessage(q)}
          onStop={stopStreaming}
        />
      </main>
    </div>
  );
}
