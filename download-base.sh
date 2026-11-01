#!/bin/bash
set -euo pipefail
umask 077
bnn_destination="${1:?Usage: bash download.sh /new/download/folder}"
bnn_repository="${HERMES_BNN_REPOSITORY:-olegeklepikov-collab/hermes-bnn-full}"
bnn_tag='full-20261031-intake-0.14.3'
mkdir -p "$bnn_destination"
cd "$bnn_destination"
fetch() {
 local bnn_file="$1" bnn_sha="$2"
 if [[ -e "$bnn_file" ]]; then
  printf '%s  %s\n' "$bnn_sha" "$bnn_file" | shasum -a 256 -c -
  return
 fi
 if [[ "$bnn_repository" == *-private ]]; then
  command -v gh >/dev/null || { echo 'Private repository: authenticate gh, or download all release assets in your browser.' >&2; exit 3; }
  gh release download "$bnn_tag" --repo "$bnn_repository" --pattern "$bnn_file" --dir .
 else
  [[ ! -e "$bnn_file.downloading" ]] || { echo 'Partial download preserved; inspect it before retrying.' >&2; exit 4; }
  curl --fail --location --retry 2 --proto '=https' --tlsv1.2 --output "$bnn_file.downloading" "https://github.com/$bnn_repository/releases/download/$bnn_tag/$bnn_file"
  printf '%s  %s\n' "$bnn_sha" "$bnn_file.downloading" | shasum -a 256 -c -
  mv "$bnn_file.downloading" "$bnn_file"
 fi
 printf '%s  %s\n' "$bnn_sha" "$bnn_file" | shasum -a 256 -c -
}
fetch 'RELEASE.json' 'e59d09d374be1a2103d02c718bf0ae1f91a8fee9e8ccf33fc9d26bc09cc367d9'
fetch 'SHA256SUMS' 'b0c7381d95a092dd519eef8582ef8b84237c4e6d0486de02fa958e5f6855284e'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part001' '476d0dd88837851a6fd37d3d45466c0a9d7f4ce985413908cd0537a619824546'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part002' 'fc812ef23c8356944d9375da1c9497a5bbf49e56d9ab85ae5f21d25f5b6c4a29'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part003' '4b452198c426262d7a7047aa77216880373599f00f04116b637e69db836927f2'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part004' 'e9618ccf6ee52bac23650817a0433f9fd33ab330c495a362f8acd00875c3b47b'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part005' '50b5b23767ecd0e01aed7e209111bda6e818a51c00f3c0ad4b88f1fd8ba420f6'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part006' '7c08198c45fdd4b0609ee3e37a2da52a8dabe1430131b94bc3f6b879b7498823'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part007' '7c144acc2eacb703cc6f2620d5124e43861ea061077400a894590209b8dfb5e2'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part008' 'bf88dbb17f3ebf1c404baf9a227334234f416267b0825cecb58291342c6b53f4'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part009' 'f8230637a1d682141e360fadfd61b37f6864274a3798def4aadb83ef3a32c300'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part010' '7a6a5e3c6ba7197d0e5f1ebb03db16d5433f3423c967e94eb3f5cc8e3e150a24'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part011' '30025c83486442db534da64d991e998eee1467b66e9226b7ebbac8faaa91c078'
fetch 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part012' 'a44d744c55f918c3d8f025c20a7c04265e13f4d67a9754717d4d13498de35914'
if [[ ! -e 'Hermes-BNN-Full.tar.gz' ]]; then
 [[ ! -e 'Hermes-BNN-Full.tar.gz.assembling' ]] || { echo "Incomplete assembly preserved" >&2; exit 5; }
 cat 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part001' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part002' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part003' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part004' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part005' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part006' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part007' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part008' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part009' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part010' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part011' 'Hermes-BNN-Full-20261031-Intake-0.14.3.tar.gz.part012' > 'Hermes-BNN-Full.tar.gz.assembling'
 printf '%s  %s\n' 'fe9cef77e74d47aaa6aae39974d716d0c7318b129cde162ec6cf933d4bab0189' 'Hermes-BNN-Full.tar.gz.assembling' | shasum -a 256 -c -
 mv 'Hermes-BNN-Full.tar.gz.assembling' 'Hermes-BNN-Full.tar.gz'
fi
shasum -a 256 -c SHA256SUMS
echo "Full archive verified. Read docs/INSTALL_MACOS.md before installation."
