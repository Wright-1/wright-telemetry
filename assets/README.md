# Assets

Place the app icon here before building:

| File | Purpose |
|------|---------|
| `wright-telemetry.icns` | macOS `.app` bundle icon (required for production builds) |
| `wright-telemetry.png`  | Source PNG (1024×1024) — convert with `make-icon.sh` |

## Generating the .icns from a PNG

```bash
# Requires Xcode command-line tools
mkdir wright.iconset
sips -z 16   16   wright-telemetry.png --out wright.iconset/icon_16x16.png
sips -z 32   32   wright-telemetry.png --out wright.iconset/icon_16x16@2x.png
sips -z 32   32   wright-telemetry.png --out wright.iconset/icon_32x32.png
sips -z 64   64   wright-telemetry.png --out wright.iconset/icon_32x32@2x.png
sips -z 128  128  wright-telemetry.png --out wright.iconset/icon_128x128.png
sips -z 256  256  wright-telemetry.png --out wright.iconset/icon_128x128@2x.png
sips -z 256  256  wright-telemetry.png --out wright.iconset/icon_256x256.png
sips -z 512  512  wright-telemetry.png --out wright.iconset/icon_256x256@2x.png
sips -z 512  512  wright-telemetry.png --out wright.iconset/icon_512x512.png
cp wright-telemetry.png                    wright.iconset/icon_512x512@2x.png
iconutil -c icns wright.iconset -o wright-telemetry.icns
rm -rf wright.iconset
```

If no icon file is present the build will still succeed — the app will use
the default Python/PyInstaller icon.
