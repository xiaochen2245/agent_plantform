#!/usr/bin/env bash
# Agent Platform · Alembic 迁移封装（在 backend/ 内运行）
# 用法: scripts/migrate.sh <upgrade|revision|downgrade|stamp> [args...]
#   upgrade              → alembic upgrade head
#   revision "msg" [--autogenerate] → 生成新迁移
#   downgrade -1         → 回退一步（示例）
#   stamp head           → 存量库 schema 已一致时打标（见 MIGRATIONS.md）
set -euo pipefail
cd "$(dirname "$0")/.."

cmd="${1:-help}"
shift || true
case "$cmd" in
  upgrade)    exec uv run alembic upgrade "${1:-head}" ;;
  revision)   exec uv run alembic revision "$@" ;;
  downgrade)  exec uv run alembic downgrade "${1:--1}" ;;
  stamp)      exec uv run alembic stamp "${1:-head}" ;;
  current)    exec uv run alembic current ;;
  history)    exec uv run alembic history ;;
  *) echo "usage: $0 <upgrade|revision|downgrade|stamp|current|history> [args...]"; exit 1 ;;
esac
