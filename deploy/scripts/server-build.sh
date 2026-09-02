#!/usr/bin/env bash
# server-build.sh — 服务器侧构建部署（新管线，替代本机 build-ship）
#
# 流程：本地 git push (SSH) → 服务器 git checkout develop → compose build → 运行目录 up
# 前提（已就绪）：服务器 /root/agent_plantform 为仓库克隆；/root/agent-platform 为运行
# 目录（prebuilt compose + .env）；daemon.json 已配 docker.m.daocloud.io 镜像源 +
# live-restore。服务器 GitHub 直连不可达，代码经本地 SSH 通道推送。
#
# Usage: deploy/scripts/server-build.sh          # 部署 origin/develop 当前 HEAD
#        REF=<sha|tag> deploy/scripts/server-build.sh
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-root@192.168.20.226}"
SRC_DIR="${SRC_DIR:-/root/agent_plantform}"     # 服务器源码/构建目录
RUN_DIR="${RUN_DIR:-/root/agent-platform}"      # 服务器运行目录（.env + prebuilt compose）
REF="${REF:-develop}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> push ${REF} → ${REMOTE_HOST}:${SRC_DIR}"
git -C "$REPO_ROOT" remote | grep -qx prod || git -C "$REPO_ROOT" remote add prod "ssh://${REMOTE_HOST}${SRC_DIR}"
git -C "$REPO_ROOT" push prod "${REF}:develop"

echo "==> build on server"
ssh "$REMOTE_HOST" "cd '$SRC_DIR' && git checkout -q develop && git reset -q --hard refs/heads/develop && \
  docker compose -f deploy/docker-compose.yml build"

echo "==> up (run dir)"
ssh "$REMOTE_HOST" "cd '$RUN_DIR' && docker compose up -d"

PORTAL_PORT="$(ssh "$REMOTE_HOST" "grep '^PORTAL_PORT=' '$RUN_DIR/.env' | cut -d= -f2" | tr -d '[:space:]')"
PORTAL_PORT="${PORTAL_PORT:-8180}"
echo "==> waiting for health (max 90s)"
for i in $(seq 1 45); do
  if ssh "$REMOTE_HOST" "curl -sf 'http://127.0.0.1:${PORTAL_PORT}/api/health' >/dev/null 2>&1"; then
    echo "==> READY: portal http://192.168.20.226:${PORTAL_PORT}"
    exit 0
  fi
  sleep 2
done
echo "!! health check timeout"; exit 1
