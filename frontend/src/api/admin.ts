import { http } from "./http";

/**
 * 管理端数据封装 —— 契约 v2（docs/api-contract.md「Admin」段），真实后端端点。
 * 仅 PLATFORM_ADMIN 可用（后端 403 兜底，前端另有路由守卫）。
 */

/** 契约 v2 用户形状。status：1 启用 / 0 禁用。 */
export interface AdminUser {
  id: number;
  name: string;
  email: string;
  dept: string | null;
  dept_id?: number | null;
  roles: string[];
  status: number;
  created_at: string;
}

export interface AdminUsersPage {
  total: number;
  items: AdminUser[];
}

export interface ListUsersParams {
  query?: string;
  status?: 0 | 1;
  dept_id?: number;
  page?: number;
  page_size?: number;
}

export async function listUsers(params: ListUsersParams): Promise<AdminUsersPage> {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== ""));
  return (await http.get<AdminUsersPage>("/admin/users", { params: clean })).data;
}

export interface CreateUserPayload {
  name: string;
  email: string;
  password: string;
  dept_id?: number | null;
  roles?: string[];
}

export async function createUser(payload: CreateUserPayload): Promise<AdminUser> {
  return (await http.post<AdminUser>("/admin/users", payload)).data;
}

export interface PatchUserPayload {
  name?: string;
  dept_id?: number | null;
  roles?: string[];
  status?: 0 | 1;
}

export async function patchUser(id: number, payload: PatchUserPayload): Promise<AdminUser> {
  return (await http.patch<AdminUser>(`/admin/users/${id}`, payload)).data;
}

/** 重置密码：返回后端生成的随机新密码（同时失效该用户全部 refresh token）。 */
export async function resetPassword(id: number): Promise<{ password: string }> {
  return (await http.post<{ password: string }>(`/admin/users/${id}/reset_password`)).data;
}

/** 用户级 Agent 授权（全量替换）。 */
export async function getUserApps(id: number): Promise<{ app_ids: number[] }> {
  return (await http.get<{ app_ids: number[] }>(`/admin/users/${id}/apps`)).data;
}

export async function putUserApps(id: number, appIds: number[]): Promise<void> {
  await http.put(`/admin/users/${id}/apps`, { app_ids: appIds });
}

/** 契约 v8：用户级知识库授权（dataset_id 为 Dify 侧 UUID；勾选入口在知识库页授权抽屉）。 */
export async function getUserDatasets(id: number): Promise<{ dataset_ids: string[] }> {
  return (await http.get<{ dataset_ids: string[] }>(`/admin/users/${id}/datasets`)).data;
}

export async function putUserDatasets(id: number, datasetIds: string[]): Promise<void> {
  await http.put(`/admin/users/${id}/datasets`, { dataset_ids: datasetIds });
}

// ── 部门管理 ────────────────────────────────────────────────────────────────

export interface AdminDept {
  id: number;
  name: string;
  parent_id: number | null;
  /** 物化路径，形如 `/1/3/7/`；顶级为 `/<id>/`。 */
  path: string | null;
}

export async function listDepts(): Promise<{ items: AdminDept[] }> {
  return (await http.get<{ items: AdminDept[] }>("/admin/depts")).data;
}

export interface CreateDeptPayload {
  name: string;
  parent_id?: number | null;
}

export async function createDept(payload: CreateDeptPayload): Promise<AdminDept> {
  return (await http.post<AdminDept>("/admin/depts", payload)).data;
}

export interface UpdateDeptPayload {
  name?: string;
  /** 显式传 null 表示移到顶级；不传则保留原父。 */
  parent_id?: number | null;
}

export async function updateDept(id: number, payload: UpdateDeptPayload): Promise<AdminDept> {
  return (await http.patch<AdminDept>(`/admin/depts/${id}`, payload)).data;
}

export async function deleteDept(id: number): Promise<void> {
  await http.delete(`/admin/depts/${id}`);
}

export async function getDeptApps(id: number): Promise<{ app_ids: number[] }> {
  return (await http.get<{ app_ids: number[] }>(`/admin/depts/${id}/apps`)).data;
}

export async function putDeptApps(id: number, appIds: number[]): Promise<void> {
  await http.put(`/admin/depts/${id}/apps`, { app_ids: appIds });
}

// ── 角色管理 ────────────────────────────────────────────────────────────────

export interface AdminRole {
  id: number;
  code: string;
  name: string;
}

export async function listRoles(): Promise<{ items: AdminRole[] }> {
  return (await http.get<{ items: AdminRole[] }>("/admin/roles")).data;
}

export interface CreateRolePayload {
  /** 大写字母开头的 SNAKE_CASE；后端自动 upper()。 */
  code: string;
  name: string;
}

export async function createRole(payload: CreateRolePayload): Promise<AdminRole> {
  return (await http.post<AdminRole>("/admin/roles", payload)).data;
}

export interface UpdateRolePayload {
  name?: string;
}

export async function updateRole(id: number, payload: UpdateRolePayload): Promise<AdminRole> {
  return (await http.patch<AdminRole>(`/admin/roles/${id}`, payload)).data;
}

export async function deleteRole(id: number): Promise<void> {
  await http.delete(`/admin/roles/${id}`);
}

export async function getRoleApps(id: number): Promise<{ app_ids: number[] }> {
  return (await http.get<{ app_ids: number[] }>(`/admin/roles/${id}/apps`)).data;
}

export async function putRoleApps(id: number, appIds: number[]): Promise<void> {
  await http.put(`/admin/roles/${id}/apps`, { app_ids: appIds });
}
