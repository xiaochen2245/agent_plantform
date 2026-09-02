import { BulbOutlined, DownOutlined, FileImageOutlined, FileMarkdownOutlined, FilePdfOutlined, FileTextOutlined, FileWordOutlined, LockFilled, PaperClipOutlined, WarningFilled } from "@ant-design/icons";
import { Button } from "antd";
import Markdown from "./Markdown";
import { fileKindOf, formatFileSize } from "../utils/files";
import "../styles/markdown.css";
import "../styles/reasoning.css";
import { useEffect, useRef, useState } from "react";
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
  const shown = useTypewriter(text, streaming); // 同正文：成簇增量打字机平滑（契约 v9 观感统一）
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
      {open && <pre className="panel-body">{shown}</pre>}
    </div>
  );
}

/** 打字机平滑（Cherry Studio 式逐字输出）。
 *
 * 实测上游 SSE 是成簇到达的（同一毫秒内十几个小增量、随后数百 ms 静默），
 * 叠加 store 层 80ms 合批后视觉上就是"一段一段蹦"。此 hook 把展示节奏与到达
 * 节奏解耦：增量按 30ms 节拍吐字，积压越多步长越大（每 tick 吐 backlog 的
 * ~20%、至少 1 字，~150ms 内自动追平，不会越落越远）。
 * active=false（流结束/错误/历史消息）时直接显示全文，不打字。 */
function useTypewriter(text: string, active: boolean): string {
  const [shown, setShown] = useState(text);
  const lenRef = useRef(text.length); // 已吐出长度；挂载时从当前全文起（不重放旧内容）
  useEffect(() => {
    if (!active) {
      lenRef.current = text.length;
      setShown(text);
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      const backlog = text.length - lenRef.current;
      if (backlog <= 0) return; // 吐完即停；下一次 text 变化会重启 effect
      lenRef.current += Math.max(1, Math.ceil(backlog * 0.2));
      setShown(text.slice(0, lenRef.current));
      timer = setTimeout(tick, 30);
    };
    tick();
    return () => clearTimeout(timer);
  }, [text, active]);
  return shown;
}

/** 单条消息：用户=右侧青色气泡；AI=全宽文档式排版（设计稿约定）。 */
export default function MessageItem({ message, onRetry, streaming }: MessageItemProps) {
  // hooks 必须先于下方所有 early return 调用：用户消息/错误卡走 active=false 直显
  const isStreamingMsg = message.role === "assistant" && message.status === "streaming";
  const shownContent = useTypewriter(message.content ?? "", isStreamingMsg);

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
        <Markdown content={shownContent} />
      ) : (
        <span className="placeholder">…</span>
      )}
      {isStreamingMsg && streaming && <span className="stream-cursor" aria-label="生成中" />}
      {message.status === "done" && message.usage && (
        <span className="usage">tokens: {message.usage.total}</span>
      )}
    </div>
  );
}
