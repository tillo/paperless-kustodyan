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
# go1.24.6), so build from source with golang:1.27 and raise its two deps — the go.mod
# pins x/sys v0.1.0, which carries GO-2026-5024. The raised versions are `# renovate:`
# pins, not @latest: @latest was resolved once and then frozen by the layer cache, so
# it neither moved nor said which version the image held (infra-ik4p.28).
FROM golang:1.27 AS gosu
# renovate: datasource=go depName=golang.org/x/sys
ARG X_SYS_VERSION=v0.50.0
# renovate: datasource=go depName=github.com/moby/sys/user
ARG MOBY_SYS_USER_VERSION=v0.5.0
WORKDIR /build
RUN go mod init gosu-build && \
    go get github.com/tianon/gosu@1.19 && \
    go get golang.org/x/sys@${X_SYS_VERSION} github.com/moby/sys/user@${MOBY_SYS_USER_VERSION} && \
    CGO_ENABLED=0 go build -trimpath -o /usr/local/bin/gosu github.com/tianon/gosu

FROM ghcr.io/paperless-ngx/paperless-ngx:3.5.1

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

# Patch floors over 3.4.2's own pins (infra-ik4p.26, main scan job 180232): nltk
# 3.10.3 -> 3.10.4 (GHSA-8mgp-746c-j5xp, GHSA-j456-xh4h-cpf2) and oauthlib 4.0.0 ->
# 4.0.1 (GHSA-3jp2-3mrg-2vxc), both same-minor patch releases. The assert fails the
# build if the install did not take. These are exact pins: on every base bump check
# what upstream ships and drop a floor it already meets, or it would downgrade.
RUN python3 -m pip install --no-cache-dir --no-deps nltk==3.10.4 oauthlib==4.0.1 && \
    python3 -c "import django, nltk, oauthlib; \
v = {'django': django.get_version(), 'nltk': nltk.__version__, 'oauthlib': oauthlib.__version__}; \
print('floors:', v); \
assert v == {'django': '5.2.17', 'nltk': '3.10.4', 'oauthlib': '4.0.1'}, v"

# /usr/src/paperless/src is paperless's WORKDIR and on the Python import path, owned by
# uid 1000 (paperless). --chown keeps the runtime user able to read it.
COPY --chown=1000:1000 ./paperless_kustodyan /usr/src/paperless/src/paperless_kustodyan

# IAST tracer (infra-7rog.41): ddtrace (Apache-2.0/BSD-3) is installed in every image but
# wraps the web server ONLY when DD_IAST_ENABLED=true (the QA cluster) -- iast/iast-exec.sh.
# The upstream s6 run script execs granian in two branches (root / non-root); both are
# routed through the switch and the build FAILS if either is missed or the switch
# misbehaves (iast-exec.test.sh). ddtrace IAST is proven on this image's CPython 3.14
# with Django (QA canary, 2026-11-24); NOT on 3.15 (dd-trace-py#17843).
# renovate: datasource=pypi depName=ddtrace
ARG DDTRACE_VERSION=4.17.2
COPY iast/ /tmp/iast/
RUN python3 -m pip install --no-cache-dir "ddtrace==${DDTRACE_VERSION}" && \
    install -m 0755 /tmp/iast/iast-exec.sh /usr/local/bin/iast-exec && \
    run=/etc/s6-overlay/s6-rc.d/svc-webserver/run && \
    test "$(grep -c 'exec .*granian ' "$run")" = 2 && \
    sed -i -e 's|exec granian |exec iast-exec granian |' \
           -e 's|exec s6-setuidgid paperless granian |exec s6-setuidgid paperless iast-exec granian |' "$run" && \
    test "$(grep -c 'iast-exec granian ' "$run")" = 2 && \
    IAST_EXEC=/usr/local/bin/iast-exec sh /tmp/iast/iast-exec.test.sh && \
    rm -rf /tmp/iast

LABEL org.opencontainers.image.title="paperless-ngx + kustodyan field protection" \
      org.opencontainers.image.description="paperless-ngx with the Kustodyan (RPS) custom-field protection add-on"
