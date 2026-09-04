"""
大纲层级断层与排版格式质检引擎 (Outline Validator & Format QC Engine)
实现 Features 15 & 16:
1. 大纲层级与序号断层检测 (1.1 -> 1.3 缺失 1.2、L1 -> L3 跳级、多编号体系)
2. 表格与排版格式质检 (空单元格比例、未合并表头异常、列表截断/断层、图表题注缺失)
"""

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    BoundingBox,
    TableData,
    UnifiedDocumentAST,
)
from app.schemas.audit import (
    DocumentQualityReport,
    FormatIssue,
    FormatIssueType,
    FormatValidationReport,
    NumberingFamily,
    OutlineIssue,
    OutlineIssueType,
    OutlineValidationReport,
    OutlineValidatorConfig,
)


# ===========================================================================
# 2. 多编号体系解析器 (NumberingParser)
# ===========================================================================

class HeadingNumberInfo(BaseModel):
    """标题编号解析元数据"""
    family: NumberingFamily
    raw_prefix: str
    sequence_tuple: Tuple[int, ...]
    unit: Optional[str] = None
    clean_title: str


class NumberingParser:
    """多源异构编号与中文/罗马/阿拉伯数字双向转换器"""

    CN_DIGITS: Dict[str, int] = {
        '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
        '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5,
        '陆': 6, '柒': 7, '捌': 8, '玖': 9
    }
    CN_UNITS: Dict[str, int] = {
        '十': 10, '拾': 10, '百': 100, '佰': 100, '千': 1000, '仟': 1000, '万': 10000
    }
    ROMAN_MAP: Dict[str, int] = {
        'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000
    }
    CIRCLED_CHARS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

    @classmethod
    def parse_chinese_numeral(cls, s: str) -> Optional[int]:
        """将中文大/小写数字字符串转为整数 (如 '二十三' -> 23, '一百零五' -> 105)"""
        s = s.strip()
        if not s:
            return None
        total = 0
        r = 0
        has_digit = False
        for char in s:
            if char in cls.CN_DIGITS:
                r = cls.CN_DIGITS[char]
                has_digit = True
            elif char in cls.CN_UNITS:
                unit = cls.CN_UNITS[char]
                if r == 0:
                    r = 1
                total += r * unit
                r = 0
                has_digit = True
            elif char.isdigit():
                # 兼容 "第1章" 混合场景
                return int(s)
            else:
                return None
        total += r
        return total if has_digit else None

    @classmethod
    def format_chinese_numeral(cls, n: int) -> str:
        """将正整数转为标准小写中文数字 (如 23 -> '二十三')"""
        if n <= 0:
            return str(n)
        cn_digits = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']
        if n < 10:
            return cn_digits[n]
        if n < 20:
            return '十' + (cn_digits[n % 10] if n % 10 != 0 else '')
        if n < 100:
            tens = n // 10
            rem = n % 10
            return cn_digits[tens] + '十' + (cn_digits[rem] if rem != 0 else '')
        if n < 1000:
            hundreds = n // 100
            rem = n % 100
            if rem == 0:
                return cn_digits[hundreds] + '百'
            elif rem < 10:
                return cn_digits[hundreds] + '百零' + cn_digits[rem]
            elif rem < 20:
                return cn_digits[hundreds] + '百一十' + (cn_digits[rem % 10] if rem % 10 != 0 else '')
            else:
                return cn_digits[hundreds] + '百' + cls.format_chinese_numeral(rem)
        return str(n)

    @classmethod
    def parse_roman_numeral(cls, s: str) -> Optional[int]:
        """将罗马数字转为整数 (如 'IV' -> 4, 'IX' -> 9)"""
        s = s.upper().strip()
        if not s or not all(c in cls.ROMAN_MAP for c in s):
            return None
        total = 0
        prev_val = 0
        for char in reversed(s):
            val = cls.ROMAN_MAP.get(char, 0)
            if val < prev_val:
                total -= val
            else:
                total += val
                prev_val = val
        return total if total > 0 else None

    @classmethod
    def format_roman_numeral(cls, n: int) -> str:
        """将正整数转为标准罗马数字大写字符串"""
        if n <= 0 or n > 3999:
            return str(n)
        val_map = [
            (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'),
            (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')
        ]
        res = []
        for val, sym in val_map:
            while n >= val:
                res.append(sym)
                n -= val
        return ''.join(res)

    @classmethod
    def parse_circled_numeral(cls, char: str) -> Optional[int]:
        """将带圈字符 ①..⑳ 解析为整数"""
        if len(char) == 1 and char in cls.CIRCLED_CHARS:
            return cls.CIRCLED_CHARS.index(char) + 1
        return None

    @classmethod
    def extract_heading_info(cls, text: str) -> Optional[HeadingNumberInfo]:
        """从标题原始文本中提取编号族系、序列元组及纯标题"""
        text = text.strip()
        if not text:
            return None

        # 1. 中文章节式: 第[一二三/1]章/节/篇/部/卷
        m1 = re.match(r'^(第\s*([一二三四五六七八九十百千\d]+)\s*([章节篇部卷]))\s*(.*)$', text)
        if m1:
            num = cls.parse_chinese_numeral(m1.group(2))
            if num is not None:
                return HeadingNumberInfo(
                    family=NumberingFamily.CHINESE_CHAPTER,
                    raw_prefix=m1.group(1),
                    sequence_tuple=(num,),
                    unit=m1.group(3),
                    clean_title=m1.group(4).strip()
                )

        # 2. 中文顿号序数: 一、, 二、, 二十三、
        m2 = re.match(r'^([一二三四五六七八九十百千]+)[、.．]\s*(.*)$', text)
        if m2:
            num = cls.parse_chinese_numeral(m2.group(1))
            if num is not None:
                return HeadingNumberInfo(
                    family=NumberingFamily.CHINESE_IDEOGRAPHIC,
                    raw_prefix=m2.group(0)[:len(m2.group(1)) + 1],
                    sequence_tuple=(num,),
                    clean_title=m2.group(2).strip()
                )

        # 3. 中文括号序数: （一）, (一), （十二）
        m3 = re.match(r'^[（(]([一二三四五六七八九十百千]+)[）)]\s*(.*)$', text)
        if m3:
            num = cls.parse_chinese_numeral(m3.group(1))
            if num is not None:
                return HeadingNumberInfo(
                    family=NumberingFamily.CHINESE_PARENTHESIZED,
                    raw_prefix=m3.group(0)[:len(m3.group(1)) + 2],
                    sequence_tuple=(num,),
                    clean_title=m3.group(2).strip()
                )

        # 4. 多级点分十进制: 1.1, 1.1.1, 2.1.3.4 (必须含至少一个点，且非单纯句号)
        m4 = re.match(r'^(\d+(?:\.\d+)+)\.?\s*(.*)$', text)
        if m4:
            nums = tuple(int(x) for x in m4.group(1).split('.'))
            return HeadingNumberInfo(
                family=NumberingFamily.DECIMAL_DOT,
                raw_prefix=m4.group(1),
                sequence_tuple=nums,
                clean_title=m4.group(2).strip()
            )

        # 5. 阿拉伯单数点: 1., 1、, 2.
        m5 = re.match(r'^(\d+)[、.．]\s*(.*)$', text)
        if m5:
            return HeadingNumberInfo(
                family=NumberingFamily.ARABIC_DOT,
                raw_prefix=m5.group(0).split()[0] if ' ' in m5.group(0) else m5.group(1),
                sequence_tuple=(int(m5.group(1)),),
                clean_title=m5.group(2).strip()
            )

        # 6. 阿拉伯数字括号: （1）, (1)
        m6 = re.match(r'^[（(](\d+)[）)]\s*(.*)$', text)
        if m6:
            return HeadingNumberInfo(
                family=NumberingFamily.ARABIC_PARENTHESIZED,
                raw_prefix=m6.group(0)[:len(m6.group(1)) + 2],
                sequence_tuple=(int(m6.group(1)),),
                clean_title=m6.group(2).strip()
            )

        # 7. 带圈数字: ①, ②
        if text and text[0] in cls.CIRCLED_CHARS:
            num = cls.parse_circled_numeral(text[0])
            if num is not None:
                return HeadingNumberInfo(
                    family=NumberingFamily.CIRCLED,
                    raw_prefix=text[0],
                    sequence_tuple=(num,),
                    clean_title=text[1:].strip()
                )

        # 8. 罗马数字: I., II., III.
        m8 = re.match(r'^([IVXLCDM]+|[ivxlcdm]+)[、.．]\s*(.*)$', text)
        if m8:
            r_num = cls.parse_roman_numeral(m8.group(1))
            if r_num is not None:
                return HeadingNumberInfo(
                    family=NumberingFamily.ROMAN,
                    raw_prefix=m8.group(0)[:len(m8.group(1)) + 1],
                    sequence_tuple=(r_num,),
                    clean_title=m8.group(2).strip()
                )

        # 9. 纯英文字母: A., B., C. 或 (A), (B)
        m9 = re.match(r'^(?:([A-Za-z])[、.．]|[（(]([A-Za-z])[）)])\s*(.*)$', text)
        if m9:
            char = m9.group(1) or m9.group(2)
            alpha_num = ord(char.upper()) - ord('A') + 1
            return HeadingNumberInfo(
                family=NumberingFamily.ALPHABETIC,
                raw_prefix=m9.group(0).split()[0],
                sequence_tuple=(alpha_num,),
                clean_title=m9.group(3).strip()
            )

        return None

    @classmethod
    def format_expected_number(
        cls,
        family: NumberingFamily,
        seq: Tuple[int, ...],
        unit: Optional[str] = None
    ) -> str:
        """根据族系与序列号还原规范的编号显示字符串"""
        if family == NumberingFamily.DECIMAL_DOT:
            return '.'.join(str(x) for x in seq)
        elif family == NumberingFamily.CHINESE_CHAPTER:
            num = seq[0] if seq else 1
            u = unit or "章"
            return f"第{cls.format_chinese_numeral(num)}{u}"
        elif family == NumberingFamily.CHINESE_IDEOGRAPHIC:
            num = seq[0] if seq else 1
            return f"{cls.format_chinese_numeral(num)}、"
        elif family == NumberingFamily.CHINESE_PARENTHESIZED:
            num = seq[0] if seq else 1
            return f"（{cls.format_chinese_numeral(num)}）"
        elif family == NumberingFamily.ARABIC_DOT:
            num = seq[0] if seq else 1
            return f"{num}."
        elif family == NumberingFamily.ARABIC_PARENTHESIZED:
            num = seq[0] if seq else 1
            return f"（{num}）"
        elif family == NumberingFamily.CIRCLED:
            num = seq[0] if seq else 1
            if 1 <= num <= 20:
                return cls.CIRCLED_CHARS[num - 1]
            return f"({num})"
        elif family == NumberingFamily.ROMAN:
            num = seq[0] if seq else 1
            return f"{cls.format_roman_numeral(num)}."
        elif family == NumberingFamily.ALPHABETIC:
            num = seq[0] if seq else 1
            return f"{chr(ord('A') + num - 1)}."
        return '.'.join(str(x) for x in seq)


# ===========================================================================
# 3. 大纲层级断层质检器 (OutlineValidator - Feature 15)
# ===========================================================================

class OutlineValidator:
    """标题树大纲跃升跳级与序号断层质检器"""

    def __init__(self, config: Optional[OutlineValidatorConfig] = None):
        self.config = config or OutlineValidatorConfig()

    def validate(self, ast: UnifiedDocumentAST) -> OutlineValidationReport:
        """执行大纲层级与编号全面核验"""
        issues: List[OutlineIssue] = []
        heading_nodes = [node for node in ast.nodes if node.block_type == ASTBlockType.HEADING]

        if not heading_nodes:
            return OutlineValidationReport(
                document_id=ast.document_id,
                total_headings_inspected=0,
                is_valid=True,
                max_heading_level=0,
                summary="文档未包含任何 HEADING 类型的大纲标题节点。"
            )

        conventions_detected: Set[str] = set()
        max_level = 0
        prev_level = 0

        # 存储各作用域下的最后连续序号:
        # 针对点分十进制: prefix_tuple -> last_int (如 (1,) -> 2 表示 1.2)
        decimal_scope_tracker: Dict[Tuple[int, ...], int] = {}
        # 针对离散体系: (parent_section_path_str, family) -> last_int
        discrete_scope_tracker: Dict[Tuple[str, NumberingFamily], int] = {}

        for idx, h_node in enumerate(heading_nodes):
            raw_text = h_node.text_content.strip()
            level = h_node.level or 1
            max_level = max(max_level, level)

            # 检查空标题
            if not raw_text:
                issues.append(
                    OutlineIssue(
                        issue_type=OutlineIssueType.EMPTY_HEADING_TITLE,
                        severity=SeverityLevel.HIGH,
                        node_id=h_node.block_id,
                        section_path=h_node.section_path,
                        current_heading="",
                        current_level=level,
                        page_or_sheet=h_node.page_or_sheet,
                        bbox=h_node.bbox,
                        message="检测到空文本的大纲标题节点",
                        suggestion="补充该标题文本或移除无效的标题样式标记"
                    )
                )
                continue

            # -------------------------------------------------------------
            # A. 标题层级跳级检查 (Level Jump & Root Level Skip)
            # -------------------------------------------------------------
            if idx == 0:
                if level > 1 and not self.config.allow_root_level_skip:
                    issues.append(
                        OutlineIssue(
                            issue_type=OutlineIssueType.ROOT_LEVEL_SKIP,
                            severity=SeverityLevel.MEDIUM,
                            node_id=h_node.block_id,
                            section_path=h_node.section_path,
                            current_heading=raw_text,
                            current_level=level,
                            expected_level=1,
                            page_or_sheet=h_node.page_or_sheet,
                            bbox=h_node.bbox,
                            message=f"文档首个大纲标题直接以 {level} 级标题开头，缺失 1 级主标题（根标题断层）",
                            suggestion=f"将标题「{raw_text}」提升为 1 级标题，或在前方增补 1 级主标题"
                        )
                    )
            else:
                if level > prev_level + self.config.max_heading_level_jump:
                    expected_lvl = prev_level + 1
                    issues.append(
                        OutlineIssue(
                            issue_type=OutlineIssueType.LEVEL_JUMP,
                            severity=SeverityLevel.HIGH,
                            node_id=h_node.block_id,
                            section_path=h_node.section_path,
                            current_heading=raw_text,
                            current_level=level,
                            expected_level=expected_lvl,
                            page_or_sheet=h_node.page_or_sheet,
                            bbox=h_node.bbox,
                            message=f"大纲标题层级跳跃：从 {prev_level} 级直接跃升至 {level} 级，跳过 {expected_lvl} 级中间父标题",
                            suggestion=f"将标题「{raw_text}」调整为 {expected_lvl} 级，或在二者之间补充中间层级父标题"
                        )
                    )

            prev_level = level

            # -------------------------------------------------------------
            # B. 编号解析与序号断层检查 (Sequence Gap Detection)
            # -------------------------------------------------------------
            num_info = NumberingParser.extract_heading_info(raw_text)
            if not num_info:
                conventions_detected.add(NumberingFamily.UNNUMBERED.value)
                continue

            conventions_detected.add(num_info.family.value)

            if num_info.family == NumberingFamily.DECIMAL_DOT:
                # 多级点分体系: 依据前缀元组划分作用域
                seq = num_info.sequence_tuple
                prefix = seq[:-1]
                last_num = seq[-1]

                if prefix in decimal_scope_tracker:
                    prev_num = decimal_scope_tracker[prefix]
                    if last_num > prev_num + 1:
                        # 发现断层
                        missing_seqs = [prefix + (i,) for i in range(prev_num + 1, last_num)]
                        missing_strs = ['.'.join(str(x) for x in m) for m in missing_seqs]
                        exp_str = '.'.join(str(x) for x in prefix + (prev_num + 1,))
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.SEQUENCE_GAP,
                                severity=SeverityLevel.HIGH,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                expected_heading=exp_str,
                                missing_items=missing_strs,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"标题序号断层：在同级序号 {'.'.join(str(x) for x in prefix + (prev_num,))} 之后直接出现 {num_info.raw_prefix}，缺失 {missing_strs}",
                                suggestion=f"检查是否遗漏了序号为 {missing_strs} 的章节，或将当前编号调整为 {exp_str}"
                            )
                        )
                    elif last_num == prev_num:
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.DUPLICATE_NUMBER,
                                severity=SeverityLevel.HIGH,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"检测到重复的标题序号：「{num_info.raw_prefix}」已在当前同级章节中存在",
                                suggestion="修正重复的标题编号，确保同一层级章节编号唯一"
                            )
                        )
                    elif last_num < prev_num:
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.OUT_OF_ORDER,
                                severity=SeverityLevel.HIGH,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"标题序号倒序：当前标题序号「{num_info.raw_prefix}」小于前一标题序号",
                                suggestion="调整章节编排顺序或重新编号"
                            )
                        )
                else:
                    # 该前缀下首个子项
                    if last_num != 1 and self.config.strict_prefix_matching:
                        missing_strs = ['.'.join(str(x) for x in prefix + (i,)) for i in range(1, last_num)]
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.SEQUENCE_GAP,
                                severity=SeverityLevel.MEDIUM,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                missing_items=missing_strs,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"章节起始序号不规范：前缀 {'.'.join(str(x) for x in prefix) or '根'} 下子章节直接从 {last_num} 开始，缺少 {missing_strs}",
                                suggestion=f"建议将首个子节起始编号设定为 1（如 {'.'.join(str(x) for x in prefix + (1,))}）"
                            )
                        )
                decimal_scope_tracker[prefix] = last_num

            else:
                # 离散编号族系 (第一章、一、(一)、1. 等)
                parent_path_str = "/".join(h_node.section_path[:-1]) if h_node.section_path else "root"
                scope_key = (parent_path_str, num_info.family)
                curr_num = num_info.sequence_tuple[0]

                if scope_key in discrete_scope_tracker:
                    prev_num = discrete_scope_tracker[scope_key]
                    if curr_num > prev_num + 1:
                        missing_nums = list(range(prev_num + 1, curr_num))
                        missing_strs = [
                            NumberingParser.format_expected_number(num_info.family, (m,), num_info.unit)
                            for m in missing_nums
                        ]
                        exp_str = NumberingParser.format_expected_number(num_info.family, (prev_num + 1,), num_info.unit)
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.SEQUENCE_GAP,
                                severity=SeverityLevel.HIGH,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                expected_heading=exp_str,
                                missing_items=missing_strs,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"标题序号断层：在同级「{NumberingParser.format_expected_number(num_info.family, (prev_num,), num_info.unit)}」之后直接出现「{num_info.raw_prefix}」，缺失 {missing_strs}",
                                suggestion=f"核实是否遗漏章节内容，或修正序号为「{exp_str}」"
                            )
                        )
                    elif curr_num == prev_num:
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.DUPLICATE_NUMBER,
                                severity=SeverityLevel.HIGH,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"检测到重复的同级标题序号：「{num_info.raw_prefix}」",
                                suggestion="修正重复的标题序号"
                            )
                        )
                    elif curr_num < prev_num:
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.OUT_OF_ORDER,
                                severity=SeverityLevel.HIGH,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"标题序号倒序：当前标题序号「{num_info.raw_prefix}」小于前置序号",
                                suggestion="调整章节排列次序或修正序号"
                            )
                        )
                else:
                    if curr_num != 1 and num_info.family in (NumberingFamily.CHINESE_CHAPTER, NumberingFamily.CHINESE_IDEOGRAPHIC):
                        missing_strs = [
                            NumberingParser.format_expected_number(num_info.family, (m,), num_info.unit)
                            for m in range(1, curr_num)
                        ]
                        issues.append(
                            OutlineIssue(
                                issue_type=OutlineIssueType.SEQUENCE_GAP,
                                severity=SeverityLevel.MEDIUM,
                                node_id=h_node.block_id,
                                section_path=h_node.section_path,
                                current_heading=raw_text,
                                current_level=level,
                                missing_items=missing_strs,
                                page_or_sheet=h_node.page_or_sheet,
                                bbox=h_node.bbox,
                                message=f"章节起始序号断层：当前章节直接从「{num_info.raw_prefix}」开始，缺失起始项 {missing_strs}",
                                suggestion="补齐前置初始章节或调整编号从第1项起始"
                            )
                        )
                discrete_scope_tracker[scope_key] = curr_num

        # 统计严重度分布
        severity_counts: Dict[str, int] = {}
        for iss in issues:
            s_val = iss.severity.value
            severity_counts[s_val] = severity_counts.get(s_val, 0) + 1

        is_valid = len([iss for iss in issues if iss.severity in (SeverityLevel.HIGH, SeverityLevel.CRITICAL)]) == 0
        summary = (
            f"大纲核验完成：检查大纲标题 {len(heading_nodes)} 个，发现问题 {len(issues)} 处 "
            f"(高危/严重: {severity_counts.get('high', 0) + severity_counts.get('critical', 0)}，"
            f"中危: {severity_counts.get('medium', 0)}，低危: {severity_counts.get('low', 0)})。"
        )

        return OutlineValidationReport(
            document_id=ast.document_id,
            total_headings_inspected=len(heading_nodes),
            is_valid=is_valid,
            max_heading_level=max_level,
            numbering_conventions_detected=sorted(list(conventions_detected)),
            issues=issues,
            issue_count_by_severity=severity_counts,
            summary=summary
        )


