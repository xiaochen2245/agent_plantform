/** 文档审查客户端（/api/review/*，功能①）。 */
import { http } from "./http";

export interface ReviewIssue {
  type: "font" | "size" | "alignment" | "numbering";
  severity: "warn" | "error";
  paragraph: number;
  text: string;
  expected: string;
  actual: string;
  message: string;
}

export interface ReviewReport {
  summary: { total_issues: number; by_type: Record<string, number> };
  issues: ReviewIssue[];
}

export interface TypoCandidate {
  orig: string;
  suggestion: string;
  confidence: number;
  paragraph: number;
  context: string;
}

export const reviewApi = {
  rules: (file: File, template: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("template", template);
    return http.post<ReviewReport>("/review/docx", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  typos: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post<{ model: string; typos: TypoCandidate[] }>("/review/typos", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};
