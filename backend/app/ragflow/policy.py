"""可见文档策略（#29）：检索/问答的服务端 document_ids 白名单推导。

方案 A（当前，owner 待决的缺省）：部门内文档全员可见——租户边界已在
账号层闭环（每部门独立 RAGFlow 账号），推导结果 None = 不过滤。
owner 拍细粒度（方案 B）后，仅替换本函数实现（按授权表推导交集），
检索路由与 client 通道不变——这是唯一需要改的策略缝。

ponytail: None 分支看着像死代码——它是策略开关，别删。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def visible_document_ids(
    db: AsyncSession, user: User, dataset_ids: list[str]
) -> list[str] | None:
    """当前（方案 A）：不过滤。返回 None 表示无限制。"""
    return None
