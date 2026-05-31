# fast-gateway — local/self-hosted image. Ships the gateway + the `fast-gateway` CLI.
# Build:  docker build -t fast-gateway:local .
# Run:    docker run -p 8000:8000 -v "$PWD/gateway.json:/config/gateway.json:ro" \
#                 -v gateway-data:/data fast-gateway:local
# Only HTTP/SSE upstreams are supported; bridge local stdio servers to HTTP first
# (e.g. `fastmcp run --transport http ...` or `mcp-proxy`).

FROM python:3.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --no-dev --extra cli --frozen


FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    FAST_GATEWAY_URL=http://127.0.0.1:8000

WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY examples/gateway.json /config/gateway.json

VOLUME ["/data"]
EXPOSE 8000

# Bind on all interfaces and keep the registry DB on the /data volume so it survives
# container recreation. Override the config by mounting your own at /config/gateway.json.
CMD ["fast-gateway", "serve", "--config", "/config/gateway.json", "--host", "0.0.0.0", "--db", "/data/gateway.db"]
