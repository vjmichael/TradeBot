#!/usr/bin/env bash
# Verify the unchanged D2 and prior R2B trees before a single browser race repair.
set -euo pipefail
fixture="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:?target path required}"
bash "$fixture/../r2b-integrate-02-r2-r1/restore-r2b.sh" "$target"
cd "$target"
test "$(git write-tree)" = 250310b3409dd4c0feffa33eba1fe74d8b4ca54a
test "$(git hash-object dashboard/app.js)" = 6db63247f4c994470a955b6ef9cfbf213767aa19
install -m644 "$fixture/overlay/dashboard/app.js" dashboard/app.js
test "$(git hash-object dashboard/app.js)" = 9142d3780d9bdedde9f21ca332d8e7383ad4223a
git add dashboard/app.js
git diff --exit-code
echo "R2B_R2_REPAIRED_TREE=$(git write-tree)"
