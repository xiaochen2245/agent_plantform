import { PlusOutlined, RobotOutlined } from "@ant-design/icons";
import { Button, Empty, Select, Spin } from "antd";
import { useEffect, useRef } from "react";
import Composer from "../components/Composer";
import MessageItem from "../components/MessageItem";
import ErrorBoundary from "../components/ErrorBoundary";
import WorkflowComposer from "../components/WorkflowComposer";
import { useChatStore } from "../stores/chat";

/** 对话主界面（AppShell 内）：顶栏 Agent 切换 + 居中 760px 线程 + 底部 composer。 */
export default function Chat() {
  const appsLoading = useChatStore((s) => s.appsLoading);
  const loadApps = useChatStore((s) => s.loadApps);
  const activeApp = useChatStore((s) => s.activeApp());
  const messages = useChatStore((s) => s.messagesOfActive());
  const streaming = useChatStore((s) => s.streaming);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const retryLast = useChatStore((s) => s.retryLast);
  const stopStreaming = useChatStore((s) => s.stopStreaming);

  const apps = useChatStore((s) => s.apps);
  const setActiveApp = useChatStore((s) => s.setActiveApp);

  // 契约 v3：带必填输入的 workflow 应用 → 表单式输入区替代普通 composer
  const isWorkflow =
    activeApp?.mode === "workflow" && (activeApp.inputs_schema?.length ?? 0) > 0;

  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void loadApps();
  }, [loadApps]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  return (
    <div className="chat-main">
      <div className="chat-topbar">
        <RobotOutlined style={{ color: "var(--teal)", fontSize: 18 }} />
        <Select
          value={activeApp?.id ?? undefined}
          placeholder="选择 Agent"
          style={{ width: 220 }}
          popupMatchSelectWidth={false}
          disabled={streaming}
          onChange={(id) => setActiveApp(id)}
          options={apps.map((a) => ({ value: a.id, label: a.name }))}
        />
        {activeApp && <div className="agent-desc" style={{ flex: 1, minWidth: 0 }}>{activeApp.description}</div>}
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
                      {isWorkflow ? (
                        <>
                          填写表单，生成你在 <b style={{ color: "var(--ink)" }}>{activeApp.name}</b> 的第一份结果
                        </>
                      ) : (
                        <>
                          向 <b style={{ color: "var(--ink)" }}>{activeApp.name}</b> 提出你的第一个问题
                        </>
                      )}
                    </span>
                  ) : (
                    "选择上方 Agent 开始对话"
                  )
                }
              />
            </div>
          ) : (
            <div className="thread-col">
              <ErrorBoundary scope="local">
                {messages.map((m) => (
                  <MessageItem key={m.id} message={m} onRetry={() => void retryLast()} streaming={streaming} />
                ))}
              </ErrorBoundary>
              <div ref={bottomRef} />
            </div>
          )}
      </div>

      {isWorkflow && activeApp ? (
        <WorkflowComposer
          appName={activeApp.name}
          schema={activeApp.inputs_schema ?? []}
          disabled={!activeApp}
          streaming={streaming}
          onSubmit={(values) =>
            void sendMessage(Object.values(values).join(" "), values)
          }
        />
      ) : (
        <Composer
          disabled={!activeApp}
          streaming={streaming}
          onSend={(q, files) => void sendMessage(q, undefined, files)}
          onStop={stopStreaming}
        />
      )}
    </div>
  );
}
