FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-core \
        fonts-noto-color-emoji \
        libraqm0 \
        libfreetype6 \
        libjpeg62-turbo \
        zlib1g \
        libtiff6 \
        libwebp7 \
        libopenjp2-7 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app \
    && mkdir /data \
    && chown app:app /data

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app

# Copy the project before syncing so uv can install the package itself.
COPY . .

FROM base AS test
RUN uv sync --frozen \
    && chown -R app:app /app
USER 10001:10001
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["-q", "-p", "no:cacheprovider"]

FROM base AS runtime
RUN uv sync --frozen --no-dev \
    && chown -R app:app /app

USER 10001:10001
ENTRYPOINT ["python", "-m", "quoted_bot"]
