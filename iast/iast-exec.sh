#!/bin/sh
# IAST switch (infra-7rog.41): ddtrace is INSTALLED in every image but wraps the web
# server only when DD_IAST_ENABLED=true, which only the QA cluster sets. Anything
# else -- unset, false, TRUE -- execs the command exactly as before.
if [ "${DD_IAST_ENABLED-}" = true ]; then
  exec ddtrace-run "$@"
fi
exec "$@"
