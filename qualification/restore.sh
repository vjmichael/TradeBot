#!/usr/bin/env bash
set -euo pipefail
fixture="$(cd "$(dirname "$0")" && pwd)"
target="$1"
git worktree add --detach "$target" 867e58a87fd0a27aeea693739646b36edfa7f113
cd "$target"
test "$(git rev-parse HEAD^{tree})" = 87c5f6da6768efb64858d7889e18d4b2b0ea66cf
for p in 01 02 03 04 05 06 07; do
  git apply --index "$fixture/$p.patch"
done
install -m644 "$fixture/08-task-ledger.md" docs/tasks.md
install -m644 "$fixture/08-security-contract.md" docs/tb-sec-key-01.md
git add docs/tasks.md docs/tb-sec-key-01.md
for p in 09 10; do git apply --index "$fixture/$p.patch"; done
tree="$(git write-tree)"
test "$tree" = a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b
git diff --exit-code
printf 'EXACT_CANDIDATE_TREE=%s\n' "$tree"
