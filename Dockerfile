# syntax=docker/dockerfile:1.7
FROM mwader/static-ffmpeg:8.0 AS ffmpeg

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/

WORKDIR /srv
COPY pyproject.toml uv.lock ./
# Dependencies go into the system interpreter so the runtime updater can use plain pip.
RUN --mount=from=ghcr.io/astral-sh/uv:0.9.6,source=/uv,target=/bin/uv \
    uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY src ./src
RUN --mount=from=ghcr.io/astral-sh/uv:0.9.6,source=/uv,target=/bin/uv \
    uv pip install --system --no-cache --no-deps .

RUN useradd --create-home --uid 1000 grabby \
    && mkdir -p /work /data \
    && chown grabby:grabby /work /data
USER grabby

CMD ["python", "-m", "grabby"]
