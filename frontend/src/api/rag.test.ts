import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("./http", () => ({
  http: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  refreshOnce: vi.fn(async () => undefined),
}));

import { refreshOnce } from "./http";
import { ragApi, streamRagChat, type RagRef } from "./rag";

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("streamRagChat（契约 v2）", () => {
  it("引用全字段透传不截断（document_keyword 兜底 document_name）", async () => {
    const long = "切".repeat(80);
    const frames = [
      `data: ${JSON.stringify({ choices: [{ delta: { content: "答" } }] })}`,
      "",
      `data: ${JSON.stringify({
        choices: [{ message: { reference: { chunks: {
          a: { content: long, document_id: "d1", document_keyword: "管道规范.docx", similarity: 0.87 },
        } } } }],
      })}`,
      "",
      "data: [DONE]",
      "",
    ].join("\n");
    const fetchMock = vi.fn(async () => new Response(frames, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const refs: RagRef[][] = [];
    let text = "";
    await streamRagChat(
      [{ role: "user", content: "q" }],
      { onDelta: (t) => { text += t; }, onRefs: (r) => { refs.push(r); } },
    );

    expect(text).toBe("答");
    expect(refs).toHaveLength(1);
    expect(refs[0][0].content).toHaveLength(80); // 不再 slice(0,60)
    expect(refs[0][0].document_name).toBe("管道规范.docx");
    expect(refs[0][0].similarity).toBe(0.87);
    expect(refs[0][0].document_id).toBe("d1");
  });

  it("onStart 注册 cancel：中止后静默返回（不抛错，保留部分内容语义）", async () => {
    const fetchMock = vi.fn(
      (_url: unknown, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onStart = vi.fn();
    const p = streamRagChat(
      [{ role: "user", content: "q" }],
      { onDelta: () => {}, onRefs: () => {}, onStart },
    );
    const cancel = onStart.mock.calls[0]?.[0] as (() => void) | undefined;
    cancel?.();
    await expect(p).resolves.toBeUndefined();
  });

  it("401 → refreshOnce 单飞刷新后重试一次", async () => {
    const fetchMock = vi.fn();
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    fetchMock.mockResolvedValueOnce(new Response("data: [DONE]\n\n", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await streamRagChat([{ role: "user", content: "q" }], { onDelta: () => {}, onRefs: () => {} });

    expect(refreshOnce).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("ragApi.retrieve（契约 v2 参数拼装）", () => {
  it("默认 top_n=10，不透传已弃用的 top_k", async () => {
    const { http } = await import("./http");
    (http.post as Mock).mockResolvedValue({ data: { chunks: [] } });

    await ragApi.retrieve({ question: "q", dataset_ids: ["d1"] });

    expect(http.post).toHaveBeenCalledWith("/rag/retrieval", { question: "q", dataset_ids: ["d1"], top_n: 10 });
    expect((http.post as Mock).mock.calls[0][1]).not.toHaveProperty("top_k");
  });

  it("可选参数仅在设置时携带；keyword=false 不发送", async () => {
    const { http } = await import("./http");
    (http.post as Mock).mockResolvedValue({ data: { chunks: [] } });

    await ragApi.retrieve({
      question: "q", dataset_ids: ["d1"], discipline: "电气",
      similarity_threshold: 0.3, vector_similarity_weight: 0.6,
      keyword: true, highlight: true, top_n: 5,
    });

    expect((http.post as Mock).mock.calls.at(-1)?.[1]).toMatchObject({
      top_n: 5,
      similarity_threshold: 0.3,
      vector_similarity_weight: 0.6,
      keyword: true,
      highlight: true,
      metadata_condition: { conditions: [{ name: "discipline", value: "电气" }] },
    });

    await ragApi.retrieve({ question: "q", dataset_ids: ["d1"], keyword: false, highlight: false });
    expect((http.post as Mock).mock.calls.at(-1)?.[1]).not.toHaveProperty("keyword");
    expect((http.post as Mock).mock.calls.at(-1)?.[1]).not.toHaveProperty("highlight");
  });
});
