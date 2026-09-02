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

/**
 * 单飞刷新，可跨模块复用（SSE fetch 路径同样需要）。
 * navigator.locks 可用时用跨标签页锁（A9）：多标签页同时 401 时串行化刷新，
 * 减少轮转竞态；不支持的環境回退为进程内单飞。
 */
export async function refreshOnce(): Promise<void> {
  if (refreshInFlight) return refreshInFlight;
  const doRefresh = () => http.post("/auth/refresh").then(() => undefined);
  let p: Promise<void>;
  if (typeof navigator !== "undefined" && typeof navigator.locks?.request === "function") {
    p = navigator.locks.request("auth-refresh", doRefresh);
  } else {
    p = doRefresh();
  }
  refreshInFlight = p.finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

/** 裸 fetch 探测 /auth/me（不走 axios 拦截器，避免 401 递归）。 */
async function probeMe(): Promise<boolean> {
  try {
    const resp = await fetch(new URL("/api/auth/me", window.location.origin).toString(), {
      credentials: "include",
    });
    return resp.ok;
  } catch {
    return false;
  }
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
        await refreshOnce();
        return http.request({ ...config });
      } catch (refreshError) {
        // 分级登出（A5）：仅 refresh 明确 401（会话真失效）才登出；
        // 登出前探测 /me —— 其他标签页可能已轮转出新会话（跨页竞态防误杀）。
        // CSRF 403 / 5xx / 网络抖动：原样抛错，由调用方处理（不再硬跳登录页）。
        const refreshStatus = axios.isAxiosError(refreshError)
          ? refreshError.response?.status
          : undefined;
        if (refreshStatus === 401 && typeof window !== "undefined") {
          const stillAuthenticated = await probeMe();
          if (!stillAuthenticated) {
            window.location.href = "/login";
            return new Promise(() => undefined); // 等待硬跳转，不再向下传播
          }
          return http.request({ ...config });
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
