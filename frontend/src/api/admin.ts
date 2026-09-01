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
