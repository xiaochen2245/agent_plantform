/**
 * 版本陈旧检测（僵尸标签页问题）：前端构建指纹 vs 后端 /api/version。
 *
 * 语义：仅当两端都注入了真实版本（非 'dev'）且不一致时判定陈旧——
 * 本地 dev（前端未注入为 'dev'，后端 git sha）不会误报。
 */

export const BUILD_SHA: string = __BUILD_SHA__;

export interface VersionInfo {
  version: string;
}

export async function fetchServerVersion(): Promise<string | null> {
  try {
    const resp = await fetch("/api/version", { credentials: "include" });
    if (!resp.ok) return null;
    const data = (await resp.json()) as Partial<VersionInfo>;
    return typeof data.version === "string" && data.version !== "" ? data.version : null;
  } catch {
    return null; // 网络失败静默：检测不得干扰正常使用
  }
}

/** serverVersion 与构建指纹不一致（且均已注入）→ 需要提示刷新；buildSha 参数仅为可测性 */
export function isStale(serverVersion: string | null, buildSha: string = __BUILD_SHA__): boolean {
  if (!serverVersion) return false;
  if (serverVersion === "dev" || buildSha === "dev") return false;
  return serverVersion !== buildSha;
}

export const VERSION_CHECK_INTERVAL_MS = 15 * 60 * 1000;
