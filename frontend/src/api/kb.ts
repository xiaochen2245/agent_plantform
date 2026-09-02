import { http } from "./http";

/** 知识库 API 封装 —— 契约 v7（docs/api-contract.md「知识库」段）。
 * 响应形状 = Dify Knowledge API 原样透传；写操作后端限 PLATFORM_ADMIN。 */

export interface KbDataset {
  id: string;
  name: string;
  description?: string | null;
  document_count: number;
  word_count: number;
  indexing_technique: "high_quality" | "economy";
  created_at?: number;
}

export interface DifyPage<T> {
  total: number;
  has_more: boolean;
  page: number;
  limit: number;
  data: T[];
}

export interface KbDocument {
  id: string;
  name: string;
  word_count: number;
  hit_count: number;
  indexing_status: string;
  display_status?: string;
  error: string | null;
  enabled: boolean;
  created_at?: number;
}

export interface RetrieveRecord {
  score: number;
  segment: {
    content: string;
    word_count?: number;
    document?: { id?: string; name?: string } | null;
  };
}

export async function listDatasets(page = 1, page_size = 20): Promise<DifyPage<KbDataset>> {
  return (await http.get<DifyPage<KbDataset>>("/kb/datasets", { params: { page, page_size } })).data;
}

export async function listDocuments(
  datasetId: string,
  opts: { page?: number; page_size?: number; keyword?: string } = {}
): Promise<DifyPage<KbDocument>> {
  const clean = Object.fromEntries(
    Object.entries(opts).filter(([, v]) => v !== undefined && v !== "")
  );
  return (
    await http.get<DifyPage<KbDocument>>(`/kb/datasets/${datasetId}/documents`, { params: clean })
  ).data;
}

export async function createDocByText(
  datasetId: string,
  payload: { name: string; text: string; indexing_technique: string }
): Promise<void> {
  await http.post(`/kb/datasets/${datasetId}/documents/text`, payload);
}

export async function createDocByFile(
  datasetId: string,
  file: File,
  indexingTechnique: string
): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  form.append("indexing_technique", indexingTechnique);
  await http.post(`/kb/datasets/${datasetId}/documents/file`, form);
}

export async function deleteDocument(datasetId: string, documentId: string): Promise<void> {
  await http.delete(`/kb/datasets/${datasetId}/documents/${documentId}`);
}

export async function retrieveChunks(
  datasetId: string,
  query: string
): Promise<RetrieveRecord[]> {
  const resp = await http.post<{ query?: { records?: RetrieveRecord[] } }>(
    `/kb/datasets/${datasetId}/retrieve`,
    { query }
  );
  return resp.data.query?.records ?? [];
}
