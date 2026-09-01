/** 与 docs/api-contract.md v1 对齐的共享类型。 */

export interface MeInfo {
  id: number;
  email: string;
  name: string;
  roles: string[];
  dept_id: number | null;
}

export interface AppInputField {
  name: string;
  label?: string;
  type: "text" | "paragraph" | string;
  required?: boolean;
}

export interface AppInfo {
  id: number;
  name: string;
  description: string;
  mode: "chat" | "agent" | "workflow" | string;
  /** 契约 v3：仅 workflow 应用非空 */
  inputs_schema?: AppInputField[] | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

export type MessageRole = "user" | "assistant";
export type MessageStatus = "done" | "streaming" | "error";
/** 错误分类：unauthorized=403 未授权（重试无意义）；generic=生成失败/网络错误。 */
export type MessageErrorKind = "unauthorized" | "generic";

export interface ChatMessage {
  id: string;
  conversationId: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  errorKind?: MessageErrorKind;
  usage?: { total: number };
  createdAt: number;
}

/** SSE 事件（契约 Chat 段）。 */
export type ChatSSEEvent =
  | { event: "message"; data: { answer: string } }
  | { event: "message_end"; data: { metadata: { usage: { total: number } } } }
  | { event: "error"; data: { message: string } }
  | { event: "agent_done"; data: Record<string, never> };

export interface ChatSendRequest {
  app_id: number;
  query: string;
  conversation_id: string;
  /** 契约 v3：workflow 应用变量透传 */
  inputs?: Record<string, string>;
}
