# The dev-host daemon — deployable #2 of three (A1).
#
# Runs on each machine that carries developer containers. It registers with the
# server, heartbeats, and reconciles the number of running containers to the
# number the team asked for. It never listens on a port: every connection it is
# part of, it opened (A15).
#
# Build:  docker build -f deploy/devhost.Dockerfile -t weave-devhost:latest .
# Run:    docker compose -f deploy/compose.devhost.yml up -d
#
# THIS IMAGE IS THIN ON PURPOSE (R75). It installs
# `deploy/requirements.devhost.txt`, not the server's set: no database driver, no
# model SDK, no HTTP framework. `tests/test_dependency_parity.py` imports the
# daemon with each of those poisoned and fails if the import reaches for one, so
# the thinness is a checked property rather than an intention that decays.

FROM python:3.12-slim

# `git` for the shared clone and per-worker worktrees the daemon prepares on the
# host side, and the Docker CLI to start sibling containers through the mounted
# socket. No compiler: nothing here builds a native wheel, and if that changes it
# should be a visible decision rather than a silently fatter image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/weave
COPY deploy/requirements.devhost.txt ./
RUN pip install --no-cache-dir -r requirements.devhost.txt

COPY pyproject.toml README.md ./
COPY weave_core ./weave_core
COPY weave ./weave
# `--no-deps`: the requirements above are the whole dependency set for this
# machine, and letting pip resolve the package's own would quietly reintroduce
# what this image exists to leave out.
RUN pip install --no-cache-dir -e . --no-deps

# The daemon prepares worktrees and talks to the Docker socket, so it runs as
# root here — unlike the dev-agent image, which drops to an unprivileged user
# because a container running unattended agent code is a boundary worth keeping.
# The socket mount is the privileged thing on this machine; adding a user that
# can use it would be ceremony, not isolation.

ENTRYPOINT ["python", "-m", "weave.devhost"]
CMD ["--help"]
