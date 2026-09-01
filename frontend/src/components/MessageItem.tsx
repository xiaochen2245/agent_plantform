import { WarningFilled } from "@ant-design/icons";
import { Button } from "antd";
import type { ChatMessage } from "../types";

interface MessageItemProps {
  message: ChatMessage;
  onRetry: () => void;
  streaming: boolean;
}

/** 单条消息：用户=右侧青色气泡；AI=全宽文档式排版（设计稿约定）。 */
export default function MessageItem({ message, onRetry, streaming }: MessageItemProps) {
  if (message.role === "user") {
    return <div className="msg-user">{message.content}</div>;
  }

  if (message.status === "error") {
    return (
      <div className="error-card">
        <WarningFilled className="icon" />
        <span style={{ flex: 1 }}>{message.content || "回答生成失败，请重试"}</span>
        <Button size="small" danger type="text" onClick={onRetry}>
          重试
        </Button>
      </div>
    );
  }

  return (
    <div className="msg-assistant">
      {message.content}
      {message.status === "streaming" && streaming && (
        <span className="stream-cursor" aria-label="生成中" />
      )}
      {message.status === "done" && message.usage && (
        <span className="usage">tokens: {message.usage.total}</span>
      )}
    </div>
  );
}
