#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
root="$PWD"
sdk="${IOS_SDK_DIR:-$HOME/theos-sdks/iPhoneOS9.3.sdk}"
bin="${IOS_TOOLCHAIN_BIN:-$HOME/ios-toolchain/linux/iphone/bin}"
signer="${LDID_BIN:-$HOME/ios-tools/ldid}"
[[ -x "$bin/clang" && -x "$bin/lipo" && -x "$signer" && -d "$sdk" ]] || {
    echo "Set IOS_SDK_DIR, IOS_TOOLCHAIN_BIN and LDID_BIN to an installed iOS 9 device SDK, toolchain and ldid." >&2; exit 1;
}
mkdir -p build
stage="$(mktemp -d "$root/build/stage.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
app="$stage/Payload/OfflineMusic.app"
mkdir -p "$app"
cp Resources/Info.plist "$app/"

# Some Theos 9.3 SDK checkouts omit a re-exported linker stub; leave the input SDK untouched.
if [[ ! -e "$sdk/usr/lib/system/liblaunch.tbd" && ! -e "$sdk/usr/lib/system/liblaunch.dylib" ]]; then
    cp -R "$sdk" "$stage/iPhoneOS9.3.sdk"
    sdk="$stage/iPhoneOS9.3.sdk"
    cat >"$sdk/usr/lib/system/liblaunch.tbd" <<'STUB'
---
archs: [ armv7, armv7s, arm64 ]
platform: ios
install-name: /usr/lib/system/liblaunch.dylib
exports:
  - archs: [ armv7, armv7s, arm64 ]
    symbols: [ __spawn_via_launchd ]
...
STUB
fi
for arch in armv7 arm64; do
    "$bin/clang" -target "$arch-apple-ios9.0" -isysroot "$sdk" -fobjc-arc -Os \
        -Wall -Wextra -Wno-unused-parameter -Werror=unguarded-availability -Werror=unguarded-availability-new \
        src/*.m -framework UIKit -framework Foundation -framework CFNetwork -framework Security -framework AVFoundation \
        -o "$stage/$arch"
done
"$bin/lipo" -create "$stage/armv7" "$stage/arm64" -output "$app/OfflineMusic"

render() {
    if command -v rsvg-convert >/dev/null; then
        rsvg-convert -w "$2" -h "$2" "$root/Resources/$1.svg" -o "$3"
    else
        QT_QPA_PLATFORM=offscreen ksvgtopng5 "$2" "$2" "$root/Resources/$1.svg" "$3"
    fi
}
for entry in '76 Icon-76' '152 Icon-76@2x' '167 Icon-83.5@2x'; do
    read -r size name <<< "$entry"
    render Icon "$size" "$app/$name.png"
done
for icon in Device Lists; do
    for scale in 1 2; do
        size=$((25 * scale)); suffix=""
        [[ $scale == 1 ]] || suffix="@2x"
        render "$icon" "$size" "$app/$icon$suffix.png"
    done
done

# Local placeholder only. No Apple identity or provisioning profile is embedded.
"$signer" -S "$app/OfflineMusic"
(cd "$stage" && zip -q -r -9 OfflineMusic.ipa Payload)
mv "$stage/OfflineMusic.ipa" "$root/build/OfflineMusic.ipa"
echo "Built $root/build/OfflineMusic.ipa"
