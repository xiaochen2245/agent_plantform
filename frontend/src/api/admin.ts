import { http } from "./http";

/**
 * 管理端数据封装。
 * TODO(后端未建)：契约 v1 不含 admin 端点，以下请求当前仅由 MSW mock 提供
 * （mocks/handlers.ts 中标注 mock-only 的四条）。真实后端就绪后仅需删除
 * 对应 mock handler，本文件调用方零改动。
 */

export interface AdminUser {
  id: number;
  name: string;
  email: string;
  dept: string;
  roles: string[];
  enabled: boolean;
  authorized_app_ids: number[];
}

export interface AdminStats {
  employees: number;
  enabled: number;
  admins: number;
}

export async function getAdminUsers(): Promise<AdminUser[]> {
  const data = (await http.get<{ users: AdminUser[] }>("/admin/users")).data;
  return data.users ?? [];
}

export async function getAdminStats(): Promise<AdminStats> {
  return (await http.get<AdminStats>("/admin/stats")).data;
}

export async function putUserAuthorizations(userId: number, appIds: number[]): Promise<void> {
  await http.put(`/admin/users/${userId}/authorizations`, { app_ids: appIds });
}
