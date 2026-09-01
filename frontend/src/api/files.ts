import { AxiosError } from "axios";
import { http } from "./http";
import type { UploadedFile } from "../types";

/** 上传失败：message 为用户可读文案，code 供测试/分支断言。 */
export class UploadError extends Error {
  code: "too-large" | "unsupported" | "failed";

  constructor(message: string, code: UploadError["code"] = "failed") {
    super(message);
    this.name = "UploadError";
    this.code = code;
  }
}

/** 读文件字节：优先原生 Blob.arrayBuffer；jsdom 的 File 无该方法，退回 FileReader。 */
function readFileBytes(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("读取文件失败"));
    reader.readAsArrayBuffer(file);
  });
}

/** 手工编码 multipart/form-data（单字段 file）。
 * 不走 FormData：jsdom/undici 跨 realm 时 axios 会把 FormData 字符串化成
 * "[object FormData]"；自编码在浏览器、node 测试、MSW 拦截层字节级一致。
 */
async function encodeMultipart(file: File): Promise<{ body: Uint8Array; contentType: string }> {
  const boundary = `----agentplatform${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  // 仅转义引号与反斜杠；中文文件名按 UTF-8 字节直出（FastAPI/python-multipart 兼容）
  const safeName = file.name.replace(/["\\]/g, "_");
  const pre = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${safeName}"\r\nContent-Type: ${
    file.type || "application/octet-stream"
  }\r\n\r\n`;
  const post = `\r\n--${boundary}--\r\n`;
  const encoder = new TextEncoder();
  const preBytes = encoder.encode(pre);
  const fileBytes = new Uint8Array(await readFileBytes(file));
  const postBytes = encoder.encode(post);
  const body = new Uint8Array(preBytes.length + fileBytes.length + postBytes.length);
  body.set(preBytes, 0);
  body.set(fileBytes, preBytes.length);
  body.set(postBytes, preBytes.length + fileBytes.length);
  return { body, contentType: `multipart/form-data; boundary=${boundary}` };
}

/** 上传附件（契约 v4）：POST /api/chat/files（multipart 字段 file）。
 * 413/400 映射为友好文案；其余错误给通用重试文案。
 */
export async function uploadFile(file: File): Promise<UploadedFile> {
  const { body, contentType } = await encodeMultipart(file);
  try {
    const resp = await http.post<UploadedFile>("/chat/files", body, {
      headers: { "Content-Type": contentType },
    });
    return resp.data;
  } catch (e) {
    const status = (e as AxiosError).response?.status;
    if (status === 413) {
      throw new UploadError(`「${file.name}」超过 20MB 上限`, "too-large");
    }
    if (status === 400) {
      throw new UploadError(`「${file.name}」类型不支持，仅支持 PDF / Word / TXT / Markdown / PNG / JPG`, "unsupported");
    }
    throw new UploadError(`「${file.name}」上传失败，请重试`, "failed");
  }
}
