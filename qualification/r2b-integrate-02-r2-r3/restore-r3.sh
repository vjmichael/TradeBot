#!/usr/bin/env bash
# Source-locked native relay candidate. Never mutate original frozen D2.
set -euo pipefail
fixture="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:?target directory required}"
bash "$fixture/../r2b-integrate-02-r2-r2/restore-r2.sh" "$target"
cd "$target"
test "$(git write-tree)" = 12a3c46401d96d616de4f88fa8a42588275d22ae
test "$(git hash-object credential_handoff.py)" = de576e164683465ebdcb9847825d91baf1cf8454
install -m644 "$fixture/overlay/credential_handoff.py" credential_handoff.py
test "$(git hash-object credential_handoff.py)" = 269bdc0b19d8a189496c7ca3b9369c158f85b0f6
git add -- credential_handoff.py
git diff --exit-code
echo "R2B_R3_SESSION_RELAY_TREE=$(git write-tree)"
