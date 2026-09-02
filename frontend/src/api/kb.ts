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

// ---- 契约 v9：库级管理（建/删/授权/审计）----

export async function createDataset(payload: {
  name: string;
  indexing_technique: "high_quality" | "economy";
}): Promise<KbDataset> {
  return (await http.post<KbDataset>("/kb/datasets", payload)).data;
}

export async function deleteDataset(datasetId: string): Promise<void> {
  await http.delete(`/kb/datasets/${datasetId}`);
}

export interface KbGrant {
  principal_type: "user" | "dept" | "role";
  principal_id: number;
  name: string | null;
}

export async function listDatasetGrants(datasetId: string): Promise<KbGrant[]> {
  const resp = await http.get<{ items: KbGrant[] }>(`/kb/datasets/${datasetId}/grants`);
  return resp.data.items ?? [];
}

export async function addDatasetGrant(
  datasetId: string,
  principalType: KbGrant["principal_type"],
  principalId: number
): Promise<void> {
  await http.post(`/kb/datasets/${datasetId}/grants`, {
    principal_type: principalType,
    principal_id: principalId,
  });
}

export async function removeDatasetGrant(
  datasetId: string,
  principalType: KbGrant["principal_type"],
  principalId: number
): Promise<void> {
  await http.delete(`/kb/datasets/${datasetId}/grants/${principalType}/${principalId}`);
}

export interface KbAuditItem {
  id: number;
  user: string | null;
  action: string;
  dataset_id: string | null;
  detail: string | null;
  created_at: string | null;
}

export async function listKbAudit(
  page = 1,
  page_size = 20
): Promise<{ total: number; items: KbAuditItem[] }> {
  return (await http.get<{ total: number; items: KbAuditItem[] }>("/kb/audit", { params: { page, page_size } })).data;
}

export async function retrieveChunks(
  datasetId: string,
  query: string
): Promise<RetrieveRecord[]> {
  // Dify 真实形状：records 为顶层字段（query 只携带查询回显）
  const resp = await http.post<{ query?: unknown; records?: RetrieveRecord[] }>(
    `/kb/datasets/${datasetId}/retrieve`,
    { query }
  );
  return resp.data.records ?? [];
}
