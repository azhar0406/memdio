# Stage 1: Build
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY memdio/ memdio/

RUN pip install --no-cache-dir ".[search]"

# Stage 2: Runtime
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/memdio /usr/local/bin/memdio
COPY memdio/ memdio/
COPY pyproject.toml .

ENV MEMDIO_HOME=/data/memdio
RUN useradd --create-home --shell /usr/sbin/nologin memdio \
    && mkdir -p /data/memdio \
    && chown -R memdio:memdio /data/memdio /app

USER memdio
VOLUME /data/memdio

ENTRYPOINT ["memdio"]
CMD ["--help"]
