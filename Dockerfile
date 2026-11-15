# magic-db: one container, one volume. See compose.yaml for how to run it.

FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# The development image (":edge") is built with the commit it is made from, and the
# Administration page then says so. A release is built without.
ARG REVISION=""
RUN if [ -n "$REVISION" ]; then printf '%s\n' "$REVISION" > src/magicdb/REVISION; fi
# The exact versions of uv.lock (checked against their hashes), then the app itself, so
# that an image built today and one built next month hold the same libraries.
RUN pip install --no-cache-dir uv==0.11.32 \
    && uv export --locked --no-dev --no-emit-project --format requirements-txt -o /tmp/requirements.txt \
    && pip install --no-cache-dir --prefix=/install --require-hashes -r /tmp/requirements.txt \
    && pip install --no-cache-dir --prefix=/install --no-deps .

FROM python:3.12-slim
LABEL org.opencontainers.image.title="magic-db" \
      org.opencontainers.image.description="Self-hosted Magic: The Gathering card browser and collection tracker" \
      org.opencontainers.image.licenses="MIT"

# Everything the app writes (catalog, collection, images, backups) lives in /data.
ENV MAGICDB_DATA_DIR=/data \
    MAGICDB_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin magicdb \
    && mkdir /data \
    && chown magicdb:magicdb /data
COPY --from=build /install /usr/local

USER magicdb
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('MAGICDB_PORT', '8080') + '/healthz', timeout=3)"]

CMD ["magicdb"]
