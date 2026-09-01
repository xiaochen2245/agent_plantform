# 数据库迁移（Alembic）

## 架构

- `alembic/versions/` 迁移链；`alembic/env.py` 异步引擎，URL 取自 `DATABASE_URL`（settings / 环境变量）
- **启动引导**（`app/db/init.py`）：文件库优先 `alembic upgrade head`；失败仅告警并回退 `create_all`（全新/异常环境不阻断启动）。测试与内存库直接 `create_all`
- 迁移历史表 `alembic_version`

## 常用命令（`backend/scripts/migrate.sh` 封装）

| 操作 | 命令 |
|---|---|
| 升级到最新 | `scripts/migrate.sh upgrade` |
| 生成迁移（改模型后） | `scripts/migrate.sh revision "描述" --autogenerate` |
| 回退一步 | `scripts/migrate.sh downgrade -1` |
| 查看当前版本 | `scripts/migrate.sh current` |
| 历史 | `scripts/migrate.sh history` |

等价原始命令：`uv run alembic <cmd>`（须在 `backend/` 内）。

## 存量 dev.db 一次性打标（已按 baseline schema 手工同步过的库）

本仓库引入 Alembic 之前的 `dev.db`（含 2026-09-01 手工补的 `apps.inputs_schema` 列）
schema 已与 baseline 一致，**只需打标，不要重复 upgrade**：

```bash
cd backend
uv run alembic stamp head
```

未打标的后果：下次启动 `upgrade head` 尝试在已有表上重建 → 失败 → 告警回退 create_all（不炸，但每次启动有告警）。

## 新环境

`uv run alembic upgrade head`（或直接启动应用，引导自动完成）→ 种子数据由 lifespan 幂等写入。

## 注意

- SQLite 不支持部分 ALTER（如改列类型），此类变更需 batch 模式：`op.batch_alter_table`；Postgres 无此限制
- baseline（`11e12afcf83c`）= 2026-09-01 完整模型现状，无数据回填
