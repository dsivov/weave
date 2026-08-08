# The developer container — one autonomous developer, one task at a time.
#
# It holds a Claude Code CLI, a git worktree bind-mounted from the host, and the
# Weave worker loop. Nothing else: no git credentials (the host clones and mounts,
# so a container that runs an agent with full write permission cannot push
# anywhere), and no API keys (the seat arrives as CLAUDE_CODE_OAUTH_TOKEN and
# every metered-auth variable is scrubbed on the way in).
#
# Build:  docker build -f deploy/dev-agent.Dockerfile -t weave-dev-agent:latest .
# Run:    the dev-host daemon runs it; `docker run` by hand is for debugging.

FROM python:3.12-slim

# git for the worktree, node for the Claude Code CLI, ripgrep because the agent
# reaches for it constantly and its absence makes every search slower.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

WORKDIR /opt/weave
COPY pyproject.toml README.md ./
COPY weave_core ./weave_core
COPY weave ./weave
RUN pip install --no-cache-dir -e . --no-deps \
    && pip install --no-cache-dir pytest

# The agent runs as a non-root user: a container is a boundary, and running the
# thing that writes unattended code as root inside it weakens that for nothing.
RUN useradd --create-home --uid 1000 dev && mkdir -p /work && chown dev:dev /work
USER dev
ENV HOME=/home/dev
WORKDIR /work

# The daemon appends --server/--workspace/--worker-id and the rest; this default
# only documents the shape.
ENTRYPOINT ["python", "-m", "weave.team.worker"]
CMD ["--help"]
