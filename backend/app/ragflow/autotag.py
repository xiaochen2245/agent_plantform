"""自动打标（#27）：上传触发解析后，后台轮询 run=DONE 即自动打标。

- fire-and-forget asyncio 任务由上传端点 spawn；进程重启即丢，
  管理页打标按钮兜底重试（失败只记日志，绝不阻塞上传响应）。
- client 复用请求依赖注入的实例（缓存于 app.state，lifespan 统一关闭）。
"""
import asyncio
import logging

from app.ragflow.client import RagflowClient, RagflowError
from app.ragflow.tagging import Tagger

_logger = logging.getLogger("app.ragflow.autotag")

POLL_INTERVAL = 10.0    # 秒；解析中档时长数十秒~数分钟
POLL_TIMEOUT = 15 * 60  # 秒；超时放弃转人工重试（大 PDF 量级）

_tasks: set[asyncio.Task] = set()  # 持引用防 GC；done 自动摘除


def spawn_autotag(client: RagflowClient, dataset_id: str, document_ids: list[str]) -> None:
    for doc_id in document_ids:
        t = asyncio.create_task(tag_when_done(client, dataset_id, doc_id))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)


async def _find_doc(client: RagflowClient, dataset_id: str, document_id: str) -> dict | None:
    """翻页找目标文档（list_documents 单页 30 条）。"""
    page = 1
    while True:
        docs = await client.list_documents(dataset_id, page=page)
        if not docs:
            return None
        for d in docs:
            if d.get("id") == document_id:
                return d
        page += 1


async def _tag(client: RagflowClient, dataset_id: str, document_id: str) -> None:
    chunks = await client.list_chunks(dataset_id, document_id)
    if not chunks:
        _logger.warning("autotag no chunks ds=%s doc=%s", dataset_id, document_id)
        return
    labels = await Tagger().extract(chunks)
    if labels is None:
        _logger.warning("autotag extract failed ds=%s doc=%s", dataset_id, document_id)
        return
    await client.update_document_meta(dataset_id, document_id, Tagger.to_meta_fields(labels))
    _logger.info("autotag done ds=%s doc=%s project=%s", dataset_id, document_id, labels.project)


async def tag_when_done(client: RagflowClient, dataset_id: str, document_id: str) -> None:
    """轮询解析状态：DONE → 打标；FAIL/CANCEL/超时/上游错误 → 记日志退出。"""
    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT
    while True:
        try:
            doc = await _find_doc(client, dataset_id, document_id)
            run = (doc or {}).get("run")
        except RagflowError as e:
            _logger.warning("autotag poll error ds=%s doc=%s: %s", dataset_id, document_id, e.message)
            return
        if run in ("FAIL", "CANCEL"):
            _logger.warning("autotag parse %s ds=%s doc=%s", run, dataset_id, document_id)
            return
        if run == "DONE":
            try:
                await _tag(client, dataset_id, document_id)
            except RagflowError as e:
                _logger.warning("autotag tag error ds=%s doc=%s: %s", dataset_id, document_id, e.message)
            return
        if asyncio.get_running_loop().time() > deadline:
            _logger.warning("autotag timeout (%ss) ds=%s doc=%s", POLL_TIMEOUT, dataset_id, document_id)
            return
        await asyncio.sleep(POLL_INTERVAL)
