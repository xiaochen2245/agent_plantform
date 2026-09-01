import { FormOutlined, SendOutlined } from "@ant-design/icons";
import { Button, Form, Input, Modal } from "antd";
import { useState } from "react";
import type { AppInputField } from "../types";

interface WorkflowComposerProps {
  appName: string;
  schema: AppInputField[];
  disabled: boolean;
  streaming: boolean;
  onSubmit: (values: Record<string, string>) => void;
}

/**
 * 工作流应用输入区（契约 v3）：替代普通 composer ——
 * 点按钮弹表单（按 inputs_schema 渲染 Input/TextArea），提交后以 inputs 发送。
 */
export default function WorkflowComposer({ appName, schema, disabled, streaming, onSubmit }: WorkflowComposerProps) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const submit = async () => {
    const values = await form.validateFields();
    onSubmit(values as Record<string, string>);
    setOpen(false);
    form.resetFields();
  };

  return (
    <div className="composer-wrap">
      <div className="composer" style={{ alignItems: "center", padding: "14px 16px" }}>
        <FormOutlined style={{ color: "var(--teal)", fontSize: 16, marginRight: 10 }} />
        <div style={{ flex: 1, color: "var(--text-muted)", fontSize: 13 }}>
          该 Agent 按表单生成结果 —— 填写「{schema.map((f) => f.label || f.name).join("、")}」后运行工作流
        </div>
        <Button
          type="primary"
          icon={<SendOutlined />}
          disabled={disabled || streaming}
          onClick={() => setOpen(true)}
        >
          {streaming ? "生成中…" : "填写并生成"}
        </Button>
      </div>
      <div className="composer-helper">{appName} · 工作流模式，每次运行相互独立</div>

      <Modal
        title={`填写表单 · ${appName}`}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
        okText="生成"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          {schema.map((field) => (
            <Form.Item
              key={field.name}
              name={field.name}
              label={field.label || field.name}
              rules={field.required ? [{ required: true, message: `请输入${field.label || field.name}` }] : undefined}
            >
              {field.type === "paragraph" ? <Input.TextArea rows={4} placeholder={`请输入${field.label || field.name}`} /> : <Input placeholder={`请输入${field.label || field.name}`} />}
            </Form.Item>
          ))}
        </Form>
      </Modal>
    </div>
  );
}
