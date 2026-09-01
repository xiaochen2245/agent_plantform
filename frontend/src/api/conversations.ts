import { http } from "./http";
import type { ConversationSummary } from "../types";

/** 会话详情消息（回放用）。
 * 契约 v2：GET /api/conversations/{id}/messages → {"messages":[...]}（按 created_at asc）。
 */

export interface ConversationMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export async function fetchConversations(appId: number): Promise<ConversationSummary[]> {
  const data = (await http.get<{ items: ConversationSummary[] }>("/conversations", { params: { app_id: appId } })).data;
  return data.items ?? [];
}

export async function fetchConversationMessages(conversationId: string): Promise<ConversationMessage[]> {
  const data = (await http.get<{ messages: ConversationMessage[] }>(`/conversations/${conversationId}/messages`)).data;
  return data.messages ?? [];
}
