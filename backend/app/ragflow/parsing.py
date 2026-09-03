"""解析器路由槽：按文件类型选解析策略。

W1 spike 裁决前的唯一策略是 ragflow-deepdoc（上传即由 RAGFlow DeepDoc 解析）。
后续候选（Docling / opendataloader / 双解析器交叉校验）以新策略名注册进
ROUTE 表即可，路由层与四个功能不感知具体实现——这是护栏3「窄接口」的落点。
"""
from pathlib import PurePosixPath

# 策略名 → 实现说明（当前仅一个，表先行是为了让「换解析器」成为改一行的事）
STRATEGIES: dict[str, str] = {
    "ragflow-deepdoc": "RAGFlow 内置 DeepDoc（docx/pdf/表格/版面）",
}

# 后缀 → 策略；未列出的后缀拒绝入库（与 kb 路由 MIME 白名单同立场）
ROUTE: dict[str, str] = {
    ".docx": "ragflow-deepdoc",
    ".pdf": "ragflow-deepdoc",
    ".txt": "ragflow-deepdoc",
    ".md": "ragflow-deepdoc",
    ".csv": "ragflow-deepdoc",
    ".xlsx": "ragflow-deepdoc",
}


def route_for(filename: str) -> str:
    """返回该文件应使用的解析策略名；不支持的后缀抛 ValueError。"""
    suffix = PurePosixPath(filename).suffix.lower()
    strategy = ROUTE.get(suffix)
    if strategy is None:
        raise ValueError(f"unsupported file type: {suffix or '(none)'}")
    return strategy
