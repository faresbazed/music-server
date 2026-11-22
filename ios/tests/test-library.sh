#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
if [[ "$(uname -s)" == Darwin ]]; then
    clang -fobjc-arc -framework Foundation -Isrc tests/library.m src/Library.m -o "$work/test"
else
    read -r -a flags <<< "$(gnustep-config --objc-flags)"
    read -r -a libs <<< "$(gnustep-config --base-libs)"
    clang "${flags[@]}" -I"$(gcc -print-file-name=include)" -Isrc \
        tests/library.m src/Library.m "${libs[@]}" -o "$work/test"
fi
"$work/test" "$work"
