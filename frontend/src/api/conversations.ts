import { http } from "./http";
import type { ConversationSummary } from "../types";

/** 会话详情消息（回放用）。
 * TODO(契约缺口)：docs/api-contract.md v1 未包含 GET /api/conversations/:id/messages，
 * 该端点目前仅由 MSW mock 提供（见 mocks/handlers.ts）；真实后端就绪前，非 mock
 * 模式下此请求会 404 —— History 页对 404 做了只读空态兜底，不私自为后端定形状。
 */
export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export async function fetchConversations(appId: number): Promise<ConversationSummary[]> {
  const data = (await http.get<{ items: ConversationSummary[] }>("/conversations", { params: { app_id: appId } })).data;
  return data.items ?? [];
}

export async function fetchConversationMessages(conversationId: string): Promise<ConversationMessage[]> {
  const data = (await http.get<{ items: ConversationMessage[] }>(`/conversations/${conversationId}/messages`)).data;
  return data.items ?? [];
}
