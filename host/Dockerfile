# The upstream tag is informational. The immutable image and source/lock hashes
# are recorded in upstream.json; no floating FROM or author-specific runtime.
FROM ghcr.io/es617/obsidian-sync-mcp@sha256:d8d71417ed3beea16573bcef581d096fd17903da514b42b2020cf358a98b6ce0
WORKDIR /wiki/host
COPY host/package.json host/package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev --no-audit --no-fund
COPY host/ ./
COPY system/production-reference/ /wiki/system/production-reference/
COPY system/config/confirmed-dictionary.empty.md /wiki/system/config/confirmed-dictionary.empty.md
COPY vault-template/ /wiki/vault-template/
COPY benchmark/fixtures/ /wiki/benchmark/fixtures/
# The runtime is loopback-only. Run with host networking and an authenticated
# reverse proxy on Linux. Config and state are mounted by the operator, not COPY.
USER node
ENTRYPOINT ["node", "/wiki/host/server.mjs"]
