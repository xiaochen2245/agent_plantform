#!/usr/bin/env bash
# build-ship.sh — build both images locally, ship to .226 via docker save | ssh load.
# The server needs no git/npm/pip network access; images arrive fully built.
#
# Usage:
#   deploy/scripts/build-ship.sh            # tag: latest (+ git sha tag locally)
#   deploy/scripts/build-ship.sh v1.2.0     # explicit tag
# Env overrides:
#   REMOTE_HOST  (default root@192.168.20.226)
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-root@192.168.20.226}"
TAG="${1:-latest}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"

echo "==> repo: $REPO_ROOT (sha $SHA)"
echo "==> remote: $REMOTE_HOST   tag: $TAG"

echo "==> building agent-platform-backend:$TAG"
docker build -t "agent-platform-backend:$TAG" -t "agent-platform-backend:$SHA" "$REPO_ROOT/backend"

echo "==> building agent-platform-frontend:$TAG"
docker build -t "agent-platform-frontend:$TAG" -t "agent-platform-frontend:$SHA" "$REPO_ROOT/frontend"

echo "==> shipping images to $REMOTE_HOST (docker save | ssh docker load)"
docker save "agent-platform-backend:$TAG" "agent-platform-frontend:$TAG" \
  | ssh "$REMOTE_HOST" docker load

echo "==> done. shipped tags: $TAG (local also tagged :$SHA)"
echo "    next: deploy/scripts/remote-up.sh  (TAG=$TAG by default; export TAG to override)"
