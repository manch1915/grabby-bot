#!/usr/bin/env bash
# Build the image here, ship it with compose.yaml over a single SSH connection, restart.
# The server keeps its own .env (chmod 600) in the target directory.
#
#   bash deploy/deploy.sh [ssh-host] [remote-dir]
set -euo pipefail

HOST="${1:-narcisvps}"
DIR="${2:-/opt/grabby}"
cd "$(dirname "$0")/.."

docker build -t grabby-bot:latest .

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
docker save grabby-bot:latest | gzip -1 > "$stage/image.tar.gz"
cp compose.yaml deploy/enable-local-api.sh "$stage/"

# fail2ban on the server bans bursts of SSH connections, so everything goes through one.
tar -C "$stage" -cf - . | ssh "$HOST" "set -e
  mkdir -p '$DIR' && cd '$DIR'
  tar -xf -
  test -f .env || { echo 'missing $DIR/.env, see docs/deploy.md' >&2; exit 1; }
  gunzip -c image.tar.gz | docker load && rm image.tar.gz
  docker compose up -d --no-build --remove-orphans
  docker image prune -f >/dev/null
  docker compose ps"