# ===========================================================================
# 4. 排版格式与表格质检器 (FormatValidator - Feature 16)
# ===========================================================================

class FormatValidator:
    """排版布局、表格结构完整性、正文列表与题注连续性质检器"""

    CAPTION_TABLE_PATTERN = re.compile(r'^(?:表|Table)\s*([0-9]+(?:[\.\-][0-9]+)*)', re.IGNORECASE)
    CAPTION_FIGURE_PATTERN = re.compile(r'^(?:图|Figure)\s*([0-9]+(?:[\.\-][0-9]+)*)', re.IGNORECASE)
    FIGURE_REF_PATTERN = re.compile(r'(?:[见如]|参见)(?:附?图|Figure)\s*([0-9]+(?:[\.\-][0-9]+)*)', re.IGNORECASE)
    LIST_MARKER_PATTERN = re.compile(r'^([（(]\d+[）)]|\d+[、.．]|[①②③④⑤⑥⑦⑧⑨⑩]|•|[-*])\s*(.*)$')

    def __init__(self, config: Optional[OutlineValidatorConfig] = None):
        self.config = config or OutlineValidatorConfig()

    def validate(self, ast: UnifiedDocumentAST) -> FormatValidationReport:
        """执行表格、正文列表与题注引用全维度排版质检"""
        issues: List[FormatIssue] = []
        table_nodes = [node for node in ast.nodes if node.block_type == ASTBlockType.TABLE]
        paragraph_nodes = [node for node in ast.nodes if node.block_type in (ASTBlockType.PARAGRAPH, ASTBlockType.CALLOUT)]

        # -------------------------------------------------------------
        # A. 表格结构与空单元格质检
        # -------------------------------------------------------------
        total_cells_all = 0
        total_empty_cells_all = 0
        table_with_issues_count = 0

        # 记录文档中出现的表/图题注标号，用于校验连续性与反查悬挂引用
        table_captions_found: List[Tuple[str, str]] = []  # (block_id, caption_num_str)
        figure_captions_found: List[Tuple[str, str]] = []

        # 预先扫描所有段落提取图表题注
        for p_node in ast.nodes:
            txt = p_node.text_content.strip()
            m_tab = self.CAPTION_TABLE_PATTERN.match(txt)
            if m_tab:
                table_captions_found.append((p_node.block_id, m_tab.group(1)))
            m_fig = self.CAPTION_FIGURE_PATTERN.match(txt)
            if m_fig:
                figure_captions_found.append((p_node.block_id, m_fig.group(1)))

        for t_idx, t_node in enumerate(table_nodes):
            t_data: Optional[TableData] = t_node.table_data
            if not t_data or (not t_data.headers and not t_data.rows and not t_data.cells):
                issues.append(
                    FormatIssue(
                        issue_type=FormatIssueType.TABLE_EMPTY,
                        severity=SeverityLevel.HIGH,
                        node_id=t_node.block_id,
                        section_path=t_node.section_path,
                        page_or_sheet=t_node.page_or_sheet,
                        bbox=t_node.bbox,
                        message="检测到空表格节点：表格未包含任何有效表头或行数据",
                        suggestion="核实源文档该表格是否解析遗漏或存在损坏"
                    )
                )
                table_with_issues_count += 1
                continue

            # 1. 检查各行列数对齐一致性 (网格对齐)
            all_grid_rows: List[List[str]] = []
            if t_data.headers:
                all_grid_rows.extend(t_data.headers)
            if t_data.rows:
                all_grid_rows.extend(t_data.rows)

            if all_grid_rows:
                base_col_count = len(all_grid_rows[0])
                for r_idx, row in enumerate(all_grid_rows):
                    if len(row) != base_col_count:
                        issues.append(
                            FormatIssue(
                                issue_type=FormatIssueType.TABLE_COLUMN_MISMATCH,
                                severity=SeverityLevel.HIGH,
                                node_id=t_node.block_id,
                                section_path=t_node.section_path,
                                page_or_sheet=t_node.page_or_sheet,
                                bbox=t_node.bbox,
                                metric_name="column_count",
                                metric_value=float(len(row)),
                                threshold=float(base_col_count),
                                details={"row_index": r_idx, "expected_cols": base_col_count, "actual_cols": len(row)},
                                message=f"表格结构不对齐：第 {r_idx + 1} 行为 {len(row)} 列，与基准列数 {base_col_count} 不匹配",
                                suggestion="检查合并单元格 (colspan/rowspan) 是否展开异常或单元格丢失"
                            )
                        )
                        table_with_issues_count += 1
                        break

            # 2. 检查空单元格比例 (Empty cell ratio)
            data_rows = t_data.rows
            if data_rows:
                tbl_total_cells = sum(len(r) for r in data_rows)
                tbl_empty_cells = sum(1 for r in data_rows for c in r if not str(c).strip())
                total_cells_all += tbl_total_cells
                total_empty_cells_all += tbl_empty_cells

                empty_ratio = tbl_empty_cells / tbl_total_cells if tbl_total_cells > 0 else 0.0
                if empty_ratio > self.config.max_empty_cell_ratio:
                    sev = SeverityLevel.HIGH if empty_ratio > 0.60 else SeverityLevel.MEDIUM
                    issues.append(
                        FormatIssue(
                            issue_type=FormatIssueType.TABLE_EMPTY_CELL_RATIO_HIGH,
                            severity=sev,
                            node_id=t_node.block_id,
                            section_path=t_node.section_path,
                            page_or_sheet=t_node.page_or_sheet,
                            bbox=t_node.bbox,
                            metric_name="empty_cell_ratio",
                            metric_value=round(empty_ratio, 4),
                            threshold=self.config.max_empty_cell_ratio,
                            details={"total_cells": tbl_total_cells, "empty_cells": tbl_empty_cells},
                            message=f"表格空单元格比例异常 ({empty_ratio:.1%})，超过告警阈值 ({self.config.max_empty_cell_ratio:.1%})",
                            suggestion="检查是否发生表格字段丢弃，或确认原表是否存在大量未填报字段"
                        )
                    )
                    table_with_issues_count += 1

                # 3. 检查全空行与全空列
                for r_idx, r in enumerate(data_rows):
                    if r and all(not str(c).strip() for c in r):
                        issues.append(
                            FormatIssue(
                                issue_type=FormatIssueType.TABLE_EMPTY_ROW,
                                severity=SeverityLevel.LOW,
                                node_id=t_node.block_id,
                                section_path=t_node.section_path,
                                page_or_sheet=t_node.page_or_sheet,
                                bbox=t_node.bbox,
                                details={"row_index": r_idx},
                                message=f"表格存在全空数据行（第 {r_idx + 1} 行全空）",
                                suggestion="移除空数据行以精简表格结构"
                            )
                        )

                # 全空列检查
                if data_rows and len(data_rows[0]) > 0:
                    num_cols = len(data_rows[0])
                    for c_idx in range(num_cols):
                        col_vals = [r[c_idx] for r in data_rows if len(r) > c_idx]
                        if col_vals and all(not str(v).strip() for v in col_vals):
                            col_header = (
                                t_data.headers[0][c_idx]
                                if t_data.headers and len(t_data.headers[0]) > c_idx
                                else f"第{c_idx+1}列"
                            )
                            issues.append(
                                FormatIssue(
                                    issue_type=FormatIssueType.TABLE_EMPTY_COLUMN,
                                    severity=SeverityLevel.MEDIUM,
                                    node_id=t_node.block_id,
                                    section_path=t_node.section_path,
                                    page_or_sheet=t_node.page_or_sheet,
                                    bbox=t_node.bbox,
                                    details={"col_index": c_idx, "header": col_header},
                                    message=f"表格存在全空数据列：第 {c_idx + 1} 列（表头: 「{col_header}」）数据全空",
                                    suggestion="核实该项业务指标是否遗漏填写"
                                )
                            )

            # 4. 检查未合并表头异常 (Unmerged Header Structural Anomaly)
            if t_data.headers:
                for h_idx, h_row in enumerate(t_data.headers):
                    for c_idx, h_cell in enumerate(h_row):
                        # 如果表头单元格为空，但在原始 cells 中并没有被标识为被前一格合并
                        if not str(h_cell).strip():
                            issues.append(
                                FormatIssue(
                                    issue_type=FormatIssueType.TABLE_UNMERGED_HEADER,
                                    severity=SeverityLevel.MEDIUM,
                                    node_id=t_node.block_id,
                                    section_path=t_node.section_path,
                                    page_or_sheet=t_node.page_or_sheet,
                                    bbox=t_node.bbox,
                                    details={"header_row": h_idx, "col_index": c_idx},
                                    message=f"表头存在空白单元格：表头第 {h_idx + 1} 行第 {c_idx + 1} 列为空白，疑似跨列合并断裂",
                                    suggestion="检查复合多级表头的合并跨度 (col_span/row_span) 配置"
                                )
                            )
                            table_with_issues_count += 1
                            break

            # 5. 检查表格题注缺失
            if self.config.table_caption_required:
                # 寻找关联题注
                has_caption = False
                if t_data.summary and any(k in t_data.summary for k in ("表", "Table")):
                    has_caption = True
                elif t_data.markdown and re.search(r'\*\*(?:表|Table)\s*[\d\.\-]+', t_data.markdown):
                    has_caption = True
                else:
                    # 检查前后邻近节点
                    node_idx = ast.nodes.index(t_node)
                    if node_idx > 0:
                        prev_node = ast.nodes[node_idx - 1]
                        if self.CAPTION_TABLE_PATTERN.match(prev_node.text_content.strip()):
                            has_caption = True
                    if not has_caption and node_idx < len(ast.nodes) - 1:
                        next_node = ast.nodes[node_idx + 1]
                        if self.CAPTION_TABLE_PATTERN.match(next_node.text_content.strip()):
                            has_caption = True

                if not has_caption:
                    issues.append(
                        FormatIssue(
                            issue_type=FormatIssueType.MISSING_TABLE_CAPTION,
                            severity=SeverityLevel.LOW,
                            node_id=t_node.block_id,
                            section_path=t_node.section_path,
                            page_or_sheet=t_node.page_or_sheet,
                            bbox=t_node.bbox,
                            message="表格缺少明确编号与题注说明（如「表1-1 设备技术规格清单」）",
                            suggestion="在表格前或表格属性中添加标准表格编号与题注"
                        )
                    )

        # -------------------------------------------------------------
        # B. 题注序号连续性与正文图件反查引用
        # -------------------------------------------------------------
        if self.config.check_caption_continuity:
            # 校验表题注序号连续性
            self._check_caption_continuity(table_captions_found, FormatIssueType.TABLE_CAPTION_SEQUENCE_GAP, "表", issues, ast)
            # 校验图题注序号连续性
            self._check_caption_continuity(figure_captions_found, FormatIssueType.FIGURE_CAPTION_SEQUENCE_GAP, "图", issues, ast)

        # 正文引用反查：检查正文引用了 "见图 2-1"，但在 figure_captions_found 中未定义
        all_fig_num_set = {num for _, num in figure_captions_found}
        for p_node in paragraph_nodes:
            matches = self.FIGURE_REF_PATTERN.findall(p_node.text_content)
            for ref_num in matches:
                # 规范化对齐
                clean_ref = ref_num.replace(".", "-")
                matched = any(f_num.replace(".", "-") == clean_ref for f_num in all_fig_num_set)
                if not matched:
                    issues.append(
                        FormatIssue(
                            issue_type=FormatIssueType.ORPHAN_FIGURE_REFERENCE,
                            severity=SeverityLevel.LOW,
                            node_id=p_node.block_id,
                            section_path=p_node.section_path,
                            page_or_sheet=p_node.page_or_sheet,
                            bbox=p_node.bbox,
                            details={"referenced_figure": ref_num},
                            message=f"正文引用了未定义图件：文中引用「图{ref_num}」，但在文档中未检索到对应图件题注",
                            suggestion="核对正文图件编号引用，或补充该图件与说明题注"
                        )
                    )

        # -------------------------------------------------------------
        # C. 列表项断层、截断与悬挂标记检查
        # -------------------------------------------------------------
        if self.config.check_broken_lists:
            self._check_list_structures(paragraph_nodes, issues)

        # 汇总统计
        severity_counts: Dict[str, int] = {}
        for iss in issues:
            s_val = iss.severity.value
            severity_counts[s_val] = severity_counts.get(s_val, 0) + 1

        is_valid = len([iss for iss in issues if iss.severity in (SeverityLevel.HIGH, SeverityLevel.CRITICAL)]) == 0
        avg_empty_ratio = (total_empty_cells_all / total_cells_all) if total_cells_all > 0 else 0.0

        summary = (
            f"排版格式质检完成：检查表格 {len(table_nodes)} 个，段落 {len(paragraph_nodes)} 个；"
            f"平均表格空单元格率 {avg_empty_ratio:.1%}；发现排版/表格异常 {len(issues)} 处 "
            f"(严重/高危: {severity_counts.get('high', 0) + severity_counts.get('critical', 0)}，"
            f"中危: {severity_counts.get('medium', 0)}，低危: {severity_counts.get('low', 0)})。"
        )

        return FormatValidationReport(
            document_id=ast.document_id,
            total_tables_inspected=len(table_nodes),
            total_paragraphs_inspected=len(paragraph_nodes),
            is_valid=is_valid,
            issues=issues,
            issue_count_by_severity=severity_counts,
            table_stats={
                "total_tables": len(table_nodes),
                "tables_with_issues": table_with_issues_count,
                "avg_empty_cell_ratio": round(avg_empty_ratio, 4),
                "total_cells": total_cells_all,
                "empty_cells": total_empty_cells_all
            },
            list_stats={"total_paragraphs": len(paragraph_nodes)},
            summary=summary
        )

    def _check_caption_continuity(
        self,
        captions: List[Tuple[str, str]],
        issue_type: FormatIssueType,
        prefix_name: str,
        issues: List[FormatIssue],
        ast: UnifiedDocumentAST
    ) -> None:
        """检查题注编号的升序连续性 (如 表1-1 -> 表1-3 缺失 表1-2)"""
        if len(captions) < 2:
            return

        # 分章节作用域比对: 如 "1-1" -> chapter 1, seq 1
        node_map = {node.block_id: node for node in ast.nodes}
        chapter_last_seq: Dict[str, int] = {}

        for b_id, cap_str in captions:
            node = node_map.get(b_id)
            parts = re.split(r'[\.\-]', cap_str)
            if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
                chap = parts[0]
                seq = int(parts[1])
                if chap in chapter_last_seq:
                    prev_seq = chapter_last_seq[chap]
                    if seq > prev_seq + 1:
                        missing = [f"{chap}-{i}" for i in range(prev_seq + 1, seq)]
                        issues.append(
                            FormatIssue(
                                issue_type=issue_type,
                                severity=SeverityLevel.MEDIUM,
                                node_id=b_id,
                                section_path=node.section_path if node else [],
                                page_or_sheet=node.page_or_sheet if node else None,
                                bbox=node.bbox if node else None,
                                details={"missing_captions": missing},
                                message=f"{prefix_name}题注编号断层：在「{prefix_name}{chap}-{prev_seq}」之后直接出现「{prefix_name}{chap}-{seq}」，缺失 {missing}",
                                suggestion=f"核实是否遗漏了 {missing} 的{prefix_name}，或重新调整编号"
                            )
                        )
                chapter_last_seq[chap] = seq

    def _check_list_structures(
        self,
        paragraphs: List[ASTNode],
        issues: List[FormatIssue]
    ) -> None:
        """检查正文段落列表项的连续性、异常截断与悬挂标记"""
        current_cluster: List[Tuple[ASTNode, str, str]] = []

        for p_node in paragraphs:
            txt = p_node.text_content.strip()
            m = self.LIST_MARKER_PATTERN.match(txt)
            if m:
                marker, content = m.group(1), m.group(2)
                current_cluster.append((p_node, marker, content))
            else:
                if current_cluster:
                    self._evaluate_list_cluster(current_cluster, issues)
                    current_cluster = []

        if current_cluster:
            self._evaluate_list_cluster(current_cluster, issues)

    def _evaluate_list_cluster(
        self,
        cluster: List[Tuple[ASTNode, str, str]],
        issues: List[FormatIssue]
    ) -> None:
        """评估同一列表簇内的项"""
        # 1. 检查悬挂标记与未完成截断
        for p_node, marker, content in cluster:
            c_strip = content.strip()
            if not c_strip:
                issues.append(
                    FormatIssue(
                        issue_type=FormatIssueType.HANGING_LIST_MARKER,
                        severity=SeverityLevel.MEDIUM,
                        node_id=p_node.block_id,
                        section_path=p_node.section_path,
                        page_or_sheet=p_node.page_or_sheet,
                        bbox=p_node.bbox,
                        details={"marker": marker},
                        message=f"悬挂列表标记异常：列表项「{marker}」后无任何实质正文内容",
                        suggestion="补充列表项具体内容或移除多余的空列表标记"
                    )
                )
            elif c_strip.endswith(('，', '、', '以及', '并且', '如下：', '包括：')):
                issues.append(
                    FormatIssue(
                        issue_type=FormatIssueType.TRUNCATED_LIST_ITEM,
                        severity=SeverityLevel.LOW,
                        node_id=p_node.block_id,
                        section_path=p_node.section_path,
                        page_or_sheet=p_node.page_or_sheet,
                        bbox=p_node.bbox,
                        details={"marker": marker, "ending_char": c_strip[-3:]},
                        message=f"列表项存在文本截断或句子未完结：项末尾以「{c_strip[-3:]}」结束但缺失下文",
                        suggestion="检查该列表项内容是否由于换页或复制粘贴而发生文字截断"
                    )
                )

        # 2. 检查数字列表序号连续性 (如 (1), (3))
        seq_items: List[Tuple[ASTNode, int, str]] = []
        for p_node, marker, _ in cluster:
            m_num = re.search(r'\d+', marker)
            if m_num:
                seq_items.append((p_node, int(m_num.group(0)), marker))

        if len(seq_items) >= 2:
            for i in range(len(seq_items) - 1):
                p1, n1, m1 = seq_items[i]
                p2, n2, m2 = seq_items[i + 1]
                if n2 > n1 + 1:
                    missing = list(range(n1 + 1, n2))
                    issues.append(
                        FormatIssue(
                            issue_type=FormatIssueType.BROKEN_LIST_SEQUENCE,
                            severity=SeverityLevel.MEDIUM,
                            node_id=p2.block_id,
                            section_path=p2.section_path,
                            page_or_sheet=p2.page_or_sheet,
                            bbox=p2.bbox,
                            details={"missing_numbers": missing},
                            message=f"正文列表项序号断层：在「{m1}」之后直接出现「{m2}」，缺失序号 {missing}",
                            suggestion=f"检查是否遗漏了序号为 {missing} 的列表内容，或修正序号连续性"
                        )
                    )


