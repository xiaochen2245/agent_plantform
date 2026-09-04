"""
千页大文档极限压力测试与内存安全套件 (test_large_document_stress.py)
验证:
1. 1000 页 PDF 解析峰值内存增量 < 50MB，AST 结构还原率 >= 98.0%
2. 500 章节 (等效千页) DOCX 解析峰值内存增量 < 50MB，AST 结构还原率 >= 98.0%
3. 1000 行 XLSX 工程量清单表格解析耗时 < 1.5s，数据行完整提取
4. 10 协程并发千页文档解析协程安全与吞吐性能
"""

import asyncio
import time
import tracemalloc
import pytest

from app.parsers.docx_parser import DOCXParser
from app.parsers.pdf_parser import PDFParser
from app.parsers.xlsx_parser import XLSXParser
from app.rag.chunker import ParentChildChunker
from app.schemas.ast import ASTBlockType
from tests.test_stress.conftest_stress import (
    generate_ground_truth_spec,
    generate_synthetic_1000p_docx,
    generate_synthetic_1000p_pdf,
    generate_synthetic_1000r_xlsx,
)


class TestLargeDocumentParsingStress:
    """千页大文档解析与切片性能压力测试"""

    @pytest.mark.asyncio
    async def test_1000_page_pdf_parsing_memory_and_reduction_rate(self):
        """验证 1000 页超长 PDF 解析峰值内存增量 < 50MB 且 AST 还原率 >= 98.0%"""
        pdf_bytes = generate_synthetic_1000p_pdf(num_pages=1000)

        tracemalloc.start()
        start_mem = tracemalloc.get_traced_memory()[0]
        start_time = time.perf_counter()

        parser = PDFParser()
        ast = await parser.parse(pdf_bytes, file_name="stress_1000p.pdf", tenant_id="tenant_stress")

        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        elapsed = time.perf_counter() - start_time

        peak_delta_mb = (peak_mem - start_mem) / (1024 * 1024)

        # 1. 验证解析节点数量与耗时
        assert len(ast.nodes) >= 2000, f"1000页PDF节点数期望>=2000，实际 {len(ast.nodes)}"
        assert elapsed < 5.0, f"1000页PDF解析耗时期望<5.0s，实际 {elapsed:.2f}s"

        # 2. 验证峰值内存增量严格受控 (< 50MB)
        assert peak_delta_mb < 50.0, f"峰值内存增量超标: {peak_delta_mb:.2f}MB (门禁 < 50MB)"

        # 3. 验证抽样地标还原率 (前 100 章大纲标题抽取率)
        sample_expected_titles = [f"第{i}章 智能工程施工规范与质量标准第{i}分册" for i in range(1, 101)]
        extracted_texts = {n.text_content for n in ast.nodes}
        matched_count = sum(1 for title in sample_expected_titles if title in extracted_texts)
        reduction_rate = (matched_count / len(sample_expected_titles)) * 100.0
        assert reduction_rate >= 98.0, f"PDF AST 地标还原率未达标: {reduction_rate:.2f}% (门禁 >= 98.0%)"

        # 4. 验证父子分块能够正常切分超大文档
        chunker = ParentChildChunker()
        parent_chunks, child_chunks = chunker.chunk_document(ast)
        assert len(parent_chunks) > 0, "父块切分结果不应为空"
        assert len(child_chunks) > 0, "子块切分结果不应为空"
        assert len(child_chunks) >= len(parent_chunks), "子切片数量应大于等于父切片"

    @pytest.mark.asyncio
    async def test_1000_page_docx_parsing_memory_and_reduction_rate(self):
        """验证 500 章节 (等效千页) DOCX 解析峰值内存增量 < 50MB 且 AST 还原率 >= 98.0%"""
        docx_bytes = generate_synthetic_1000p_docx(num_chapters=500)

        tracemalloc.start()
        start_mem = tracemalloc.get_traced_memory()[0]
        start_time = time.perf_counter()

        parser = DOCXParser()
        ast = await parser.parse(docx_bytes, file_name="stress_500ch.docx", tenant_id="tenant_stress")

        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        elapsed = time.perf_counter() - start_time

        peak_delta_mb = (peak_mem - start_mem) / (1024 * 1024)

        # 1. 验证解析节点数量与耗时
        assert len(ast.nodes) >= 1000, f"500章节DOCX节点数期望>=1000，实际 {len(ast.nodes)}"
        assert elapsed < 5.0, f"DOCX解析耗时期望<5.0s，实际 {elapsed:.2f}s"

        # 2. 验证峰值内存增量严格受控 (< 50MB)
        assert peak_delta_mb < 50.0, f"DOCX 峰值内存增量超标: {peak_delta_mb:.2f}MB (门禁 < 50MB)"

        # 3. 验证抽样地标还原率 (前 100 章标题抽取率)
        sample_expected_titles = [f"第{i}章 智能化基础设施工程实施纲要" for i in range(1, 101)]
        extracted_texts = {n.text_content for n in ast.nodes}
        matched_count = sum(1 for title in sample_expected_titles if title in extracted_texts)
        reduction_rate = (matched_count / len(sample_expected_titles)) * 100.0
        assert reduction_rate >= 98.0, f"DOCX AST 地标还原率未达标: {reduction_rate:.2f}% (门禁 >= 98.0%)"

        # 4. 验证表格结构成功提取
        table_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.TABLE]
        assert len(table_nodes) == 10, f"期望抽取 10 个内嵌表格，实际抽取 {len(table_nodes)}"

    @pytest.mark.asyncio
    async def test_1000_row_xlsx_boq_parsing_stress(self):
        """验证 1000 行工程量清单 XLSX 解析耗时 < 1.5s 且全量提取数据行"""
        xlsx_bytes = generate_synthetic_1000r_xlsx(num_rows=1000)

        start_time = time.perf_counter()
        parser = XLSXParser()
        ast = await parser.parse(xlsx_bytes, file_name="stress_1000r.xlsx", tenant_id="tenant_stress")
        elapsed = time.perf_counter() - start_time

        # 1. 验证耗时门禁 (< 1.5s)
        assert elapsed < 1.5, f"1000行XLSX解析超时: {elapsed:.2f}s (门禁 < 1.5s)"

        # 2. 验证表格节点存在且行数完整
        table_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.TABLE]
        assert len(table_nodes) >= 1, "必须提取出工程量清单表格节点"
        tbl = table_nodes[0]
        assert tbl.table_data is not None
        # 1000 行数据行 + 1 行表头
        assert len(tbl.table_data.rows) >= 1000, f"期望至少 1000 行数据，实际 {len(tbl.table_data.rows)}"
        assert len(tbl.table_data.headers[0]) == 7, "表头列数应为 7 列"

    @pytest.mark.asyncio
    async def test_concurrent_large_doc_parsing_coroutine_safety(self):
        """验证 10 协程并发千页/大体量文档解析的安全无死锁与吞吐性能"""
        pdf_bytes = generate_synthetic_1000p_pdf(num_pages=500)
        docx_bytes = generate_synthetic_1000p_docx(num_chapters=250)

        pdf_parser = PDFParser()
        docx_parser = DOCXParser()

        async def parse_pdf_task(idx: int):
            return await pdf_parser.parse(
                pdf_bytes,
                file_name=f"concurrent_{idx}.pdf",
                tenant_id=f"tenant_{idx % 3}"
            )

        async def parse_docx_task(idx: int):
            return await docx_parser.parse(
                docx_bytes,
                file_name=f"concurrent_{idx}.docx",
                tenant_id=f"tenant_{idx % 3}"
            )

        tasks = []
        for i in range(5):
            tasks.append(parse_pdf_task(i))
            tasks.append(parse_docx_task(i))

        start_time = time.perf_counter()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total_time = time.perf_counter() - start_time

        # 1. 验证全部 10 个并发任务无异常崩溃
        for idx, res in enumerate(results):
            assert not isinstance(res, Exception), f"协程任务 {idx} 异常崩溃: {res}"
            assert len(res.nodes) >= 500, f"任务 {idx} 提取节点数不足"

        # 2. 验证并发调度耗时合理 (< 10.0s)
        assert total_time < 10.0, f"10并发解析总耗时超标: {total_time:.2f}s (门禁 < 10.0s)"
