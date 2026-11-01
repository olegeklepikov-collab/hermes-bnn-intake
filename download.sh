#!/bin/bash
set -euo pipefail
umask 077
bnn_destination="${1:?Usage: bash download.sh /download/folder}"
bnn_code_repository="${HERMES_BNN_REPOSITORY:-olegeklepikov-collab/hermes-bnn-full}"
bnn_script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$bnn_destination"
if [[ ! -f "$bnn_script_root/download-base.sh" ]]; then
 echo 'Download download-base.sh from the same correction release or repository first.' >&2; exit 2
fi
# The base helper remains pinned to the exact previous complete release.
HERMES_BNN_REPOSITORY="olegeklepikov-collab/hermes-bnn-full" bash "$bnn_script_root/download-base.sh" "$bnn_destination"
cd "$bnn_destination"
bnn_fix='Hermes-BNN-context-refresh-20261101.zip'
if [[ ! -f "$bnn_fix" ]]; then
 if [[ "$bnn_code_repository" == *-private ]]; then
  gh release download 'context-refresh-20261101-r1' --repo "$bnn_code_repository" --pattern "$bnn_fix" --dir .
 else
  curl --http1.1 --fail --location --retry 2 --proto '=https' --tlsv1.2 --output "$bnn_fix.downloading" "https://github.com/$bnn_code_repository/releases/download/context-refresh-20261101-r1/$bnn_fix"
  printf '%s  %s\n' 'bc36f1efd1781151e708c8b24e4b4d19ac1a172ea316987d6cb66dbd44b78404' "$bnn_fix.downloading" | shasum -a 256 -c -
  mv "$bnn_fix.downloading" "$bnn_fix"
 fi
fi
printf '%s  %s\n' 'bc36f1efd1781151e708c8b24e4b4d19ac1a172ea316987d6cb66dbd44b78404' "$bnn_fix" | shasum -a 256 -c -
echo 'Base and correction verified. Extract both; run apply_update.py prepare-kit before install.sh. See INSTALL-UPDATE-RU.md.'
