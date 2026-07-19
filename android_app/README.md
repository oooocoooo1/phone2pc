# Phone2PC Android Client

## Release build

`flutter build apk --release` produces an internal unsigned artifact that
Android cannot install. Run `scripts/build_android_release.ps1` from the
repository root to build, sign, and verify the installable APK. The result is
written to `dist/phone2pc-android-release-v<version>.apk`.

Back up `D:\DevTools\phone2pc\signing` securely. Losing the release key makes
future APK updates incompatible with installed release builds.

Android client for the Phone2PC protocol v6. It requires Android 8.0 (API 26) or newer and must be used with PC v5.4 or newer.

After a successful connection, the client runs an ongoing foreground service and automatically reconnects after transient network loss. Use the link button, the in-app Stop button, or the notification action to disconnect explicitly. On Xiaomi/HyperOS, allow autostart and set the app battery policy to Unrestricted for the strongest background reliability.

## Development

The project pins Flutter 3.38.3 in `.fvmrc`.

```powershell
fvm install 3.38.3
fvm flutter pub get
fvm flutter analyze
fvm flutter test
fvm flutter build apk --debug
```

Received files are written to the public `Download/Phone2PC` directory. Android 11+ requires the user to grant all-files access for direct, single-pass large-file writes.

File payloads prefer a token-authenticated raw HTTP stream on port 8766, with WebSocket as an automatic compatibility fallback. Android uploads run in a native background thread and report throttled progress to Flutter.

## Release signing

Create `android/key.properties` locally:

```properties
storePassword=...
keyPassword=...
keyAlias=upload
storeFile=C:\\path\\to\\upload-keystore.jks
```

The file is ignored by Git. Without it, Gradle produces an unsigned release artifact instead of using the debug key.
