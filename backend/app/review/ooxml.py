"""OOXML 底座（#30）：docx = zip + XML，stdlib 解析，零第三方依赖。

只实现规则引擎需要的读取面：
- document.xml：段落（样式/编号/对齐/显式字体字号覆盖）与 run 文本
- styles.xml：段落样式基准（字体/字号/对齐 + basedOn 一层继承）+ docDefaults
- numbering.xml：合法 numId 集合
"""
import io
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocxFormatError(Exception):
    """非 docx/结构损坏（HTTP 层映射 400/422）。"""


@dataclass
class Run:
    text: str
    fonts: dict[str, str] = field(default_factory=dict)   # ascii/eastAsia → 值
    sz: int | None = None                                  # 半磅（w:sz val）


@dataclass
class Paragraph:
    idx: int                # document.xml 内序号（0 起，报告定位用）
    style: str | None       # pStyle id
    num_id: str | None      # 自动编号引用
    jc: str | None          # 显式对齐覆盖
    runs: list[Run] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


def _val(el: ET.Element | None) -> str | None:
    return el.get(W + "val") if el is not None else None


def _parse_run(r: ET.Element) -> Run:
    rpr = r.find(W + "rPr")
    fonts: dict[str, str] = {}
    sz: int | None = None
    if rpr is not None:
        rf = rpr.find(W + "rFonts")
        if rf is not None:
            for k in ("ascii", "eastAsia"):
                v = rf.get(W + k)
                if v:
                    fonts[k] = v
        sz_el = rpr.find(W + "sz")
        if sz_el is not None and (_v := _val(sz_el)):
            sz = int(_v)
    return Run(text="".join(t.text or "" for t in r.findall(W + "t")), fonts=fonts, sz=sz)


def _parse_paragraph(p: ET.Element, idx: int) -> Paragraph:
    para = Paragraph(idx=idx, style=None, num_id=None, jc=None)
    ppr = p.find(W + "pPr")
    if ppr is not None:
        para.style = _val(ppr.find(W + "pStyle"))
        para.num_id = _val(ppr.find(W + "numPr").find(W + "numId")) if ppr.find(W + "numPr") is not None else None
        para.jc = _val(ppr.find(W + "jc"))
    para.runs = [_parse_run(r) for r in p.findall(W + "r")]
    return para


class Docx:
    """懒解析：规则用到哪块读哪块。"""

    def __init__(self, data: bytes) -> None:
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise DocxFormatError("not a docx (zip) file") from e
        names = set(self._zip.namelist())
        if "word/document.xml" not in names:
            raise DocxFormatError("docx missing word/document.xml")

    def _xml(self, name: str) -> ET.Element | None:
        if name not in set(self._zip.namelist()):
            return None
        try:
            return ET.fromstring(self._zip.read(name))
        except ET.ParseError as e:
            raise DocxFormatError(f"malformed {name}") from e

    # ---- document.xml ----

    def paragraphs(self) -> list[Paragraph]:
        root = self._xml("word/document.xml")
        assert root is not None
        body = root.find(W + "body")
        if body is None:
            return []
        return [_parse_paragraph(p, i) for i, p in enumerate(body.findall(W + "p"))]

    # ---- styles.xml：样式基准 ----

    def style_baseline(self) -> dict[str, dict]:
        """style_id → {ascii, eastAsia, sz, jc}；basedOn 继承一层；含 docDefaults 兜底（key=None）。"""
        baseline: dict[str, dict] = {None: {"ascii": None, "eastAsia": None, "sz": None, "jc": None}}
        root = self._xml("word/styles.xml")
        if root is None:
            return baseline
        defaults = root.find(W + "docDefaults")
        if defaults is not None:
            rpr = defaults.find(f"{W}rPrDefault/{W}rPr")
            if rpr is not None:
                rf = rpr.find(W + "rFonts")
                if rf is not None:
                    baseline[None]["ascii"] = rf.get(W + "ascii")
                    baseline[None]["eastAsia"] = rf.get(W + "eastAsia")
                sz_el = rpr.find(W + "sz")
                sz_val = _val(sz_el) if sz_el is not None else None
                if sz_val:
                    baseline[None]["sz"] = int(sz_val)
        raw: dict[str, dict] = {}
        based_on: dict[str, str] = {}
        for st in root.findall(W + "style"):
            if st.get(W + "type") != "paragraph":
                continue
            sid = st.get(W + "styleId")
            if not sid:
                continue
            entry = {"ascii": None, "eastAsia": None, "sz": None, "jc": None}
            rpr = st.find(W + "rPr")
            if rpr is not None:
                rf = rpr.find(W + "rFonts")
                if rf is not None:
                    entry["ascii"] = rf.get(W + "ascii")
                    entry["eastAsia"] = rf.get(W + "eastAsia")
                sz_el = rpr.find(W + "sz")
                if sz_el is not None and (v := _val(sz_el)):
                    entry["sz"] = int(v)
            ppr = st.find(W + "pPr")
            if ppr is not None:
                entry["jc"] = _val(ppr.find(W + "jc"))
            raw[sid] = entry
            bo = st.find(W + "basedOn")
            if bo is not None and _val(bo):
                based_on[sid] = _val(bo)  # type: ignore[index]
        for sid, entry in raw.items():
            parent = raw.get(based_on.get(sid, ""), baseline[None])
            baseline[sid] = {
                k: entry[k] if entry[k] is not None else parent[k]
                for k in ("ascii", "eastAsia", "sz", "jc")
            }
        return baseline

    # ---- numbering.xml ----

    def num_ids(self) -> set[str]:
        root = self._xml("word/numbering.xml")
        if root is None:
            return set()
        return {v for v in (n.get(W + "numId") for n in root.findall(f"{W}num")) if v}
