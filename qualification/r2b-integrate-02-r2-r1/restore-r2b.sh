#!/usr/bin/env bash
# Exact-source R2B overlay, isolated from frozen D2 qualification candidate.
set -euo pipefail
fixture="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:?provide target worktree directory}"
bash "$fixture/../restore.sh" "$target"
cd "$target"

# Refuse any source drift: D2 restore already verifies exact candidate tree.
declare -A before=(
  [tradebot.py]=8d1dfa3d73a7c69e3c3ba7da3ce57281f1ebab40
  [dashboard/app.js]=3b9058c2909b0e6d7ba48e9e5d5c1525cf32f310
  [dashboard/style.css]=3936ad8da8ba5540e911aa828d2540d150f8a86d
  [paper_credential_vault.py]=00576906d18b12c2b86f19d11184e01e5a7b349e
  [deploy/maj-vaj-paper-vault.service]=2f06f37633212e5ee849375cfe4450ad5f10ebef
)
for path in "${!before[@]}"; do
  test "$(git hash-object "$path")" = "${before[$path]}"
done

declare -A after=(
  [tradebot.py]=118b7ab3e0fc0ed8b1ddd3d67cbecd4077c058c5
  [dashboard/app.js]=6db63247f4c994470a955b6ef9cfbf213767aa19
  [dashboard/style.css]=61a479a341ef36e8678d9d2f50a675ffe2566fd3
  [paper_credential_vault.py]=a9e1fa48e5992494e1c5557bb9693ac2635bf28e
  [deploy/maj-vaj-paper-vault.service]=dfb14e8b16ab6ba5236910c8233e91851f69d5d0
  [vault_owner_hooks.py]=fa4651d30732d6d0e5ed9d4c2b920c7a49d2762a
  [managed_paper_vault.py]=854fec45140801e85f12242894a572d1ad77ea22
  [managed_rpc.py]=5f9b890b0fcc9af104f84b33aacb9f754c574a06
  [health_backend.py]=19d6305c10feed5692f717df57786cb66a947d0f
  [health_cache.py]=5882a23b3275031a40623d3b219fa65f96817719
  [health_endpoint.py]=04c1b6c5c1e67ff38973e826816229d8e04c5598
  [offline_health.py]=e66ecc1bcdcbf5fbe5dc47e6e73acdc9f3141555
)
for path in "${!after[@]}"; do
  test -f "$fixture/overlay/$path"
  install -m644 "$fixture/overlay/$path" "$path"
  test "$(git hash-object "$path")" = "${after[$path]}"
  git add -- "$path"
done
git diff --exit-code
echo "D2_SOURCE_TREE=a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b"
echo "R2B_OVERLAY_TREE=$(git write-tree)"
