#!/usr/bin/env bash
# remote-build-up.sh — build images ON the production server, then bring the stack up.
# Replaces the local build-ship path when the operator wants the server to build its own
# images: no local docker needed; server pulls sources via the `prod` git remote
# (receive.denyCurrentBranch=updateInstead) and builds with cached base images.
#
# Base images unreachable via Docker Hub direct (blocked) are pulled through
# docker.m.daocloud.io and retagged — no daemon.json change required.
#
# Usage:
#   deploy/scripts/remote-build-up.sh              # push current develop, build, up
# Env overrides:
#   REMOTE_HOST   (default root@192.168.20.226)
#   REMOTE_REPO   (default /root/agent_plantform)   # git checkout on the server
#   MIRROR        (default docker.m.daocloud.io)
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-root@192.168.20.226}"
REMOTE_REPO="${REMOTE_REPO:-/root/agent_plantform}"
MIRROR="${MIRROR:-docker.m.daocloud.io}"
BRANCH="develop"
PROJECT="agent-platform"   # must match the running stack's compose project name
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHA="$(git -C "$REPO_ROOT" rev-parse --short "$BRANCH")"

if ! git -C "$REPO_ROOT" remote get-url prod >/dev/null 2>&1; then
  echo "!! git remote 'prod' not configured. Add: git remote add prod ssh://$REMOTE_HOST$REMOTE_REPO"
  exit 1
fi

echo "==> preflight: $REMOTE_HOST"
ssh "$REMOTE_HOST" "docker --version >/dev/null && docker compose version >/dev/null"
if ! ssh "$REMOTE_HOST" "test -f '$REMOTE_REPO/deploy/.env'"; then
  # migrate the env file from the legacy deploy dir on first server-side build
  ssh "$REMOTE_HOST" "test -f /root/agent-platform/.env && cp /root/agent-platform/.env '$REMOTE_REPO/deploy/.env' || { echo '!! no .env found (neither $REMOTE_REPO/deploy nor legacy dir). Run remote-env.sh first.'; exit 1; }"
fi

echo "==> pushing $BRANCH ($SHA) to prod remote (updateInstead updates the server worktree)"
git push prod "$BRANCH"

echo "==> ensuring base images (pull via $MIRROR if missing)"
ssh "$REMOTE_HOST" "for img in node:20-alpine python:3.12-slim nginx:1.25-alpine; do
  docker image inspect \$img >/dev/null 2>&1 || {
    echo \"  pulling \$img via mirror\"; docker pull $MIRROR/library/\$img && docker tag $MIRROR/library/\$img \$img
  }
done"

echo "==> building on server (GIT_SHA=$SHA)"
ssh "$REMOTE_HOST" "cd '$REMOTE_REPO/deploy' && GIT_SHA='$SHA' docker compose -p '$PROJECT' build"

echo "==> docker compose -p $PROJECT up -d"
ssh "$REMOTE_HOST" "cd '$REMOTE_REPO/deploy' && GIT_SHA='$SHA' docker compose -p '$PROJECT' up -d"

PORTAL_PORT="$(ssh "$REMOTE_HOST" "grep '^PORTAL_PORT=' '$REMOTE_REPO/deploy/.env' | cut -d= -f2")"
PORTAL_PORT="${PORTAL_PORT//[[:space:]]/}"
PORTAL_PORT="${PORTAL_PORT:-8080}"

echo "==> waiting for health endpoint (max 90s)"
READY=0
for i in $(seq 1 45); do
  if ssh "$REMOTE_HOST" "curl -sf 'http://127.0.0.1:${PORTAL_PORT}/api/health' >/dev/null 2>&1"; then
    READY=1; break
  fi
  sleep 2
done
if [ "$READY" != "1" ]; then
  echo "!! stack did not become healthy within 90s. Debug:"
  echo "   ssh $REMOTE_HOST 'cd $REMOTE_REPO/deploy && docker compose -p $PROJECT ps && docker compose -p $PROJECT logs --tail=50'"
  exit 1
fi

HEALTH="$(ssh "$REMOTE_HOST" "curl -sf 'http://127.0.0.1:${PORTAL_PORT}/api/health'")"
echo "==> READY: $HEALTH"
echo "    portal:  http://192.168.20.226:${PORTAL_PORT}"
echo "    built from $BRANCH @ $SHA on the server"
