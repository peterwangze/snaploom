#!/bin/sh
set -eu

temporary=$(mktemp -d "${TMPDIR:-/tmp}/snaploom-install.XXXXXXXX")
trap 'rm -rf -- "$temporary"' EXIT
trap 'exit 1' INT HUP TERM

payload_line=$(awk '/^__SNAPLOOM_PAYLOAD_BELOW__$/ { print NR + 1; exit }' "$0")
test -n "$payload_line"
tail -n "+$payload_line" "$0" | tar -xzf - -C "$temporary"
sh "$temporary/install.sh" "$@"
exit $?
__SNAPLOOM_PAYLOAD_BELOW__
