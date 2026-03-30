# Wright Telemetry Collector

A small tool that runs on your local network, reads data from your mining rigs, and sends it to your **Wright Fan dashboard** so you can track performance, predict fan failures, and see how Wright fans are keeping your operation running.

---

## What Does This Thing Do?

You run this on any spare computer that's on the **same local network** as your miners.  It connects to each miner's built-in API (Braiins OS to start, more coming), reads the metrics you choose to share, encrypts everything, and sends it to your Wright Fan account.

**You choose exactly what data to share.**  Every category is off by default.  The setup wizard explains what each one does and asks you before turning anything on.

---

## Quick Start

### 1. Download

Grab the latest release for your operating system from the [Releases page](https://github.com/Wright-1/wright-telemetry/releases):

| Platform | File |
|----------|------|
| Windows  | `wright-telemetry.exe` |
| macOS    | `wright-telemetry-macos.zip` (unzip first) |
| Linux    | `wright-telemetry` |

Once you have the collector running, **it updates itself automatically** — no need to download new releases manually.

### 2. Run It

Open a terminal (Command Prompt on Windows, Terminal on Mac/Linux) and navigate to wherever you downloaded the file.

**Windows:**
```
wright-telemetry.exe
```

**Mac (you may need to allow it in System Settings > Privacy):**
```
chmod +x wright-telemetry
./wright-telemetry
```

**Linux:**
```
chmod +x wright-telemetry
./wright-telemetry
```

### 3. Follow the Setup Wizard

The first time you run it, a setup wizard walks you through everything:

```
============================================================
  WRIGHT TELEMETRY COLLECTOR -- SETUP
============================================================

  This wizard will walk you through connecting your miners to
  your Wright Fan dashboard.  You'll need:
    1. Your Wright Fan API key   (from the customer portal)
    2. Your Facility ID           (from the customer portal)
    3. The IP address of each miner on your local network

  Wright Fan API Key []: wf_abc123def456
  Wright Fan API URL [https://api.wrightfan.com]:
  Facility ID []: facility_789
  Poll interval in seconds [30]:
  Collector type [braiins]:
```

Then it asks you to add your miners one at a time:

```
--- Miner #1 ---
  Give this miner a friendly name: Rack A - Slot 1
  Braiins miner IP or URL: 192.168.1.100
  Braiins username [root]:
  Braiins password (hidden):

  Add another miner? (y/n) [n]: y

--- Miner #2 ---
  Give this miner a friendly name: Rack A - Slot 2
  Braiins miner IP or URL: 192.168.1.101
  ...
```

Finally, it walks through each data category and asks if you want to enable it:

```
------------------------------------------------------------
  Temperature & Fan RPM  (currently OFF)
  API call: GET /api/v1/cooling/state

  Reads the temperature sensors and fan speeds from your miner.
  Wright uses this data to predict the lifespan of your fans and
  monitor for degradation so we can alert you before a failure.

  Enable Temperature & Fan RPM? (y/n) [y/N]: y
```

### 4. Install as a Background Service (Optional but Recommended)

So it starts automatically on boot and restarts if anything goes wrong:

```
wright-telemetry --install
```

That's it.  It runs in the background now.  Forget about it.

---

## What Data Can It Collect?

Every category is **off by default**.  You pick what to share during setup.

| Category | What It Reads | Why Wright Needs It |
|----------|--------------|---------------------|
| Temperature & Fan RPM | Fan speeds, chip temperatures | Predict fan lifespan, catch degradation early |
| Hashrate & Power | Hashrate, pool stats, power draw | Show you how Wright fans save you money |
| Uptime | How long the miner has been running | Show how modular design increases your uptime |
| Hashboard Temps | Per-board chip temperatures | Spot hot-spots before they cause downtime |
| Miner Errors | Error log from the miner | Alert you to fan failures, auto-file reports |

---

## Changing Your Settings

Want to add more miners, change what data you share, or update your API key?

```
wright-telemetry --setup
```

This re-runs the wizard.  Your existing settings are shown as defaults so you only change what you need.

---

## Uninstalling the Background Service

If you want to stop the background service:

```
wright-telemetry --uninstall
```

This removes the auto-start registration.  Your config file stays in place.

---

## Where Is My Config Stored?

All settings are saved to:

```
~/.wright-telemetry/config.json
```

(`~` means your home folder -- `C:\Users\YourName` on Windows, `/home/yourname` on Linux, `/Users/yourname` on Mac.)

Logs are at `~/.wright-telemetry/collector.log`.

---

## Troubleshooting

**"Can't reach miner" errors**

- Make sure the computer running this tool is on the **same local network** as your miners
- Try opening `http://MINER_IP` in a browser -- if that doesn't load, the tool can't reach it either
- Check if your miner has a firewall or the web interface is disabled

**"Auth failed" errors**

- Double-check your Braiins username and password
- The default username is usually `root`
- If your miner has no password set, just press Enter when asked

**Mac: "unidentified developer" warning**

Go to **System Settings > Privacy & Security** and click "Allow Anyway" next to the wright-telemetry warning.

**It was working but stopped**

If you installed it as a background service, check the logs at `~/.wright-telemetry/collector.log`.  The service auto-restarts on failures, so check for repeated errors.

**Disable automatic updates**

The collector checks for updates at every startup and restarts automatically when a new version is available. To opt out, add the following to `~/.wright-telemetry/config.json`:

```json
"disable_auto_update": true
```

---

## Privacy & Security

- **You control your data.**  Every data category is off by default.  You explicitly opt in during setup.
- **Everything is encrypted.**  Before any data leaves your network, it's encrypted with AES-256-GCM.  Even if someone intercepted the traffic, they couldn't read it.
- **We never touch your miner.**  This tool only *reads* data.  It never changes settings, never starts/stops mining, never modifies anything on your rig.
- **Open source.**  The code is right here.  You can read every line.

---

## Commands Reference

| Command | What It Does |
|---------|-------------|
| `wright-telemetry` | Run the collector (setup wizard on first run) |
| `wright-telemetry --setup` | Re-run the setup wizard |
| `wright-telemetry --install` | Install as a background service |
| `wright-telemetry --uninstall` | Remove the background service |
| `wright-telemetry --version` | Print version number |

---

## License

Apache 2.0 -- see [LICENSE](LICENSE) for details.
