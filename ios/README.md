# Offline Music for iPad

Native iPad app for iOS 9.0+, built for **armv7 and arm64**. A Spotify link supplies a playlist's names and track metadata. **Audio comes only from files you uploaded to your music server.** There is no Spotify audio extraction, streaming fallback, or third-party downloader.

## Connect and save

1. Upload music you own or have permission to copy using your server's `/upload` page. Use MP3 or AAC/M4A for broad iOS 9 playback support.
2. In **Playlists → Settings**, enter the server's **final HTTPS origin** (for example, `https://your-service.up.railway.app`) and its library token. Enter the token again when changing settings. The TLS certificate must be trusted by your iPad. Do not disable certificate verification or add ATS exceptions.
3. Choose a playlist, or tap **Import Spotify playlist…** and paste a playlist link. The server needs valid Spotify credentials and access to that playlist. The result gives matched and missing counts; the app reloads the catalog. **Open in Spotify**, when available, opens the original playlist separately.
4. Tap one track or **Save matched**. Keep the app in the foreground until the summary appears. You may use **On My iPad** during a save. Cancel or leave the playlist to stop the remaining queue. Saved files remain; retrying skips them. A missing row needs a matching upload. A failed row shows the error and can be tapped to retry.
5. Open **On My iPad**, tap a download to play, and use **Pause/Resume**. That tab is the startup screen and needs neither network nor token. Swipe left to remove a local copy; the server file is unaffected. Files are not transcoded: a format unsupported by iOS 9 (for example Ogg Vorbis) may download but cannot play in this app.

### Storage and credentials

The library lives in the app sandbox's Application Support directory, not Caches. Each download stages its audio and metadata together and publishes a single completed directory. On startup the app rebuilds the list from those entries; partial transfers are not published. Duplicate matches are keyed by normalized server origin and track ID. Downloads are excluded from backups and deleting the app removes them.

Only the server origin goes in preferences. Tokens are per-origin Keychain items (`WhenUnlockedThisDeviceOnly`), never in an IPA, URL, log, or offline index. The HTTP client uses an ephemeral session with no cookie or URL cache and refuses redirects. It uses `GET /api/catalog?enrich=0`, `POST /api/import/spotify` with a JSON `url`, and `GET /api/download/<id>`; only matched tracks with valid IDs are requested. It never treats a catalog-provided URL as an audio source.

## Build on Linux

Use an installed iOS 9.3 **device** SDK, an iOS cross-toolchain with Clang, Darwin ld and lipo (armv7 and arm64), ldid, Bash, an SVG renderer (`rsvg-convert` or `ksvgtopng5`) and zip. No dependency download or account login is performed by the build.

```sh
cd ios
export IOS_SDK_DIR="$HOME/theos-sdks/iPhoneOS9.3.sdk"
export IOS_TOOLCHAIN_BIN="$HOME/ios-toolchain/linux/iphone/bin"
export LDID_BIN="$HOME/ios-tools/ldid"
./build.sh
```

The result is **build/OfflineMusic.ipa**. Its local placeholder signature is **not an Apple signature and not sufficient to install on stock iOS**. The Theos 9.3 text stubs may emit simulator warnings at link time; verify the device architectures and minimum OS in the executable, not just the SDK name:

```sh
python3 tests/check_ipa.py build/OfflineMusic.ipa
```

Optional host-side storage tests (Clang + GNUstep Foundation on Linux, or Xcode command line tools on macOS): `./tests/test-library.sh`. These do not simulate iOS networking, Keychain, UIKit or audio playback.

## Sign and install on a stock iPad

Keep the private key and profile on your Mac; don't send them to the server or commit them. Use **one** of these flows:

- **With an Apple development or ad hoc profile:** On your Mac, use [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/) to register your iPad UDID and an explicit iOS app identifier. Create an iOS development or ad hoc provisioning profile for that identifier, device and signing certificate. Install that certificate **and its private key** in your Mac's Keychain; download the profile. Use a profile valid for this device and Apple signing tools compatible with iOS 9.
- **With Xcode-managed development signing:** Pair the iPad with a compatible Xcode version, sign in under Xcode Settings/Preferences → Accounts, and create an iOS app target with a unique explicit bundle ID, your team and automatic signing. Run that target on the iPad to obtain its development profile. Use that profile (from the signed app's `embedded.mobileprovision`) and its matching development identity below. Free personal-team profiles may require frequent renewal, and availability depends on Apple's current device/tool support.

From this directory on macOS:

```sh
security find-identity -v -p codesigning
./sign-on-mac.sh OfflineMusic.ipa Device.mobileprovision \
  "Apple Development: Your Name (IDENTIFIER)" OfflineMusic-signed.ipa
```

Use the exact identity name or hash from your Keychain. The helper requires an explicit (non-wildcard) device profile, sets its bundle ID, embeds the profile, signs with a matching Keychain access group, and verifies the app signature. It does not register a device, create an identity, or make an expired/untrusted profile valid. Use the **same team and bundle ID** when updating to keep the app's sandbox and Keychain access.

On a paired Mac with iOS 9 device support, open **Xcode → Window → Devices and Simulators** (older Xcode: **Devices**), select the iPad, and add the signed IPA under Installed Apps. Alternatively use Apple Configurator's **Add → Apps → Choose from my Mac**. If iOS asks to trust *your* development identity, verify it in Settings → General → Device Management/Profiles. Installation needs a valid certificate, profile and registered device; do not disable signing checks.

## Device acceptance

1. On the server, prepare a playlist with one matched MP3 and one missing track. Try a wrong token, then the correct token. Confirm matched and missing counts and the named missing row.
2. Save the playlist. Confirm one new download; save again and confirm it is skipped. For a failure, disconnect during a new download, reconnect and retry.
3. Enable airplane mode, terminate the app and reopen it. Play the downloaded song, pause/resume, and remove it. Confirm it stays removed after another relaunch.
