/** 文件工具单测（契约 v4）：预校验分支 / 大小格式化 / mime 映射。 */
import { describe, expect, it } from "vitest";
import { fileKindOf, formatFileSize, validateFile } from "./files";

function fakeFile(name: string, type: string, size: number): File {
  // validateFile 只读 size/type/name，不必构造真实 Blob（避免测试里分配 20MB+ 内存）
  return { name, type, size } as File;
}

describe("validateFile 前端预校验", () => {
  it("白名单内且未超限 → 通过（null）", () => {
    expect(validateFile(fakeFile("报告.pdf", "application/pdf", 1024))).toBeNull();
    expect(validateFile(fakeFile("说明.md", "text/markdown", 4096))).toBeNull();
    expect(validateFile(fakeFile("截图.png", "image/png", 5 * 1024 * 1024))).toBeNull();
  });

  it("超过 20MB → 超限文案，且不触发后续 mime 判断", () => {
    const msg = validateFile(fakeFile("大文件.pdf", "application/pdf", 20 * 1024 * 1024 + 1));
    expect(msg).toContain("超过 20MB 上限");
    expect(msg).toContain("大文件.pdf");
  });

  it("非白名单 MIME → 类型不支持文案", () => {
    const msg = validateFile(fakeFile("程序.zip", "application/zip", 512));
    expect(msg).toContain("类型不支持");
    expect(msg).toContain("程序.zip");
  });

  it("MIME 缺失但扩展名 .txt/.md → 放行（OS 不给准确 MIME 的兜底）", () => {
    expect(validateFile(fakeFile("日志.txt", "", 128))).toBeNull();
    expect(validateFile(fakeFile("笔记.md", "application/octet-stream", 128))).toBeNull();
  });
});

describe("formatFileSize", () => {
  it("B/KB/MB 三档", () => {
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("非法输入归零", () => {
    expect(formatFileSize(-1)).toBe("0 B");
    expect(formatFileSize(Number.NaN)).toBe("0 B");
  });
});

describe("fileKindOf mime 映射", () => {
  it("六类 kind 与用色", () => {
    expect(fileKindOf("application/pdf").kind).toBe("pdf");
    expect(fileKindOf("text/markdown").color).toBe("#0F766E");
    expect(fileKindOf("image/jpeg").kind).toBe("image");
    expect(fileKindOf("application/zip").kind).toBe("generic");
  });
});
