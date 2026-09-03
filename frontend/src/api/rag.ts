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
