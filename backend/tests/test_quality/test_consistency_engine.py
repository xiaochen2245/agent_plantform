"""
跨章节数值一致性校验引擎全量单元测试与 100% 矛盾注入验收套件
覆盖 Features 17, 18, 19:
1. 工期单位折算与跨章节冲突 (日历天 vs 月 vs 年 vs 周)
2. 造价与投资金额冲突 (亿元 vs 万元 vs 元，千分位逗号解析)
3. 建筑面积量纲冲突 (m² vs 万m² vs ㎡)
4. 暖通与电气设备参数冲突 (COP, 额定制冷量 RT vs kW, 额定风量, 额定功率, 扬程)
5. AST TableData 结构化表格与 ScheduleTaskData 进度任务穿透
6. 证据链完备性与 ReviewResult 数据库模型映射验证
"""

import pytest
from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel
from app.quality.consistency_engine import (
    ConflictType,
    ConsistencyConflict,
    ConsistencyEngine,
    IssueSeverity,
    MetricDimension,
    MetricNormalizer,
)
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    ScheduleTaskData,
    TableData,
    UnifiedDocumentAST,
)


# ===========================================================================
# 1. 量纲自适应归一化器单元测试 (Feature 18)
# ===========================================================================

def test_normalizer_duration():
    """验证工期向标准'天'的自适应转换"""
    assert MetricNormalizer.normalize_duration(450, "日历天") == (450.0, "天")
    assert MetricNormalizer.normalize_duration(18, "个月") == (540.0, "天")
    assert MetricNormalizer.normalize_duration(1, "年") == (365.0, "天")
    assert MetricNormalizer.normalize_duration(24, "周") == (168.0, "天")
    assert MetricNormalizer.normalize_duration(10, "工作日") == (14.0, "天")


def test_normalizer_currency():
    """验证金额向标准'万元'的自适应转换"""
    assert MetricNormalizer.normalize_currency(12345.67, "万元") == (12345.67, "万元")
    assert MetricNormalizer.normalize_currency(1.25, "亿元") == (12500.0, "万元")
    assert MetricNormalizer.normalize_currency(9800000, "元") == (980.0, "万元")
    assert MetricNormalizer.normalize_currency(500, "千元") == (50.0, "万元")


def test_normalizer_area():
    """验证面积向标准'm²'的转换"""
    assert MetricNormalizer.normalize_area(45000, "m²") == (45000.0, "m²")
    assert MetricNormalizer.normalize_area(4.5, "万平方米") == (45000.0, "m²")
    assert MetricNormalizer.normalize_area(32000, "㎡") == (32000.0, "m²")


def test_normalizer_equipment_parameters():
    """验证机电设备核心参数换算"""
    # COP 无量纲
    assert MetricNormalizer.normalize_cop(5.4) == (5.4, "无量纲")
    # 制冷量 RT -> kW
    assert MetricNormalizer.normalize_cooling_capacity(1000, "RT") == (3516.85, "kW")
    assert MetricNormalizer.normalize_cooling_capacity(3500, "kW") == (3500.0, "kW")
    # 风量
    assert MetricNormalizer.normalize_air_flow(50000, "m³/h") == (50000.0, "m³/h")
    assert MetricNormalizer.normalize_air_flow(45000, "立方米/小时") == (45000.0, "m³/h")
    # 功率
    assert MetricNormalizer.normalize_power(250, "kW") == (250.0, "kW")
    assert MetricNormalizer.normalize_power(100, "HP") == (73.55, "kW")
    # 扬程
    assert MetricNormalizer.normalize_head(32, "m") == (32.0, "m")
    assert MetricNormalizer.normalize_head(45, "米") == (45.0, "m")


# ===========================================================================
# 2. 100% 注入矛盾全维度自动化检测测试 (Feature 17 & 19)
# ===========================================================================

