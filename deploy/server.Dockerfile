# The Weave server — deployable #1 of three (A1).
#
# It serves the API, the MCP surface, and the built UI as static assets. The UI
# is NOT a fourth deployable: it is built here and served from the same process,
# which is one fewer thing to run, version-match and onboard (D-021).
#
# Two stages, because the UI needs bun and the server does not: shipping a
# JavaScript toolchain inside a Python image would be a build dependency living
# in production for no reason.
#
#   docker build -f deploy/server.Dockerfile -t weave-server:latest .

# ── stage 1 · build the UI ───────────────────────────────────────────────────
FROM oven/bun:1 AS ui

WORKDIR /ui
COPY weave-ui/package.json weave-ui/bun.lock* ./
RUN bun install --frozen-lockfile || bun install

COPY weave-ui/ ./
# vite writes into ../weave/server/webui; give it somewhere to land.
RUN mkdir -p /weave/server && bun run build

# ── stage 2 · the server ─────────────────────────────────────────────────────
FROM python:3.12-slim

# git so locator resolution can read a registered project (P2); curl for the
# healthcheck. Nothing else — a smaller surface is a smaller thing to patch.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/weave

# environment.yml is the dependency manifest (D-006), but conda in a container
# buys nothing here: the image IS the environment. The pinned set is installed
# with pip against the same versions, and tests/test_dependency_parity.py
# asserts the two lists agree so they cannot drift.
COPY deploy/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY weave_core ./weave_core
COPY weave ./weave
COPY --from=ui /weave/server/webui ./weave/server/webui
RUN pip install --no-cache-dir -e . --no-deps

# Not root. The server holds the workspace's model credential and the signing
# secret that mints roles; running it as uid 0 adds nothing and costs a boundary.
RUN useradd --create-home --uid 1000 weave \
    && mkdir -p /data && chown -R weave:weave /data /opt/weave
USER weave
ENV HOME=/home/weave

ENV WEAVE_HOST=0.0.0.0 \
    WEAVE_PORT=9800 \
    WEAVE_WORKING_DIR=/data \
    WEAVE_INPUT_DIR=/data/inputs

EXPOSE 9800

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9800/health || exit 1

ENTRYPOINT ["python", "-m", "weave.server.app"]
CMD ["--host", "0.0.0.0", "--port", "9800"]
