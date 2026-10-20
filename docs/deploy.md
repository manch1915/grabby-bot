# Deploying Grabby

The bot uses long polling, so it needs no domain, TLS or reverse proxy. Everything runs as one compose project: the bot, a local `telegram-bot-api` server and Redis.

## What you need

- A bot token from [@BotFather](https://t.me/BotFather). Use a bot that nothing else polls: two processes on one token fight over updates.
- `api_id` and `api_hash` from <https://my.telegram.org>. The local Bot API server needs them; they are what lifts the upload limit from 50 MB to 2 GB.
- A Linux host with Docker Compose v2. The image is about 850 MB on disk (static ffmpeg and Deno for YouTube make up most of it).

## First deploy

1. On the server, create the directory and the environment file:

   ```bash
   mkdir -p /opt/grabby && cd /opt/grabby
   install -m 600 /dev/null .env
   ```

   Fill `.env` using [`.env.example`](../.env.example) as a template. On a shared server, also set:

   ```ini
   WORK_DIR_LIMIT_MB=4096   # downloads in progress never take more than this
   MIN_FREE_DISK_MB=3072    # new downloads wait while the disk is below this
   MAX_PARALLEL=3           # each download subprocess takes 100-150 MB
   ```

2. From your machine:

   ```bash
   bash deploy/deploy.sh narcisvps /opt/grabby
   ```

   The script builds the image locally, streams it with `compose.yaml` over a single SSH connection, loads it and runs `docker compose up -d --no-build`. Nothing is compiled on the server.

On first start the bot calls `logOut` on api.telegram.org once (Telegram requires this before a bot can move to a local server) and remembers it in Redis.

## Resource limits

`compose.yaml` caps every container:

| Service | CPU | Memory |
|---|---|---|
| bot (with download subprocesses) | 1.5 | 680 MB |
| bot-api | 0.75 | 256 MB |
| redis | 0.25 | 96 MB (64 MB data) |

The whole stack stays around 1 GB of RAM. Swap is capped at the same values, so a download that runs out of memory is killed and reported as a failed link instead of pushing the host into swap. Logs rotate at 3 × 10 MB per container. Videos are never re-encoded except for short clips in codecs Telegram can't play, and that runs on a single thread.

## Cookies and proxies

Instagram stories, private posts and some YouTube videos need a logged-in session. Export cookies in Netscape format (any "cookies.txt" browser extension does it) and put them into the data volume, one file per site:

```bash
docker compose cp instagram.txt bot:/data/cookies/instagram.txt
```

File names match platform names: `instagram.txt`, `tiktok.txt`, `youtube.txt`, `x.txt`, `reddit.txt` and so on. Use a separate account, not your personal one.

For proxies set `PROXY_URLS` (comma separated, rotated per request) and `PROXY_PLATFORMS`.

## Updates of yt-dlp and gallery-dl

Sites change their APIs often, so the bot checks PyPI every `UPDATE_INTERVAL_H` hours (nightly yt-dlp builds by default). A new version is installed into `/data/extractors/<stamp>` and has to download the probe links at least as well as the current one before it is switched on. Admins get a message about every switch or rejected update. `/update` runs the check right away, `/rollback` goes back one version.

## Operations

```bash
cd /opt/grabby
docker compose logs -f bot          # live log
docker compose restart bot          # unfinished batches resume after restart
docker compose exec redis redis-cli info memory
```

Admin commands in the chat (user ids from `ADMIN_IDS`): `/stats`, `/access open|list`, `/allow ID`, `/deny ID`, `/ban ID`, `/unban ID`, `/update`, `/rollback`.
