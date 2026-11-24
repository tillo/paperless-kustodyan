#!/bin/sh
# Behaviour of the IAST switch (infra-7rog.41): `iast-exec CMD...` runs CMD under
# ddtrace-run only when DD_IAST_ENABLED is exactly "true"; otherwise exactly CMD.
# Run by the Dockerfile at build (a failing check fails the image), and locally.
set -eu
here="$(dirname "$0")"
bin="$(mktemp -d)"; trap 'rm -rf "$bin"' EXIT
# a stand-in ddtrace-run that marks what it wrapped
printf '#!/bin/sh\necho "TRACED $*"\n' > "$bin/ddtrace-run"; chmod +x "$bin/ddtrace-run"
n=0
check() { [ "$2" = "$3" ] || { echo "FAIL $1: expected '$2', got '$3'"; exit 1; }; n=$((n + 1)); }
run() { PATH="$bin:$PATH" sh "${IAST_EXEC:-$here/iast-exec.sh}" "$@"; }
check "off by default" "a b" "$(unset DD_IAST_ENABLED; run echo a b)"
check "off when false" "x" "$(DD_IAST_ENABLED=false run printf %s x)"
check "off when TRUE (exact match only)" "x" "$(DD_IAST_ENABLED=TRUE run printf %s x)"
check "on wraps in ddtrace-run" "TRACED granian --x" "$(DD_IAST_ENABLED=true run granian --x)"
check "argv verbatim" "a|b c|" "$(run printf '%s|' a 'b c')"
echo "iast-exec: $n checks passed"
