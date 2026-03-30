# Auto-Update

Wright Telemetry checks for updates at every startup and applies them automatically. Updates are fetched from [GitHub Releases](https://github.com/Wright-1/wright-telemetry/releases) using the public GitHub API — no extra infrastructure required.

---

## How It Works

1. At startup, `wright_telemetry/updater.py` launches a background daemon thread.
2. The thread calls the GitHub Releases API to get the latest release tag.
3. If the tag version is higher than the running `__version__`, the appropriate binary asset is downloaded for the current platform.
4. The running binary is replaced and the process restarts.
5. If the check fails for any reason (no internet, API rate limit, etc.), a warning is logged and the collector continues normally.

The check only runs for frozen PyInstaller binaries (`sys.frozen == True`). Running from source is unaffected.

---

## Platform Assets

The updater looks for these asset names on the GitHub Release:

| Platform | Asset name |
|----------|------------|
| Linux    | `wright-telemetry` |
| macOS    | `wright-telemetry-macos.zip` |
| Windows  | `wright-telemetry.exe` |

These names match what the CI workflows upload to each release. If you rename a release asset, update `_ASSET_NAMES` in `wright_telemetry/updater.py` to match.

---

## Restart Behaviour

**Linux / macOS:** The binary is overwritten in-place and the process is replaced via `os.execv`. The restart is seamless.

**Windows:** Windows won't allow overwriting a running executable. Instead, the new binary is staged alongside the current one and a PowerShell helper (launched detached) waits 2 seconds, swaps the files, and starts the new version.

---

## Opting Out

Add `"disable_auto_update": true` to `~/.wright-telemetry/config.json`:

```json
{
  "disable_auto_update": true
}
```

---

## Publishing a Release

No special steps are needed beyond the normal release process:

1. Bump `__version__` in `wright_telemetry/__init__.py`.
2. Commit and push to `main`.
3. Tag: `git tag v0.2.3 && git push origin v0.2.3`

GitHub Actions builds the binaries and uploads them to the release. All existing installs will pick up the new version on their next startup.

---

## GitHub API Rate Limits

Unauthenticated requests to `api.github.com` are limited to 60/hour per IP. For a daemon that only checks on startup this is well within limits. If you ever need to increase the limit, set a `GITHUB_TOKEN` environment variable and pass it as a `Authorization: Bearer` header in `_fetch_latest_release()`.
