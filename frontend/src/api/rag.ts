/** RAG 网关客户端（/api/rag/*，部门租户自动路由）。 */
import { http, refreshOnce } from "./http";

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
  /** 引擎侧失败原因（网关透传时才有值）。 */
  error?: string | null;
}

/** 检索/切片全字段（契约 v2：S1 后端透传，前端不再截断）。 */
export interface RagChunk {
  id?: string;
  content: string | null;
  document_id?: string;
  /** 引擎原样字段：文档名。 */
  document_keyword?: string;
  dataset_id?: string;
  similarity?: number | null;
  term_similarity?: number | null;
  vector_similarity?: number | null;
  positions?: unknown;
  highlight?: string;
  available?: boolean;
  important_keywords?: string[];
}

/** 问答引用卡片形状（SSE reference.chunks 全字段映射）。 */
export interface RagRef {
  content: string;
  document_id?: string;
  document_name?: string;
  dataset_id?: string;
  similarity?: number | null;
  positions?: unknown;
}

/** 检索测试台参数（网关自有 top_n，RAGFlow 弃用的 top_k 不透传）。 */
export interface RagRetrievalQuery {
  question: string;
  dataset_ids: string[];
  discipline?: string;
  similarity_threshold?: number;
  vector_similarity_weight?: number;
  rerank_id?: string;
  keyword?: boolean;
  highlight?: boolean;
  /** 网关截断条数，默认 10。 */
  top_n?: number;
}

export const ragApi = {
  datasets: () => http.get<{ data: RagDataset[] }>("/rag/datasets"),
  documents: (datasetId: string) =>
    http.get<{ documents: RagDocument[] }>(`/rag/datasets/${datasetId}/documents`),
  upload: (datasetId: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
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
  retrieve: (q: RagRetrievalQuery) =>
    http.post<{ chunks: RagChunk[] }>("/rag/retrieval", {
      question: q.question,
      dataset_ids: q.dataset_ids,
      top_n: q.top_n ?? 10,
      ...(q.similarity_threshold != null ? { similarity_threshold: q.similarity_threshold } : {}),
      ...(q.vector_similarity_weight != null
        ? { vector_similarity_weight: q.vector_similarity_weight }
        : {}),
      ...(q.rerank_id ? { rerank_id: q.rerank_id } : {}),
      ...(q.keyword ? { keyword: true } : {}),
      ...(q.highlight ? { highlight: true } : {}),
      ...(q.discipline
        ? {
            metadata_condition: {
              logic: "and",
              conditions: [
                { name: "discipline", comparison_operator: "is", value: q.discipline },
              ],
            },
          }
        : {}),
    }),
  /** 切片列表（契约 v2：page_size 上限 100）。 */
  listChunks: (
    datasetId: string,
    docId: string,
    opts: { keywords?: string; page?: number; page_size?: number } = {},
  ) =>
    http.get<{ chunks: RagChunk[]; total: number }>(
      `/rag/datasets/${datasetId}/documents/${docId}/chunks`,
      {
        params: {
          keywords: opts.keywords || undefined,
          page: opts.page ?? 1,
          page_size: Math.min(opts.page_size ?? 20, 100),
        },
      },
    ),
  getChunk: (datasetId: string, docId: string, chunkId: string) =>
    http.get<RagChunk>(`/rag/datasets/${datasetId}/documents/${docId}/chunks/${chunkId}`),
  /** 仅 PLATFORM_ADMIN（后端校验，前端同口径隐藏控件）。 */
  updateChunk: (
    datasetId: string,
    docId: string,
    chunkId: string,
    body: { content?: string; available?: boolean; important_keywords?: string[] },
  ) =>
    http.patch<RagChunk>(`/rag/datasets/${datasetId}/documents/${docId}/chunks/${chunkId}`, body),
  deleteChunk: (datasetId: string, docId: string, chunkId: string) =>
    http.delete(`/rag/datasets/${datasetId}/documents/${docId}/chunks/${chunkId}`),
  /** 重试解析（鉴权对齐 upload：全登录用户）。 */
  parseDocument: (datasetId: string, docId: string) =>
    http.post(`/rag/datasets/${datasetId}/documents/${docId}/parse`),
};
export const ragChatUrl = "/api/rag/chat/completions";

/** SSE 引用 → 卡片形状（document_keyword 兑底，容忍引擎原样字段）。 */
function toRagRef(c: any): RagRef {
  return {
    content: String(c?.content ?? ""),
    document_id: c?.document_id,
    document_name: c?.document_name ?? c?.document_keyword,
    dataset_id: c?.dataset_id,
    similarity: c?.similarity ?? null,
    positions: c?.positions,
  };
}

export interface RagStreamHandlers {
  onDelta: (t: string) => void;
  /** 引用全字段透传，不截断（契约 v2）。 */
  onRefs: (r: RagRef[]) => void;
  /** 流建立时同芽 cancel 句柄（当前活跃请求）。 */
  onStart?: (cancel: () => void) => void;
}

/** 解析 RAGFlow OpenAI 兼容 SSE：增量经 onDelta，引用经 onRefs；可中止、401 单飞刷新重试一次。 */
export async function streamRagChat(
  messages: { role: string; content: string }[],
  handlers: RagStreamHandlers,
): Promise<void> {
  const ac = new AbortController();
  handlers.onStart?.(() => ac.abort());
  let resp: Response;
  try {
    resp = await fetch(ragChatUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ messages }),
      signal: ac.signal,
    });
  } catch (e) {
    if (ac.signal.aborted) return; // 用户中止：静默结束，保留已流出的部分内容
    throw e;
  }
  if (resp.status === 401) {
    await refreshOnce(); // 单飞刷新（http.ts），成功后重试一次
    return streamRagChat(messages, handlers);
  }
  if (!resp.ok || !resp.body) throw new Error(`问答服务异常 (HTTP ${resp.status})`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
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
          handlers.onRefs(list.map(toRagRef));
        }
      }
    }
  } catch (e) {
    if (ac.signal.aborted) return;
    throw e;
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
