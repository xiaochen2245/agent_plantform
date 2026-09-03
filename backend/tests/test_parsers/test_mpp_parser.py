"""
MPP / MSPDI 施工进度计划解析器测试套件
验证标准 MSPDI XML 任务 WBS 拓扑、工期换算、前置依赖链、关键路径识别与二进制 OLE 降级
"""

import pytest
from app.parsers.mpp_parser import MPPParser
from app.parsers.base import EmptyDocumentError, MalformedDocumentError
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


def make_mspdi_xml(
    tasks=None,
    project_title="某三甲医院智能化工程实施进度计划",
    omit_tasks_tag=False
) -> bytes:
    """构建标准 Microsoft Project XML (MSPDI) 数据流"""
    if omit_tasks_tag:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
    <Title>{project_title}</Title>
</Project>""".encode("utf-8")

    task_list = tasks or [
        {
            "uid": "1",
            "name": "工程总体实施启动",
            "level": "1",
            "number": "1",
            "duration": "PT80H0M0S",  # 10工作日
            "start": "2026-04-01T08:00:00",
            "finish": "2026-04-10T17:00:00",
            "critical": "1",
            "preds": []
        },
        {
            "uid": "2",
            "name": "地下室及裙楼管线预埋",
            "level": "2",
            "number": "1.1",
            "duration": "PT160H0M0S",  # 20工作日
            "start": "2026-04-11T08:00:00",
            "finish": "2026-04-30T17:00:00",
            "critical": "1",
            "preds": ["1"]
        },
        {
            "uid": "3",
            "name": "智能化系统核心机房建设",
            "level": "2",
            "number": "1.2",
            "duration": "PT240H0M0S",  # 30工作日
            "start": "2026-05-01T08:00:00",
            "finish": "2026-05-30T17:00:00",
            "critical": "0",
            "preds": ["2"]
        },
        {
            "uid": "4",
            "name": "系统联调与竣工验收交付",
            "level": "1",
            "number": "2",
            "duration": "PT720H0M0S",  # 90工作日
            "start": "2026-06-01T08:00:00",
            "finish": "2026-08-30T17:00:00",
            "critical": "1",
            "preds": ["2", "3"]
        }
    ]

    task_xmls = []
    for t in task_list:
        preds_xml = ""
        for p in t.get("preds", []):
            preds_xml += f"<PredecessorLink><PredecessorUID>{p}</PredecessorUID></PredecessorLink>"

        task_xmls.append(f"""
        <Task>
            <UID>{t["uid"]}</UID>
            <Name>{t["name"]}</Name>
            <OutlineLevel>{t["level"]}</OutlineLevel>
            <OutlineNumber>{t["number"]}</OutlineNumber>
            <Duration>{t["duration"]}</Duration>
            <Start>{t["start"]}</Start>
            <Finish>{t["finish"]}</Finish>
            <Critical>{t["critical"]}</Critical>
            {preds_xml}
        </Task>
        """)

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
    <Title>{project_title}</Title>
    <StartDate>2026-04-01T08:00:00</StartDate>
    <FinishDate>2026-08-30T17:00:00</FinishDate>
    <Tasks>
        {"".join(task_xmls)}
    </Tasks>
</Project>"""
    return xml_content.encode("utf-8")


@pytest.fixture
def mpp_parser() -> MPPParser:
    return MPPParser()


def test_mpp_parser_metadata(mpp_parser: MPPParser):
    """1. 验证 MPP 解析器元数据注册"""
    assert ".mpp" in mpp_parser.supported_extensions
    assert ".xml" in mpp_parser.supported_extensions
    assert mpp_parser.source_type == DocumentSourceType.MPP


@pytest.mark.asyncio
async def test_mpp_xml_task_extraction(mpp_parser: MPPParser):
    """2. 验证标准 MSPDI XML 任务提取为 ASTBlockType.SCHEDULE_TASK 与 ScheduleTaskData"""
    data = make_mspdi_xml()
    ast = await mpp_parser.parse(data, "schedule.xml")

    assert ast.source_type == DocumentSourceType.MPP
    assert ast.file_name == "schedule.xml"
    # 第 1 个是总览表格，后 4 个是各分解任务
    assert len(ast.nodes) == 5

    task_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.SCHEDULE_TASK]
    assert len(task_nodes) == 4

    t1 = task_nodes[0]
    assert t1.schedule_data is not None
    assert t1.schedule_data.task_name == "工程总体实施启动"
    assert t1.schedule_data.task_id == "1"


