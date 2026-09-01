import {
  FileAddOutlined,
  LoadingOutlined,
  PaperClipOutlined,
  SendOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { Button, Tooltip, message } from "antd";
import { useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { uploadFile } from "../api/files";
import { ACCEPT_ATTR, fileKindOf, formatFileSize, validateFile } from "../utils/files";
import type { UploadedFile } from "../types";

interface ComposerProps {
  disabled: boolean;
  streaming: boolean;
  onSend: (query: string, files: UploadedFile[]) => void;
  onStop: () => void;
}

/** 底部输入区：Enter 发送 / Shift+Enter 换行；回形针 → 选文件 → 预校验 → 上传 → 附件 chip（契约 v4）。 */
export default function Composer({ disabled, streaming, onSend, onStop }: ComposerProps) {
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(0);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const submit = () => {
    const q = value.trim();
    if (!q || disabled || streaming || uploading > 0) return;
    onSend(q, attachments);
    setValue("");
    setAttachments([]);
    ref.current?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  async function handleFiles(e: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = ""; // 允许重复选择同一文件
    for (const file of picked) {
      const invalid = validateFile(file);
      if (invalid) {
        message.error(invalid);
        continue;
      }
      setUploading((n) => n + 1);
      try {
        const uploaded = await uploadFile(file);
        setAttachments((list) => [...list, uploaded]);
      } catch (err) {
        message.error(err instanceof Error ? err.message : `「${file.name}」上传失败，请重试`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        {attachments.length > 0 && (
          <div className="attach-list" aria-label="待发送附件">
            {attachments.map((f) => {
              const meta = fileKindOf(f.mime);
              return (
                <span className="attach-chip" key={f.file_id} data-kind={meta.kind}>
                  <FileAddOutlined style={{ color: meta.color }} />
                  <span className="name" title={f.name}>{f.name}</span>
                  <span className="size">{formatFileSize(f.size)}</span>
                  <button
                    type="button"
                    className="remove"
                    aria-label={`移除 ${f.name}`}
                    onClick={() => setAttachments((list) => list.filter((x) => x.file_id !== f.file_id))}
                  >
                    ×
                  </button>
                </span>
              );
            })}
          </div>
        )}
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder="输入你的问题，Enter 发送"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <div className="composer-bar">
          <Tooltip title={uploading > 0 ? "附件上传中…" : "添加附件（≤ 20MB）"}>
            <Button
              type="text"
              icon={uploading > 0 ? <LoadingOutlined /> : <PaperClipOutlined />}
              disabled={streaming || uploading > 0}
              aria-label="添加附件"
              onClick={() => fileRef.current?.click()}
            />
          </Tooltip>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept={ACCEPT_ATTR}
            style={{ display: "none" }}
            onChange={(e) => void handleFiles(e)}
          />
          <div style={{ flex: 1 }} />
          {streaming ? (
            <Button danger type="text" icon={<StopOutlined />} onClick={onStop}>
              停止生成
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              disabled={disabled || uploading > 0 || value.trim().length === 0}
              onClick={submit}
            >
              发送
            </Button>
          )}
        </div>
      </div>
      <div className="composer-helper">支持 PDF / Word / TXT / Markdown / 图片，单文件 ≤ 20MB</div>
    </div>
  );
}
