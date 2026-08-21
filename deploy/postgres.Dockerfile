# The database the production path needs — PostgreSQL with **both** `age` and
# `vector` (P12, D-046).
#
# **Not a fourth deployable** (A1). Weave ships three things: the server, the
# dev-host daemon, and the dev-agent image. This is what the bundle runs
# *against*, the same way it runs against a Neo4j nobody claims we ship.
#
#   docker build -f deploy/postgres.Dockerfile -t weave-postgres:16 .
#
# ── why this file exists at all ──────────────────────────────────────────────
#
# `PGGraphStorage` needs both extensions in one database, and **no published
# image provides both**:
#
#   pgvector/pgvector:pg16       vector ✓  age ✗   → dies at `create_graph(unknown)`
#   apache/age:release_PG16_…    age ✓     vector ✗ → dies earlier, on connect,
#                                                     "extension vector is not available"
#
# The second failure is the sharper one: `PostgreSQLDB` creates the vector
# extension on connect regardless of which storage is in use, so AGE-alone is not
# a partial configuration — it is no configuration. One image with both, or
# nothing.
#
# ── the direction of the build, and what it costs us ─────────────────────────
#
# AGE is the base and pgvector is compiled on top, because that is the cheaper
# half to build: pgvector is one `make install`, while compiling AGE needs
# `postgresql-server-dev-16`, flex and bison.
#
# **It also decides which half we own.** pgvector publishes refreshed images
# often; the `apache/age` PG16 tag has been static for about eleven months. So
# building on AGE means we track the slower-moving half by hand — when AGE
# publishes a new PG16 tag, the base here has to be moved deliberately. That is
# a real ongoing cost and it was accepted knowingly (D-046), not inherited from
# a fix.

# ── stage 1 · build pgvector against this exact server ───────────────────────
#
# Same base as the final image on purpose: an extension is compiled against a
# specific PostgreSQL major and ABI, so building it anywhere else would produce
# a `.so` that loads on a good day.
FROM apache/age:release_PG16_1.6.0 AS pgvector

# v0.8.5 rather than the newest (v0.8.6), because that is the version
# `pgvector/pgvector:pg16` ships and therefore the version every PostgreSQL test
# in this suite has been passing against — measured, not assumed. The only
# capability this image adds is the graph half; changing the vector half in the
# same step would make a green suite prove less than it appears to.
ARG PGVECTOR_VERSION=v0.8.5

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential git ca-certificates postgresql-server-dev-16; \
    git clone --branch "${PGVECTOR_VERSION}" --depth 1 \
        https://github.com/pgvector/pgvector.git /tmp/pgvector; \
    cd /tmp/pgvector; \
    make clean; \
    make OPTFLAGS=""; \
    make install DESTDIR=/staging; \
    rm -rf /var/lib/apt/lists/*

# ── stage 2 · the database ───────────────────────────────────────────────────
FROM apache/age:release_PG16_1.6.0

# Staged with DESTDIR and copied wholesale rather than naming
# `/usr/lib/postgresql/16/lib/vector.so` and friends: the install prefix comes
# from `pg_config`, and a hard-coded path is a silent miss the day the base
# image moves it — the file would simply not be there and `CREATE EXTENSION
# vector` would fail at runtime with nothing to point at.
COPY --from=pgvector /staging/ /

# **AGE has to be loaded, and the adapter never loads it.**
#
# `weave_core/graph/storage/postgres.py` issues `CREATE EXTENSION AGE CASCADE`
# and `SET search_path = ag_catalog, …`, but no `LOAD 'age'` — so the library
# must already be in the server. `postgresql.conf.sample` is where the official
# entrypoint's `initdb` reads defaults from, which makes this hold however the
# container is started rather than only under our compose file.
RUN set -eux; \
    conf="$(ls -d /usr/share/postgresql/*/postgresql.conf.sample | head -n1)"; \
    echo "shared_preload_libraries = 'age'" >> "$conf"

# Proof the image can do the two things it exists for, at build time.
#
# **A container that starts is not the gate** — `pgvector/pgvector:pg16` starts
# perfectly and cannot run the adapter, which is exactly how this survived seven
# milestone gates. This checks the extension *files* are installed; the real gate
# is a graph round-trip on a running server
# (`tests/test_storage_paths.py::test_the_postgres_graph_path`).
RUN set -eux; \
    ls "$(pg_config --sharedir)/extension/vector.control"; \
    ls "$(pg_config --sharedir)/extension/age.control"
