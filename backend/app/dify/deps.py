"""Dify 依赖注入：进程级 DifyClient（lifespan 建立；测试可 override）。"""
from fastapi import Request

from app.dify.client import DifyClient


def get_dify(request: Request) -> DifyClient:
    client = getattr(request.app.state, "dify", None)
    if client is None:
        # ASGITransport 测试路径不跑 lifespan —— 懒建兜底
        client = DifyClient()
        request.app.state.dify = client
    return client
