"""部门业务：建/改/删 + 物化路径维护。

路径规则（设计 §4.2）：`/1/3/7/` 表示 1→3→7 的祖先链。
改父时若路径前缀变化，**级联更新所有后代的 path**，否则三态授权解析不变
（仅看 dept_id，但物化路径用于列表排序和环检测）。

返回约定：
- list_depts → list[dict]
- get_dept   → dict | None
- create_dept → dict | str（str=错误码：CYCLE 已禁用）
- delete_dept → str | None（None=不存在）
"""
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_authorization import AppAuthorization
from app.models.department import Department
from app.models.user import User

_ROOT = "/"  # 顶级 path 形式 `/<id>/`


def _dept_out(d: Department) -> dict:
    return {"id": d.id, "name": d.name, "parent_id": d.parent_id, "path": d.path}


async def list_depts(session: AsyncSession) -> list[dict]:
    """按 path 排序，便于前端直接拼树或平铺展示。"""
    rows = (
        await session.execute(
            select(Department).order_by(Department.path, Department.id)
        )
    ).scalars().all()
    return [_dept_out(r) for r in rows]


async def get_dept(session: AsyncSession, dept_id: int) -> dict | None:
    d = await session.get(Department, dept_id)
    return _dept_out(d) if d else None


async def create_dept(
    session: AsyncSession, name: str, parent_id: int | None
) -> dict | str:
    """创建部门并维护 path。父不存在 → 'UNKNOWN_PARENT'。"""
    parent: Department | None = None
    if parent_id is not None:
        parent = await session.get(Department, parent_id)
        if parent is None or parent.path is None:
            return "UNKNOWN_PARENT"
    dept = Department(name=name, parent_id=parent_id, path=None)
    session.add(dept)
    await session.flush()  # 取自增 id
    base = parent.path if parent else _ROOT
    dept.path = f"{base}{dept.id}/"
    await session.flush()
    return _dept_out(dept)


async def update_dept(
    session: AsyncSession, dept_id: int, payload: dict
) -> dict | str | None:
    """不存在 → None；改名校名；改父同步刷新自身与后代 path。

    错误码：
    - 'SELF_PARENT'     把自身设为父
    - 'UNKNOWN_PARENT'  父 id 不存在
    - 'CYCLE'           新父是当前节点的后代（防成环）
    """
    dept = await session.get(Department, dept_id)
    if dept is None:
        return None
    old_path = dept.path
    new_path = old_path  # 默认不变

    if "name" in payload and payload["name"] is not None:
        dept.name = payload["name"]

    if "parent_id" in payload:
        new_parent_id = payload["parent_id"]
        if new_parent_id == dept.id:
            return "SELF_PARENT"
        if new_parent_id is not None:
            new_parent = await session.get(Department, new_parent_id)
            if new_parent is None or new_parent.path is None:
                return "UNKNOWN_PARENT"
            # 环检测：新父 path 不能包含当前 id
            if old_path and f"/{dept.id}/" in new_parent.path:
                return "CYCLE"
            new_path = f"{new_parent.path}{dept.id}/"
        else:
            new_path = f"{_ROOT}{dept.id}/"
        dept.parent_id = new_parent_id

    if new_path != old_path and old_path:
        dept.path = new_path
        # 级联刷新所有后代的 path 前缀
        rows = await session.execute(
            select(Department).where(
                Department.path.like(f"{old_path}%"), Department.id != dept.id
            )
        )
        suffix_offset = len(old_path)
        for child in rows.scalars():
            # child.path 形如 /1/old/5/8/，保留 old/ 之后的部分拼到 new_path 之后
            child.path = f"{new_path}{child.path[suffix_offset:]}"

    return _dept_out(dept)


async def delete_dept(session: AsyncSession, dept_id: int) -> str | None:
    """不存在 → None；有子部门 → 'HAS_CHILDREN'；有用户引用 → 'HAS_USERS'。

    通过后清理 dept 类型的 app_authorizations（FK 无 cascade），再删部门行。
    """
    dept = await session.get(Department, dept_id)
    if dept is None:
        return None
    has_children = await session.scalar(
        select(func.count())
        .select_from(Department)
        .where(Department.parent_id == dept_id)
    )
    if has_children:
        return "HAS_CHILDREN"
    has_users = await session.scalar(
        select(func.count()).select_from(User).where(User.dept_id == dept_id)
    )
    if has_users:
        return "HAS_USERS"
    await session.execute(
        delete(AppAuthorization).where(
            AppAuthorization.principal_type == "dept",
            AppAuthorization.principal_id == dept_id,
        )
    )
    await session.delete(dept)
    return "OK"
