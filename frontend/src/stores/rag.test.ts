import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("../api/rag", () => ({
  ragApi: {
    datasets: vi.fn(),
    documents: vi.fn(),
    retrieve: vi.fn(),
    listChunks: vi.fn(),
    upload: vi.fn(),
  },
}));

import { ragApi } from "../api/rag";
import { useRagStore } from "./rag";

beforeEach(() => {
  vi.clearAllMocks();
  useRagStore.setState({
    datasets: [], dsId: "", docs: [], loading: false, searching: false, results: [],
    params: { top_n: 10, keyword: false, highlight: false },
    drawer: { doc: null, dsId: null, chunks: [], total: 0, page: 1, loading: false },
  });
});

describe("rag store（契约 v2）", () => {
  it("loadDatasets 取首个库为默认", async () => {
    (ragApi.datasets as Mock).mockResolvedValue({ data: { data: [{ id: "ds1", name: "库1" }, { id: "ds2", name: "库2" }] } });
    await useRagStore.getState().loadDatasets();
    expect(useRagStore.getState().dsId).toBe("ds1");
    expect(useRagStore.getState().datasets).toHaveLength(2);
  });

  it("setParams 增量合并；search 按参数拼装并落结果", async () => {
    useRagStore.setState({ dsId: "ds1" });
    useRagStore.getState().setParams({ top_n: 5, similarity_threshold: 0.4, keyword: true });
    expect(useRagStore.getState().params).toEqual({ top_n: 5, keyword: true, highlight: false, similarity_threshold: 0.4 });

    (ragApi.retrieve as Mock).mockResolvedValue({ data: { chunks: [{ content: "c", similarity: 0.9 }] } });
    await useRagStore.getState().search("问", "电气");

    expect(ragApi.retrieve).toHaveBeenCalledWith(expect.objectContaining({
      question: "问",
      dataset_ids: ["ds1"],
      discipline: "电气",
      top_n: 5,
      similarity_threshold: 0.4,
      keyword: true,
    }));
    expect(useRagStore.getState().results).toHaveLength(1);
    expect(useRagStore.getState().searching).toBe(false);
  });

  it("openDrawer 载入切片列表；loadChunksPage 翻页；closeDrawer 复位", async () => {
    (ragApi.listChunks as Mock)
      .mockResolvedValueOnce({ data: { chunks: [{ content: "第1页" }], total: 25 } })
      .mockResolvedValueOnce({ data: { chunks: [{ content: "第2页" }], total: 25 } });

    await useRagStore.getState().openDrawer({ id: "doc1", name: "n.docx", run: "DONE" }, "dsX");
    const st = useRagStore.getState().drawer;
    expect(st.doc?.id).toBe("doc1");
    expect(st.dsId).toBe("dsX");
    expect(st.chunks[0].content).toBe("第1页");
    expect(st.total).toBe(25);
    expect(st.loading).toBe(false);

    await useRagStore.getState().loadChunksPage(2);
    expect(ragApi.listChunks).toHaveBeenLastCalledWith("dsX", "doc1", { page: 2 });
    expect(useRagStore.getState().drawer.chunks[0].content).toBe("第2页");

    useRagStore.getState().closeDrawer();
    expect(useRagStore.getState().drawer.doc).toBeNull();
  });
});
