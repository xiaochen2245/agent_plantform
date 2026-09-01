import { PaperClipOutlined, SendOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import { useRef, useState, type KeyboardEvent } from "react";

interface ComposerProps {
  disabled: boolean;
  streaming: boolean;
  onSend: (query: string) => void;
  onStop: () => void;
}

/** 底部输入区：Enter 发送 / Shift+Enter 换行（契约与设计稿约定）。 */
export default function Composer({ disabled, streaming, onSend, onStop }: ComposerProps) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const q = value.trim();
    if (!q || disabled || streaming) return;
    onSend(q);
    setValue("");
    ref.current?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder="输入你的问题，Enter 发送"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <div className="composer-bar">
          <Tooltip title="附件（即将支持）">
            <Button type="text" icon={<PaperClipOutlined />} disabled={streaming} />
          </Tooltip>
          <div style={{ flex: 1 }} />
          {streaming ? (
            <Button danger type="text" icon={<StopOutlined />} onClick={onStop}>
              停止生成
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              disabled={disabled || value.trim().length === 0}
              onClick={submit}
            >
              发送
            </Button>
          )}
        </div>
      </div>
      <div className="composer-helper">支持 PDF / Word / TXT / 图片，单文件 ≤ 20MB</div>
    </div>
  );
}
