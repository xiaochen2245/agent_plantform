import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

/**
 * 统一 axios 实例：cookie 随行（JWT httpOnly cookie 鉴权，见契约「通用」）。
 */
export const http = axios.create({
  baseURL: "/api",
  withCredentials: true,
});

/** 单飞（single-flight）refresh：并发 401 只触发一次刷新。 */
let refreshInFlight: Promise<void> | null = null;

interface RetriableConfig extends InternalAxiosRequestConfig {
  __retried?: boolean;
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined;
    const url = config?.url ?? "";
    const isAuthEndpoint = url.includes("/auth/login") || url.includes("/auth/refresh");
    const status = error.response?.status;

    if (status === 401 && config && !isAuthEndpoint && !config.__retried) {
      config.__retried = true;
      try {
        if (!refreshInFlight) {
          refreshInFlight = http
            .post("/auth/refresh")
            .then(() => undefined)
            .finally(() => {
              refreshInFlight = null;
            });
        }
        await refreshInFlight;
        return http.request({ ...config });
      } catch {
        // 刷新失败 → 未认证，跳登录页
        if (typeof window !== "undefined") {
          window.location.href = "/login";
        }
      }
    }
    return Promise.reject(error);
  }
);

/** 从 axios 错误中取后端 detail 文案。 */
export function extractDetail(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: string } | undefined)?.detail;
    if (typeof detail === "string" && detail.length > 0) return detail;
  }
  return fallback;
}
