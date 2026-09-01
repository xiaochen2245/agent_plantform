import { BulbOutlined, DownOutlined, FileImageOutlined, FileMarkdownOutlined, FilePdfOutlined, FileTextOutlined, FileWordOutlined, LockFilled, PaperClipOutlined, WarningFilled } from "@ant-design/icons";
import { Button } from "antd";
import Markdown from "./Markdown";
import { fileKindOf, formatFileSize } from "../utils/files";
import "../styles/markdown.css";
import "../styles/reasoning.css";
import { useEffect, useState } from "react";
import type { ChatMessage, UploadedFile } from "../types";

interface MessageItemProps {
  message: ChatMessage;
  onRetry: () => void;
  streaming: boolean;
}

const KIND_ICONS = {
  pdf: FilePdfOutlined,
  word: FileWordOutlined,
  text: FileTextOutlined,
  markdown: FileMarkdownOutlined,
  image: FileImageOutlined,
  generic: PaperClipOutlined,
} as const;

/** user 消息附件列表（契约 v4）：图标按 mime 映射 + 名称 + 大小。 */
function AttachmentList({ files }: { files: UploadedFile[] }) {
  return (
    <div className="msg-files">
      {files.map((f) => {
        const meta = fileKindOf(f.mime);
        const Icon = KIND_ICONS[meta.kind];
        return (
          <span className="attach-chip readonly" key={f.file_id} data-kind={meta.kind}>
            <Icon style={{ color: meta.color }} />
            <span className="name" title={f.name}>{f.name}</span>
            <span className="size">{formatFileSize(f.size)}</span>
          </span>
        );
      })}
    </div>
  );
}

/** 思考过程面板（契约 v6）：无思考不渲染。
 * 展开策略：生成中且尚无正文时默认展开；正文出现/流结束自动收起；用户手动优先。 */
function ReasoningPanel({ text, streaming, answerEmpty }: { text: string; streaming: boolean; answerEmpty: boolean }) {
  const [open, setOpen] = useState(streaming && answerEmpty);
  const [userToggled, setUserToggled] = useState(false);
  useEffect(() => {
    if (!userToggled) setOpen(streaming && answerEmpty);
  }, [streaming, answerEmpty, userToggled]);
  return (
    <div className={`reasoning-panel${open ? " open" : ""}`}>
      <button
        type="button"
        className="panel-header"
        aria-expanded={open}
        onClick={() => {
          setUserToggled(true);
          setOpen((o) => !o);
        }}
      >
        <BulbOutlined />
        <span>思考过程</span>
        <DownOutlined className="chevron" />
      </button>
      {open && <pre className="panel-body">{text}</pre>}
    </div>
  );
}

/** 单条消息：用户=右侧青色气泡；AI=全宽文档式排版（设计稿约定）。 */
export default function MessageItem({ message, onRetry, streaming }: MessageItemProps) {
  if (message.role === "user") {
    return (
      <div className="msg-user">
        {message.content}
        {message.files && message.files.length > 0 && <AttachmentList files={message.files} />}
      </div>
    );
  }

  if (message.status === "error") {
    // 契约 v2：403 未授权与生成失败是两类错误 —— 前者重试无意义，不给重试按钮
    if (message.errorKind === "unauthorized") {
      return (
        <div className="error-card">
          <LockFilled className="icon" />
          <span style={{ flex: 1 }}>
            未授权：{message.content || "你没有该 Agent 的访问权限，请联系管理员开通"}
          </span>
        </div>
      );
    }
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
      {message.reasoning && (
        <ReasoningPanel
          text={message.reasoning}
          streaming={streaming && message.status === "streaming"}
          answerEmpty={message.content === ""}
        />
      )}
      {message.content ? (
        <Markdown content={message.content} />
      ) : (
        <span className="placeholder">…</span>
      )}
      {message.status === "streaming" && streaming && (
        <span className="stream-cursor" aria-label="生成中" />
      )}
      {message.status === "done" && message.usage && (
        <span className="usage">tokens: {message.usage.total}</span>
      )}
    </div>
  );
}
