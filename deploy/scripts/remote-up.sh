#!/usr/bin/env bash
# remote-up.sh — sync compose + nginx conf to .226 and bring the stack up.
# Never touches secrets: .env must already exist server-side (remote-env.sh).
#
# Usage:
#   deploy/scripts/remote-up.sh              # TAG=latest
#   TAG=<sha|version> deploy/scripts/remote-up.sh
# Env overrides:
#   REMOTE_HOST  (default root@192.168.20.226)
#   REMOTE_DIR   (default /root/agent-platform)
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-root@192.168.20.226}"
REMOTE_DIR="${REMOTE_DIR:-/root/agent-platform}"
TAG="${TAG:-latest}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> preflight: checking $REMOTE_HOST"
ssh "$REMOTE_HOST" "docker --version >/dev/null && mkdir -p '$REMOTE_DIR/nginx/conf.d'"
if ! ssh "$REMOTE_HOST" "test -f '$REMOTE_DIR/.env'"; then
  echo "!! $REMOTE_DIR/.env missing on server. Run deploy/scripts/remote-env.sh first."
  exit 1
fi

PORTAL_PORT="$(ssh "$REMOTE_HOST" "grep '^PORTAL_PORT=' '$REMOTE_DIR/.env' | cut -d= -f2")"
PORTAL_PORT="${PORTAL_PORT//[[:space:]]/}"
PORTAL_PORT="${PORTAL_PORT:-8080}"

echo "==> syncing compose + nginx conf"
scp -q "$REPO_ROOT/deploy/docker-compose.prebuilt.yml" "$REMOTE_HOST:$REMOTE_DIR/docker-compose.yml"
scp -q "$REPO_ROOT/deploy/nginx/conf.d/portal.conf" "$REMOTE_HOST:$REMOTE_DIR/nginx/conf.d/portal.conf"

echo "==> docker compose up -d (TAG=$TAG)"
ssh "$REMOTE_HOST" "cd '$REMOTE_DIR' && TAG='$TAG' docker compose up -d"

echo "==> waiting for health endpoint (max 90s)"
READY=0
for i in $(seq 1 45); do
  if ssh "$REMOTE_HOST" "curl -sf 'http://127.0.0.1:${PORTAL_PORT}/api/health' >/dev/null 2>&1"; then
    READY=1
    break
  fi
  sleep 2
done

if [ "$READY" != "1" ]; then
  echo "!! stack did not become healthy within 90s. Debug:"
  echo "   ssh $REMOTE_HOST 'cd $REMOTE_DIR && docker compose ps && docker compose logs --tail=50'"
  exit 1
fi

HEALTH="$(ssh "$REMOTE_HOST" "curl -sf 'http://127.0.0.1:${PORTAL_PORT}/api/health'")"
echo "==> READY: $HEALTH"
echo "    portal:  http://192.168.20.226:${PORTAL_PORT}  (Dify untouched on :80/:3000)"
echo "    login:   admin@company.com / admin123  (CHANGE after first login)"
