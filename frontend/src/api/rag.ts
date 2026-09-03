/** RAG 网关客户端（/api/rag/*，部门租户自动路由）。 */
import { http } from "./http";

export interface RagDataset {
  id: string;
  name: string;
  document_count?: number;
  chunk_count?: number;
}

export interface RagDocument {
  id: string;
  name: string;
  run: "UNSTART" | "RUNNING" | "DONE" | "FAIL" | "CANCEL";
  progress?: number;
}

export interface RagChunk {
  content: string | null;
  similarity: number | null;
  document_id?: string;
}

export const ragApi = {
  datasets: () => http.get<{ data: RagDataset[] }>("/rag/datasets"),
  documents: (datasetId: string) =>
    http.get<{ documents: RagDocument[] }>(`/rag/datasets/${datasetId}/documents`),
  upload: (datasetId: string, file: File) => {
    const form = new FormData();
    form.append("files", file);
    return http.post<{ accepted: RagDocument[] }>(
      `/rag/datasets/${datasetId}/documents`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } },
    );
  },
  tag: (datasetId: string, docId: string) =>
    http.post<{ meta_fields: Record<string, string> }>(
      `/rag/datasets/${datasetId}/documents/${docId}/tag`,
    ),
  ensureChat: () => http.get<{ chat_id: string }>("/rag/chat/assistant"),
  deleteDataset: (id: string) => http.delete(`/rag/datasets/${id}`),
  updateDataset: (id: string, body: { name?: string; description?: string }) =>
    http.patch(`/rag/datasets/${id}`, body),
  createDataset: (name: string, description = "") =>
    http.post<{ id: string; name: string }>("/rag/datasets", { name, description }),
  deleteDocuments: (datasetId: string, ids: string[]) =>
    http.delete(`/rag/datasets/${datasetId}/documents`, { data: { ids } }),
  retrieve: (
    question: string,
    datasetIds: string[],
    discipline?: string,
  ) =>
    http.post<{ chunks: RagChunk[] }>("/rag/retrieval", {
      question,
      dataset_ids: datasetIds,
      top_k: 5,
      ...(discipline
        ? {
            metadata_condition: {
              logic: "and",
              conditions: [
                { name: "discipline", comparison_operator: "is", value: discipline },
              ],
            },
          }
        : {}),
    }),
};
export const ragChatUrl = "/api/rag/chat/completions";

/** 解析 RAGFlow OpenAI 兼容 SSE：增量经 onDelta，引用经 onRefs。 */
export async function streamRagChat(
  messages: { role: string; content: string }[],
  handlers: { onDelta: (t: string) => void; onRefs: (r: string[]) => void },
): Promise<void> {
  const resp = await fetch(ragChatUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ messages }),
  });
  if (!resp.ok || !resp.body) throw new Error(`问答服务异常 (HTTP ${resp.status})`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      let j: any;
      try { j = JSON.parse(payload); } catch { continue; }
      const delta = j.choices?.[0]?.delta;
      if (delta?.content) handlers.onDelta(delta.content);
      const ref = delta?.reference ?? j.choices?.[0]?.message?.reference;
      if (ref?.chunks) {
        const list = Array.isArray(ref.chunks) ? ref.chunks : Object.values(ref.chunks);
        handlers.onRefs(list.map((c: any) => String(c?.content ?? c ?? "").slice(0, 60)));
      }
    }
  }
}

export interface RagChatSession {
  id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

export interface RagSessionMessage {
  role: "user" | "assistant";
  content: string;
  created_at?: string;
}

/** #38 会话持久化：ChatSurface 多轮不丢，审查/比对应用同接口复用。 */
export const ragSessions = {
  list: () => http.get<{ sessions: RagChatSession[] }>("/rag/chat/sessions"),
  create: (title: string) => http.post<{ id: string; title: string }>("/rag/chat/sessions", { title }),
  messages: (id: string) =>
    http.get<{ messages: RagSessionMessage[] }>(`/rag/chat/sessions/${id}/messages`),
  sync: (id: string, messages: { role: "user" | "assistant"; content: string }[], title?: string) =>
    http.put<{ id: string; message_count: number }>(`/rag/chat/sessions/${id}/messages`, {
      messages,
      ...(title ? { title } : {}),
    }),
  remove: (id: string) => http.delete(`/rag/chat/sessions/${id}`),
};
