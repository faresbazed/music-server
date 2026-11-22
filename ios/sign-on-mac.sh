#!/bin/bash
# Sign with your own Apple certificate and a matching device provisioning profile.
set -euo pipefail
if [[ "$(uname -s)" != Darwin || $# != 4 ]]; then
    echo 'On macOS: sign-on-mac.sh input.ipa device.mobileprovision "Signing Identity" output.ipa' >&2
    exit 2
fi
input="$1"; profile="$2"; identity="$3"; output="$4"
[[ -f "$input" && -f "$profile" && ! -e "$output" ]] || { echo 'Check inputs; output must be a new file.' >&2; exit 1; }
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
/usr/bin/ditto -x -k "$input" "$stage"
app="$stage/Payload/OfflineMusic.app"
[[ -f "$app/Info.plist" && -x "$app/OfflineMusic" ]] || { echo 'Expected OfflineMusic.app in IPA.' >&2; exit 1; }
security cms -D -i "$profile" > "$stage/profile.plist"
readkey() { /usr/libexec/PlistBuddy -c "Print :$1" "$stage/profile.plist"; }
prefix="$(readkey ApplicationIdentifierPrefix:0)"
team="$(readkey TeamIdentifier:0)"
appID="$(readkey Entitlements:application-identifier)"
readkey ProvisionedDevices:0 >/dev/null || { echo 'Use an iOS development or ad hoc device profile.' >&2; exit 1; }
[[ "$appID" == "$prefix."* && "$appID" != *'*'* ]] || { echo 'An explicit App ID profile is required.' >&2; exit 1; }
bundle="${appID#"$prefix."}"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $bundle" "$app/Info.plist"
ent="$stage/entitlements.plist"
pb=/usr/libexec/PlistBuddy
"$pb" -c 'Clear dict' -c "Add :application-identifier string $appID" \
    -c "Add :com.apple.developer.team-identifier string $team" \
    -c 'Add :keychain-access-groups array' -c "Add :keychain-access-groups:0 string $appID" "$ent"
cp "$profile" "$app/embedded.mobileprovision"
/usr/bin/codesign --force --sign "$identity" --digest-algorithm=sha1,sha256 --timestamp=none --entitlements "$ent" "$app"
/usr/bin/codesign --verify --strict --verbose=2 "$app"
(cd "$stage" && /usr/bin/zip -qry signed.ipa Payload)
cp "$stage/signed.ipa" "$output"
echo "Signed $output"