# ===========================================================================
# 5. 统一质检执行引擎与领域模型转换桥接器 (DocumentQualityEngine)
# ===========================================================================

class DocumentQualityEngine:
    """文档统一排版与大纲综合质检引擎，提供对外单一主调用门面"""

    def __init__(self, config: Optional[OutlineValidatorConfig] = None):
        self.config = config or OutlineValidatorConfig()
        self.outline_validator = OutlineValidator(self.config)
        self.format_validator = FormatValidator(self.config)

    def validate_document(self, ast: UnifiedDocumentAST) -> DocumentQualityReport:
        """对 UnifiedDocumentAST 执行全套质检，返回结构化总报告"""
        outline_rep = self.outline_validator.validate(ast)
        format_rep = self.format_validator.validate(ast)

        # 计算综合质量健康分 (满分100，根据问题严重程度梯度扣减)
        score = 100.0
        high_risk = 0

        for iss in outline_rep.issues:
            if iss.severity == SeverityLevel.CRITICAL:
                score -= 25.0
                high_risk += 1
            elif iss.severity == SeverityLevel.HIGH:
                score -= 10.0
                high_risk += 1
            elif iss.severity == SeverityLevel.MEDIUM:
                score -= 4.0
            elif iss.severity == SeverityLevel.LOW:
                score -= 1.0

        for iss in format_rep.issues:
            if iss.severity == SeverityLevel.CRITICAL:
                score -= 25.0
                high_risk += 1
            elif iss.severity == SeverityLevel.HIGH:
                score -= 10.0
                high_risk += 1
            elif iss.severity == SeverityLevel.MEDIUM:
                score -= 4.0
            elif iss.severity == SeverityLevel.LOW:
                score -= 1.0

        final_score = max(0.0, min(100.0, round(score, 1)))
        passed = (final_score >= 80.0) and (high_risk == 0)

        return DocumentQualityReport(
            document_id=ast.document_id,
            file_name=ast.file_name,
            tenant_id=ast.tenant_id,
            overall_score=final_score,
            passed=passed,
            total_issues_count=len(outline_rep.issues) + len(format_rep.issues),
            high_risk_count=high_risk,
            outline_report=outline_rep,
            format_report=format_rep
        )

    def to_review_results(
        self,
        report: DocumentQualityReport,
        task_id: str,
        tenant_id: str
    ) -> List[ReviewResult]:
        """将质检报告中的所有问题转换为可直接入库的 ReviewResult 数据库实体列表"""
        results: List[ReviewResult] = []

        # 1. 转换大纲问题
        for iss in report.outline_report.issues:
            pg = int(iss.page_or_sheet) if (iss.page_or_sheet and iss.page_or_sheet.isdigit()) else None
            results.append(
                ReviewResult(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    deviation_type=(
                        DeviationType.NEGATIVE
                        if iss.severity in (SeverityLevel.HIGH, SeverityLevel.CRITICAL)
                        else DeviationType.NOT_APPLICABLE
                    ),
                    severity=iss.severity,
                    confidence=1.0,
                    rule_category="outline_hierarchy",
                    title=f"大纲异常: {iss.issue_type.value}",
                    description=iss.message,
                    suggestion=iss.suggestion,
                    source_section="/".join(iss.section_path),
                    source_page=pg,
                    source_quote=iss.current_heading,
                    diff_payload=iss.model_dump()
                )
            )

        # 2. 转换格式问题
        for iss in report.format_report.issues:
            pg = int(iss.page_or_sheet) if (iss.page_or_sheet and iss.page_or_sheet.isdigit()) else None
            results.append(
                ReviewResult(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    deviation_type=(
                        DeviationType.NEGATIVE
                        if iss.severity in (SeverityLevel.HIGH, SeverityLevel.CRITICAL)
                        else DeviationType.NOT_APPLICABLE
                    ),
                    severity=iss.severity,
                    confidence=1.0,
                    rule_category="table_and_format",
                    title=f"排版格式异常: {iss.issue_type.value}",
                    description=iss.message,
                    suggestion=iss.suggestion,
                    source_section="/".join(iss.section_path),
                    source_page=pg,
                    diff_payload=iss.model_dump()
                )
            )

        return results