@pytest.mark.asyncio
async def test_mpp_critical_path_identification(mpp_parser: MPPParser):
    """3. 验证关键路径 (Critical Path) 准确识别"""
    data = make_mspdi_xml()
    ast = await mpp_parser.parse(data, "schedule.xml")

    task_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.SCHEDULE_TASK]
    critical_nodes = [n for n in task_nodes if n.schedule_data.is_critical_path]
    non_critical_nodes = [n for n in task_nodes if not n.schedule_data.is_critical_path]

    assert len(critical_nodes) == 3
    assert len(non_critical_nodes) == 1
    assert non_critical_nodes[0].schedule_data.task_name == "智能化系统核心机房建设"


@pytest.mark.asyncio
async def test_mpp_duration_iso_conversion(mpp_parser: MPPParser):
    """4. 验证 ISO 工期字符串换算为标准天数 (PT80H -> 10天, PT720H -> 90天)"""
    data = make_mspdi_xml()
    ast = await mpp_parser.parse(data, "schedule.xml")

    task_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.SCHEDULE_TASK]
    # PT80H / 8 = 10天
    assert task_nodes[0].schedule_data.duration_days == 10.0
    # PT160H / 8 = 20天
    assert task_nodes[1].schedule_data.duration_days == 20.0
    # PT720H / 8 = 90天
    assert task_nodes[3].schedule_data.duration_days == 90.0


@pytest.mark.asyncio
async def test_mpp_wbs_hierarchy_breadcrumb(mpp_parser: MPPParser):
    """5. 验证 WBS 多级大纲层级与路径面包屑"""
    data = make_mspdi_xml()
    ast = await mpp_parser.parse(data, "schedule.xml")

    task_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.SCHEDULE_TASK]
    t_child = task_nodes[1]  # WBS 1.1 地下室及裙楼管线预埋
    assert t_child.level == 2
    assert any("1 工程总体实施启动" in p or "工程总体实施启动" in p for p in t_child.section_path)


@pytest.mark.asyncio
async def test_mpp_predecessor_links(mpp_parser: MPPParser):
    """6. 验证前置依赖关系 (Predecessors) 提取"""
    data = make_mspdi_xml()
    ast = await mpp_parser.parse(data, "schedule.xml")

    task_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.SCHEDULE_TASK]
    t4 = task_nodes[3]  # UID 4 依赖 2 和 3
    assert t4.schedule_data.predecessors == ["2", "3"]


@pytest.mark.asyncio
async def test_mpp_dependency_cycle_handling(mpp_parser: MPPParser):
    """7. 验证存在循环依赖时解析不发生无限递归"""
    cyclic_tasks = [
        {"uid": "1", "name": "任务A", "level": "1", "number": "1", "duration": "PT8H", "start": "", "finish": "", "critical": "0", "preds": ["2"]},
        {"uid": "2", "name": "任务B", "level": "1", "number": "2", "duration": "PT8H", "start": "", "finish": "", "critical": "0", "preds": ["1"]},
    ]
    data = make_mspdi_xml(tasks=cyclic_tasks)
    ast = await mpp_parser.parse(data, "cyclic.xml")

    task_nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.SCHEDULE_TASK]
    assert len(task_nodes) == 2


@pytest.mark.asyncio
async def test_mpp_missing_tasks_error(mpp_parser: MPPParser):
    """8. 验证缺失 <Tasks> 标签报错 MalformedDocumentError"""
    data = make_mspdi_xml(omit_tasks_tag=True)
    with pytest.raises(MalformedDocumentError):
        await mpp_parser.parse(data, "missing_tasks.xml")


@pytest.mark.asyncio
async def test_mpp_binary_ole_fallback(mpp_parser: MPPParser):
    """9. 验证二进制 OLE 文件头平滑降级"""
    binary_ole_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200 + "关键进度说明：主楼封顶".encode("utf-8")
    ast = await mpp_parser.parse(binary_ole_header, "project.mpp")

    assert ast.source_type == DocumentSourceType.MPP
    assert ast.metadata.get("is_binary_ole_fallback") is True
    assert any(n.block_type == ASTBlockType.CALLOUT for n in ast.nodes)


@pytest.mark.asyncio
async def test_mpp_reduction_accuracy_threshold(mpp_parser: MPPParser):
    """10. 验证进度计划关键信息还原率 >= 98%"""
    ground_truth = [
        "工程总体实施启动",
        "地下室及裙楼管线预埋",
        "智能化系统核心机房建设",
        "系统联调与竣工验收交付"
    ]
    data = make_mspdi_xml()
    ast = await mpp_parser.parse(data, "accuracy.xml")

    extracted_text = " ".join(n.text_content for n in ast.nodes)
    matched = sum(1 for name in ground_truth if name in extracted_text)
    rate = matched / len(ground_truth)
    assert rate >= 0.98, f"MPP 关键任务还原率 {rate:.4f} 未达到 98%"
