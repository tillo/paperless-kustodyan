# paperless-ngx + Kustodyan field protection.
#
# A standalone Django add-on app (paperless_kustodyan) is dropped into paperless's
# import root and activated at runtime via:
#   PAPERLESS_APPS=paperless_kustodyan.apps.PaperlessKustodyanConfig
# No paperless source files are modified. The app is stdlib-only (urllib), so no extra
# Python packages are installed.
# Rebuild gosu with a current Go toolchain to clear the ~25 unreachable Go-stdlib CVEs
# the upstream image ships in gosu (1.17 built with EOL go1.24.4; net/tls/mail/HTTP/url
# stdlib paths gosu never exercises). gosu's own release binaries lag too (1.19 ships
# go1.24.6), so build from source with golang:1.27 and bump its two deps to latest —
# the go.mod pins x/sys v0.1.0, which carries GO-2026-5024.
FROM golang:1.27 AS gosu
WORKDIR /build
RUN go mod init gosu-build && \
    go get github.com/tianon/gosu@1.19 && \
    go get golang.org/x/sys@latest github.com/moby/sys/user@latest && \
    CGO_ENABLED=0 go build -trimpath -o /usr/local/bin/gosu github.com/tianon/gosu

FROM ghcr.io/paperless-ngx/paperless-ngx:3.2.1

# Replace the stale gosu binary with the freshly built one.
COPY --from=gosu /usr/local/bin/gosu /usr/sbin/gosu

# CACHEBUST_DAY (injected by CI as $(date +%Y%m%d)) invalidates this layer once per day.
# Upstream releases quarterly-ish while Debian patches weekly, so the base image
# accumulates already-fixed CVEs between releases; the daily upgrade closes that gap.
ARG CACHEBUST_DAY=unset
RUN echo "cache day: ${CACHEBUST_DAY}" && \
    apt-get update && apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

# Security patch-bump for the one package 3.2.1 ships behind our pin: django 5.2.17
# (GHSA-mwm9-4648-f68q SQLi, GHSA-gvg8-93h5-g6qq SQLi, GHSA-8p8v-wh79-9r56 DoS,
# GHSA-933h-hp56-hf7m DoS). 3.2.1 already ships nltk 3.10.3 and urllib3 2.7.0 (>= the
# former pins below), so those pins are dropped — re-pinning them would downgrade.
# Re-check these against the base image on every bump.
RUN python3 -m pip install --no-cache-dir --no-deps django==5.2.17

# /usr/src/paperless/src is paperless's WORKDIR and on the Python import path, owned by
# uid 1000 (paperless). --chown keeps the runtime user able to read it.
COPY --chown=1000:1000 ./paperless_kustodyan /usr/src/paperless/src/paperless_kustodyan

LABEL org.opencontainers.image.title="paperless-ngx + kustodyan field protection" \
      org.opencontainers.image.description="paperless-ngx with the Kustodyan (RPS) custom-field protection add-on"
