"""功能① 规则引擎（#30）：docx vs 模板基准，确定性规则 + API。"""
import io
import zipfile

import pytest
from httpx import AsyncClient

from app.review.ooxml import Docx, DocxFormatError
from app.review.rules import check
from tests.conftest import login

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def build_docx(
    paragraphs: list[str],
    styles: str = "",
    numbering_nums: list[str] | None = None,
) -> bytes:
    """最小合法 docx：document.xml（段落 XML 原样嵌入）+ 可选 styles/numbering。"""
    body = "".join(paragraphs)
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "word/document.xml": f'<?xml version="1.0"?><w:document {NS}><w:body>{body}</w:body></w:document>',
    }
    if styles:
        parts["word/styles.xml"] = f'<?xml version="1.0"?><w:styles {NS}>{styles}</w:styles>'
    if numbering_nums:
        nums = "".join(f'<w:num w:numId="{n}"><w:abstractNumId w:val="0"/></w:num>' for n in numbering_nums)
        parts["word/numbering.xml"] = f'<?xml version="1.0"?><w:numbering {NS}>{nums}</w:numbering>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, xml in parts.items():
            z.writestr(name, xml)
    return buf.getvalue()


TPL_STYLES = (
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="24"/>'
    "</w:rPr></w:rPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/>'
    '<w:pPr><w:jc w:val="left"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="32"/></w:rPr></w:style>'
)


def _p(inner: str, style: str | None = None, num_id: str | None = None, jc: str | None = None) -> str:
    ppr = ""
    bits = ""
    if style:
        bits += f'<w:pStyle w:val="{style}"/>'
    if num_id:
        bits += f'<w:numPr><w:numId w:val="{num_id}"/></w:numPr>'
    if jc:
        bits += f'<w:jc w:val="{jc}"/>'
    if bits:
        ppr = f"<w:pPr>{bits}</w:pPr>"
    return f"<w:p>{ppr}{inner}</w:p>"


def _r(text: str, east: str | None = None, ascii_: str | None = None, sz: int | None = None) -> str:
    rpr = ""
    if east or ascii_:
        attrs = ""
        if east:
            attrs += f' w:eastAsia="{east}"'
        if ascii_:
            attrs += f' w:ascii="{ascii_}"'
        rpr += f"<w:rFonts{attrs}/>"
    if sz:
        rpr += f'<w:sz w:val="{sz}"/>'
    rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
    return f"<w:r>{rpr}<w:t>{text}</w:t></w:r>"


@pytest.fixture
def tpl() -> Docx:
    return Docx(build_docx([_p(_r("模板"))], styles=TPL_STYLES))


def test_clean_doc_no_issues(tpl):
    doc = Docx(build_docx([
        _p(_r("正文段落")),                          # 默认样式，无显式覆盖 → 无问题
        _p(_r("标题"), style="Heading1"),            # 模板样式，无覆盖 → 无问题
    ], styles=TPL_STYLES, numbering_nums=["1"]))
    report = check(doc, tpl)
    assert report["summary"]["total_issues"] == 0


def test_font_size_alignment_violations_reported_with_location(tpl):
    doc = Docx(build_docx([
        _p(_r("第一段合规")),
        _p(_r("微软雅黑段落", east="微软雅黑")),                       # 中文字体偏离
        _p(_r("大字号", sz=48), style="Heading1"),                    # 字号偏离（基准 32）
        _p(_r("右对齐"), style="Normal", jc="right"),      # 对齐偏离
    ], styles=TPL_STYLES))
    report = check(doc, tpl)
    by_type = report["summary"]["by_type"]
    assert by_type == {"font": 1, "size": 1, "alignment": 1}
    font_issue = next(i for i in report["issues"] if i["type"] == "font")
    assert font_issue["paragraph"] == 1 and font_issue["expected"] == "宋体"
    assert font_issue["actual"] == "微软雅黑" and "微软雅黑段落".startswith(font_issue["text"][:2])
    size_issue = next(i for i in report["issues"] if i["type"] == "size")
    assert size_issue["expected"] == "16pt" and size_issue["actual"] == "24pt"


def test_numbering_broken_ref_and_double_numbering(tpl):
    doc = Docx(build_docx([
        _p(_r("断裂编号"), num_id="99"),                    # numId 不在 numbering.xml
        _p(_r("1. 双重编号"), num_id="1"),                  # 自动编号 + 手写序号
        _p(_r("正常编号项"), num_id="1"),
    ], styles=TPL_STYLES, numbering_nums=["1"]))
    report = check(doc, tpl)
    errs = [i for i in report["issues"] if i["type"] == "numbering"]
    assert len(errs) == 2
    assert errs[0]["severity"] == "error" and errs[0]["paragraph"] == 0
    assert errs[1]["severity"] == "warn" and "双重" in errs[1]["message"]


def test_template_missing_style_skips_style_checks(tpl):
    # 文档用了模板没有的样式：样式项跳过，但编号断裂仍查
    doc = Docx(build_docx([_p(_r("未知样式"), style="Custom", num_id="7")], styles=TPL_STYLES))
    report = check(doc, tpl)
    assert [i["type"] for i in report["issues"]] == ["numbering"]


def test_docx_format_error_on_garbage():
    with pytest.raises(DocxFormatError):
        Docx(b"not a zip at all")


async def test_review_api(client: AsyncClient):
    await login(client)
    doc = build_docx([_p(_r("偏字体", east="楷体"))], styles=TPL_STYLES)
    tpl_bytes = build_docx([_p(_r("模板"))], styles=TPL_STYLES)
    r = await client.post(
        "/api/review/docx",
        files=[("file", ("审查.docx", doc, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
               ("template", ("模板.docx", tpl_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
    )
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["by_type"] == {"font": 1}

    r = await client.post("/api/review/docx", files=[
        ("file", ("坏.txt", b"junk", "text/plain")),
        ("template", ("模板.docx", tpl_bytes, "application/octet-stream")),
    ])
    assert r.status_code == 422

    r = await client.post(
        "/api/review/docx",
        files=[("file", ("a.docx", doc, "application/octet-stream")),
               ("template", ("t.docx", b"", "application/octet-stream"))],
    )
    assert r.status_code == 422