def test_100_percent_injected_conflicts_detection():
    """
    全量注入矛盾综合验收测试:
    注入 8 组前后相悖的工程指标，验证检出率达 100%
    1. 工期矛盾: 450日历天 vs 18个月 (540天) vs 360天
    2. 造价矛盾: 1.25亿元 vs 12,345.67万元 vs 8500万元
    3. 建筑面积矛盾: 45,000 m² vs 4.2万平方米 (42,000 m²)
    4. COP矛盾: 5.4 vs 4.85
    5. 制冷量矛盾: 3500 kW vs 800 RT (2813.48 kW)
    6. 额定风量矛盾: 50,000 m³/h vs 45,000 立方米/小时
    7. 额定功率矛盾: 250 kW vs 315 kW
    8. 扬程矛盾: 32 m vs 45 米
    """
    engine = ConsistencyEngine()

    injected_sections = [
        {"section_title": "第1章 投标总函与工程概况", "page": "第5页", "content": "本工程总工期450日历天。合同总价 1.25 亿元。"},
        {"section_title": "第2章 总体建设规划", "page": "第10页", "content": "项目总建筑面积 45,000 m²。"},
        {"section_title": "第5章 暖通空调系统设计技术说明", "page": "第22页", "content": "选用高效离心冷水机组，性能系数(COP)不小于5.4，单台额定制冷量 3500 kW。"},
        {"section_title": "第6章 通风与防排烟工程设计", "page": "第28页", "content": "组合式空调机组额定送风量 50000 m³/h，电机额定功率 250 kW。"},
        {"section_title": "第7章 给排水及水暖动力工程", "page": "第33页", "content": "冷冻水循环泵额定扬程 32 m，确保系统水力平衡。"},
        {"section_title": "第10章 工程投资预算与资金计划", "page": "第38页", "content": "本项目投资估算表中，工程总投资 12,345.67万元。"},
        {"section_title": "第14章 建筑面积核算明细表", "page": "第50页", "content": "经二次复核，本工程地上地下总建筑面积 4.2 万平方米。"},
        {"section_title": "第15章 施工进度计划与工期保障", "page": "第45页", "content": "依据关键节点网络分析，施工总工期 18 个月。"},
        {"section_title": "第18章 核心机电设备采购规格明细", "page": "第60页", "content": "冷水机组额定 COP 为 4.85，单台制冷量 800 RT。"},
        {"section_title": "第19章 风系统主要设备清单", "page": "第65页", "content": "组合式空调机组送风量 45,000 立方米/小时，电机额定功率 315 kW。"},
        {"section_title": "第20章 水泵机组选型明细表", "page": "第72页", "content": "冷水循环水泵扬程 45 米。"},
        {"section_title": "第28章 商务标投标报价说明", "page": "第95页", "content": "最终核定工程总造价为 8500 万元。"},
        {"section_title": "第35章 关键施工节点网络横道图", "page": "第120页", "content": "项目总工期为 360 天。"},
    ]

    report = engine.validate_document_consistency(
        document_title="新建数据中心园区机电安装工程投标文件.docx",
        sections_data=injected_sections
    )

    # 1. 验证扫描到的指标总数与冲突数
    assert report.total_metrics_scanned >= 16
    assert report.conflicts_found >= 8, f"期望至少检出 8 组冲突，实际检出 {report.conflicts_found}"

    detected_categories = {c.metric_category for c in report.conflicts}
    # 验证 8 大维度 100% 检出
    assert "工期" in detected_categories
    assert "造价" in detected_categories
    assert "建筑面积" in detected_categories
    assert "COP" in detected_categories
    assert "制冷量" in detected_categories
    assert "风量" in detected_categories
    assert "功率" in detected_categories
    assert "扬程" in detected_categories

    # 2. 详细核验工期矛盾证据链
    duration_conflicts = [c for c in report.conflicts if c.metric_category == "工期"]
    assert len(duration_conflicts) >= 2
    for dc in duration_conflicts:
        assert dc.severity == IssueSeverity.CRITICAL
        assert dc.unit_a == "天"
        assert dc.unit_b == "天"
        assert dc.diff_value > 0
        assert dc.diff_percent > 0
        assert "第1章" in dc.section_a
        assert "第5页" in dc.page_a
        assert dc.quote_a != ""
        assert dc.quote_b != ""
        assert "废标" in dc.detailed_reason

    # 3. 详细核验造价矛盾证据链
    cost_conflicts = [c for c in report.conflicts if c.metric_category == "造价"]
    assert len(cost_conflicts) >= 2
    for cc in cost_conflicts:
        assert cc.severity == IssueSeverity.CRITICAL
        assert cc.unit_a == "万元"
        assert cc.unit_b == "万元"
        assert cc.diff_value > 0

    # 4. 验证偏差百分比公式: abs(val_a - val_b) / max(val_a, val_b) * 100%
    for c in report.conflicts:
        v_a = c.value_a
        v_b = c.value_b
        expected_diff = round(abs(v_a - v_b), 4)
        max_v = max(abs(v_a), abs(v_b))
        expected_pct = round((expected_diff / max_v * 100.0), 2)
        assert abs(c.diff_value - expected_diff) < 1e-3
        assert abs(c.diff_percent - expected_pct) < 1e-2


