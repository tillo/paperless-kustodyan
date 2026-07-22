# paperless-ngx + Kustodyan field protection.
#
# A standalone Django add-on app (paperless_kustodyan) is dropped into paperless's
# import root and activated at runtime via:
#   PAPERLESS_APPS=paperless_kustodyan.apps.PaperlessKustodyanConfig
# No paperless source files are modified. The app is stdlib-only (urllib), so no extra
# Python packages are installed.
FROM ghcr.io/paperless-ngx/paperless-ngx:2.20.15

# CACHEBUST_DAY (injected by CI as $(date +%Y%m%d)) invalidates this layer once per day.
# Upstream releases quarterly-ish while Debian patches weekly, so the base image
# accumulates already-fixed CVEs between releases; the daily upgrade closes that gap.
ARG CACHEBUST_DAY=unset
RUN echo "cache day: ${CACHEBUST_DAY}" && \
    apt-get update && apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

# Security patch-bumps for Python packages upstream pins one release behind:
# django 5.2.8 (GHSA-frmv-pr5f-9mcr), nltk 3.9.3 (GHSA-7p94-766c-hgjp). Exact
# pins on purpose — DROP these on the next base-image bump: if upstream already
# ships >= these versions, this line would downgrade them.
RUN python3 -m pip install --no-cache-dir --no-deps django==5.2.8 nltk==3.9.3

# /usr/src/paperless/src is paperless's WORKDIR and on the Python import path, owned by
# uid 1000 (paperless). --chown keeps the runtime user able to read it.
COPY --chown=1000:1000 ./paperless_kustodyan /usr/src/paperless/src/paperless_kustodyan

LABEL org.opencontainers.image.title="paperless-ngx + kustodyan field protection" \
      org.opencontainers.image.description="paperless-ngx with the Kustodyan (RPS) custom-field protection add-on"
