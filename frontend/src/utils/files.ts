/** 文件上传工具（契约 v4）：前端预校验常量/规则 + 展示辅助。
 * 后端会做同样校验（双重防线），前端预校验避免无谓上传。
 */

export const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20MB

export const ALLOWED_MIME_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
  "image/png",
  "image/jpeg",
] as const;

/** input[type=file] 的 accept 属性（含常见 .txt/.md 别名兜底）。 */
export const ACCEPT_ATTR = [...ALLOWED_MIME_TYPES, ".txt", ".md"].join(",");

/** 预校验：返回 null 表示通过，否则返回用户可读错误文案（每个文件独立提示）。 */
export function validateFile(file: File): string | null {
  if (file.size > MAX_FILE_SIZE) {
    return `「${file.name}」超过 20MB 上限`;
  }
  // 部分浏览器/OS 对 .txt/.md 不给准确 MIME，按扩展名兜底放行
  const lower = file.name.toLowerCase();
  const extAllowed = lower.endsWith(".txt") || lower.endsWith(".md");
  if (!extAllowed && !(ALLOWED_MIME_TYPES as readonly string[]).includes(file.type)) {
    return `「${file.name}」类型不支持，仅支持 PDF / Word / TXT / Markdown / PNG / JPG`;
  }
  return null;
}

/** B/KB/MB 展示（<1KB 显 B，<1MB 显 KB 保留 1 位，其余 MB 保留 1 位）。 */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export type FileKind = "pdf" | "word" | "text" | "markdown" | "image" | "generic";

/** mime → 展示元数据（kind 由渲染层映射为图标；color 为图标/徽标用色）。 */
export function fileKindOf(mime: string): { kind: FileKind; color: string } {
  switch (mime) {
    case "application/pdf":
      return { kind: "pdf", color: "#DC2626" };
    case "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
      return { kind: "word", color: "#2563EB" };
    case "text/plain":
      return { kind: "text", color: "#64748B" };
    case "text/markdown":
      return { kind: "markdown", color: "#0F766E" };
    case "image/png":
    case "image/jpeg":
      return { kind: "image", color: "#D97706" };
    default:
      return { kind: "generic", color: "#94A3B8" };
  }
}