# ===========================================================================
# 3. AST TableData 表格与 ScheduleTaskData 进度任务穿透测试
# ===========================================================================

def test_ast_table_data_and_schedule_task_extraction():
    """验证从 AST 表格二维单元格与 MPP 进度计划节点中成功抽取指标并检出矛盾"""
    table_rows = [
        ["序号", "设备名称", "设计规格参数", "数量"],
        ["1", "离心机组", "制冷量 3500 kW，能效比 COP 5.4", "2台"],
        ["2", "空调箱", "额定风量 50000 m³/h，电机功率 250 kW", "4台"],
    ]

    ast = UnifiedDocumentAST(
        document_id="ast_doc_001",
        tenant_id="tenant_alpha",
        file_name="complex_design.mpp",
        source_type=DocumentSourceType.MPP,
        nodes=[
            ASTNode(
                block_id="node_sec1",
                block_type=ASTBlockType.HEADING,
                level=1,
                section_path=["第1章 投标总说明"],
                text_content="本工程施工总工期 360 天，总投资 9000 万元。",
                page_or_sheet="1",
            ),
            ASTNode(
                block_id="node_tbl1",
                block_type=ASTBlockType.TABLE,
                section_path=["第5章 主要设备材料表"],
                text_content="设备清单",
                page_or_sheet="15",
                table_data=TableData(headers=[table_rows[0]], rows=table_rows[1:]),
            ),
            ASTNode(
                block_id="node_task1",
                block_type=ASTBlockType.SCHEDULE_TASK,
                section_path=["第12章 施工网络进度图"],
                text_content="施工总工期进度任务",
                page_or_sheet="Sheet1",
                schedule_data=ScheduleTaskData(
                    task_id="1",
                    task_name="工程总承包全周期总工期",
                    duration_days=420.0,
                    start_date="2026-10-01",
                    finish_date="2027-11-25",
                ),
            ),
            ASTNode(
                block_id="node_sec2",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第18章 后期运维保修"],
                text_content="机房冷水机组单台制冷量 2800 kW，额定 COP 4.9。",
                page_or_sheet="35",
            ),
        ],
    )

    engine = ConsistencyEngine()
    report = engine.validate_ast_consistency(ast)

    assert report.total_metrics_scanned >= 6

    # 1. 验证进度计划任务 420天 与 投标总说明 360天 的工期冲突检出
    duration_conflicts = [c for c in report.conflicts if c.metric_category == "工期"]
    assert len(duration_conflicts) >= 1
    dc = duration_conflicts[0]
    assert dc.value_a == 360.0
    assert dc.value_b == 420.0
    assert dc.diff_value == 60.0

    # 2. 验证表格内的制冷量 3500 kW 与段落 2800 kW 冲突检出
    cooling_conflicts = [c for c in report.conflicts if c.metric_category == "制冷量"]
    assert len(cooling_conflicts) >= 1
    cc = cooling_conflicts[0]
    assert cc.value_a == 3500.0
    assert cc.value_b == 2800.0


# ===========================================================================
# 4. ReviewResult 持久化模型映射测试
# ===========================================================================

def test_consistency_engine_export_to_review_results():
    """验证 ConsistencyReport 能正确转换为 SQLAlchemy ReviewResult 实体"""
    engine = ConsistencyEngine()
    sections = [
        {"section_title": "第1章 投标函", "page": "P.2", "content": "项目总工期 300 天。"},
        {"section_title": "第8章 施工组织", "page": "P.40", "content": "项目总工期 365 天。"},
    ]
    report = engine.validate_document_consistency("工期冲突标书.docx", sections)
    assert report.conflicts_found == 1

    # 测试字典导出
    dict_records = engine.export_to_review_results(report, task_id="task_001", tenant_id="tenant_xyz")
    assert len(dict_records) == 1
    assert dict_records[0]["tenant_id"] == "tenant_xyz"
    assert dict_records[0]["task_id"] == "task_001"
    assert dict_records[0]["severity"] == "critical"
    assert dict_records[0]["source_page"] == 40
    assert "300" in dict_records[0]["benchmark_quote"]
    assert "365" in dict_records[0]["source_quote"]

    # 测试 ORM 实体转换
    entities = engine.to_review_results(report, task_id="task_001", tenant_id="tenant_xyz")
    assert len(entities) == 1
    entity = entities[0]
    assert isinstance(entity, ReviewResult)
    assert entity.tenant_id == "tenant_xyz"
    assert entity.deviation_type == DeviationType.NEGATIVE
    assert entity.severity == SeverityLevel.CRITICAL
    assert entity.confidence == 1.0
    assert entity.source_page == 40
