#!/usr/bin/env bash
# Run on the server once TELEGRAM_API_ID and TELEGRAM_API_HASH are filled in .env.
# Moves the bot from api.telegram.org (50 MB) to the bundled local Bot API server (2 GB).
# The bot logs out of the cloud API by itself on its first start against the local server.
set -euo pipefail
cd "$(dirname "$0")"
test -f .env || cd ..

grep -Eq '^TELEGRAM_API_ID=[0-9]+' .env || { echo "TELEGRAM_API_ID is empty in .env" >&2; exit 1; }
grep -Eq '^TELEGRAM_API_HASH=[0-9a-f]{32}' .env || { echo "TELEGRAM_API_HASH is empty in .env" >&2; exit 1; }

set_var() {
  if grep -q "^$1=" .env; then
    # Rewrite in place (same inode) so the 600 permissions stay.
    python3 - "$1" "$2" <<'PY'
import sys
key, value = sys.argv[1], sys.argv[2]
lines = open(".env").read().splitlines()
lines = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
open(".env", "w").write("\n".join(lines) + "\n")
PY
  else
    echo "$1=$2" >> .env
  fi
}

set_var COMPOSE_PROFILES local-api
set_var BOT_API_URL http://bot-api:8081
docker compose up -d
docker compose ps
