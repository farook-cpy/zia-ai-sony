# Project Zia - Headless Android Backend Daemon & AI Companion

Custom-tailored, high-performance, lightweight headless backend server and AI companion written in Python for the **Sony Xperia 1 III** (`aarch64` architecture, Android 13) running inside an unrooted **Termux** environment, with multi-node sync for legacy hardware (e.g. 2007 MacBook Pro running Linux Mint XFCE).

Designed strictly according to the **Zero-Bloat Rule**:
* **Zero GUI Bindings on Mobile**: No X11, desktop window managers, tkinter, or pyautogui on the phone.
* **Zero Compilation Bottlenecks**: No local Rust (`maturin`, `pydantic-core`) or Fortran build steps. Pre-compiled system packages are integrated via virtual environment system site-packages.
* **Screen-Off Background Persistence**: Native integration with `termux-wake-lock`, POSIX double-fork daemonization, and signal resilience (`SIGHUP`, `SIGTERM`, `SIGINT`).

---

## 1. Directory Structure

```text
.
├── data/
│   ├── habits.db            # SQLite3 database (WAL mode)
│   ├── finance.db           # SQLite3 database (WAL mode)
│   ├── audio/               # Generated neural TTS MP3 files
│   ├── photos/              # Captured Xperia camera snapshots
│   ├── backups/             # Atomic zip database backups
│   ├── zia.log              # Persistent daemon execution log
│   └── server.pid           # Running process PID file
├── modules/
│   ├── ai_brain.py          # Gemini AI brain with automatic function/tool calling
│   ├── xperia_hardware.py   # Native Termux API bindings (torch, camera, GPS, SMS)
│   ├── events.py            # WebSocket event broadcaster & 10s telemetry heartbeat
│   ├── voice.py             # edge-tts synthesis, on-device speak, & multimodal STT
│   ├── telegram_bot.py      # Background long-polling bot & notification gateway
│   ├── scheduler.py         # Automated cron scheduler (briefings, watchdog, DB prune)
│   ├── network.py           # Tailscale & local Wi-Fi auto-discovery & Serve helper
│   └── backup.py            # Atomic SQLite online backup & Telegram exporter
├── dashboard/
│   └── index.html           # Single-page client dashboard web app (Tailwind dark mode)
├── dashboard.html           # Standalone MacBook client dashboard copy
├── companion_node.py        # Zero-dependency telemetry worker for Linux Mint / legacy laptops
├── nodes/
│   └── mint_companion.py    # Companion worker copy for deployment
├── venv/                    # Python virtual environment (with system site-packages enabled)
├── .env.example             # Configuration template for API keys & tokens
├── server.py                # Master backend application server (Flask + WebSocket daemon)
├── requirements.txt         # Pure-Python dependencies
├── setup.sh                 # Automated setup, network discovery, and daemon manager
└── README.md                # Full documentation & endpoint reference
```

---

## 2. Quick Start Playbook (Sony Xperia 1 III Termux)

### Step 1: Clone or Copy to Device

Inside Termux on your Xperia 1 III:
```bash
cd ~
mkdir -p Project-Zia
cd Project-Zia
```

### Step 2: Configure Environment Keys (Optional but Recommended)

Copy the environment template and add your API keys:
```bash
cp .env.example .env
nano .env
```
* **`GEMINI_API_KEY`**: Obtain from [Google AI Studio](https://aistudio.google.com/) for conversational intelligence.
* **`TELEGRAM_BOT_TOKEN`**: Obtain from [@BotFather](https://t.me/BotFather) on Telegram for remote mobile control.

### Step 3: Run Automated Setup

Run the self-contained setup script:
```bash
chmod +x setup.sh
./setup.sh
```

---

## 3. Daemon & Network Control Commands

Use `./setup.sh` to manage daemon lifecycle:

| Command | Action |
|---|---|
| `./setup.sh --start` | Launch server in foreground (interactive console mode) |
| `./setup.sh --daemon` | Spawn background daemon (detached with nohup & PID tracking) |
| `./setup.sh --status` | Check if process is alive and query `GET /status` |
| `./setup.sh --network` | Auto-detect Tailscale IP, local Wi-Fi IP, and wireless `scrcpy` command |
| `./setup.sh --serve` | Enable Tailscale Serve reverse proxy for automatic HTTPS (`no port numbers`) |
| `./setup.sh --backup` | Create atomic SQLite backup and push to Telegram |
| `./setup.sh --stop` | Cleanly terminate the background daemon |
| `./setup.sh --logs` | Stream recent logs from `data/zia.log` |
| `./setup.sh --test` | Run self-test across all local API and AI endpoints |

---

## 4. Subsystems & Features

### 1. Xperia Hardware Tool-Calling (`termux-api`)
Interacts directly with Sony Xperia 1 III physical hardware via Termux API bindings:
* **Flashlight**: `POST /api/hardware/torch` — Toggle phone flashlight (`{"action": "on"|"off"}`).
* **Camera Snapshot**: `POST /api/hardware/photo` — Trigger camera capture (`{"camera_id": 0}`) and saves high-resolution photo to `data/photos/`.
* **GPS Location**: `GET /api/hardware/location` — Fetches live GPS/network latitude, longitude, and accuracy.
* **Cellular SMS**: `GET /api/hardware/sms` & `POST /api/hardware/sms` — Read inbox messages or transmit SMS through SIM.
* **Haptics**: `POST /api/hardware/vibrate` — Trigger physical vibration motor.
* **Clipboard**: `GET|POST /api/hardware/clipboard` — Read or write Android system clipboard.
* **AI Tool Integration**: Zia's Gemini brain automatically invokes these tools when prompted (e.g. *"Zia, turn on my flashlight"* or *"What's my phone's location?"*).
* **Telegram Bot Controls**: Command hardware remotely via `/torch`, `/photo`, `/location`, `/sms`, and `/vibrate`.

### 2. Tailscale Serve & Automatic HTTPS
* Secure your web dashboard and API endpoints with automatic HTTPS encryption over your private Tailnet:
  ```bash
  ./setup.sh --serve
  # Or directly: tailscale serve --bg 5000
  ```
* Bypasses port numbers and HTTP warnings, serving clean MagicDNS URLs:
  ```text
  https://xperia-1-iii.tailxxxx.ts.net/dashboard
  ```
* Dedicated API control: `GET|POST /api/system/tailscale/serve`.

### 3. Linux Mint / Legacy Laptop Companion Node Sync
Unifies your 2007 MacBook Pro running Linux Mint XFCE (or any legacy computer) into Project Zia's single pane of glass:
* **Zero Dependencies**: `companion_node.py` uses only Python standard library.
* **Hardware Vitals Reported**:
  * Core 2 Duo CPU Temperatures (°C) from `/sys/class/thermal` or `hwmon`
  * 500GB HDD storage capacity and free space
  * CPU Load average and RAM metrics
  * Uptime and Battery/AC status
* **How to Run on Linux Mint**:
  ```bash
  python3 companion_node.py --server http://<xperia_tailscale_ip>:5000
  ```
* **Dashboard View**: Connected companion nodes appear live under the "Companion Nodes" card on the web dashboard!

### 4. MacBook Client Dashboard Web App
A lightweight, brutalist dark-mode single-page client application built with Tailwind CSS:
* **Hosted on Device**: Accessible from any browser at `http://<xperia_ip>:5000/dashboard` or `https://<magicdns>/dashboard`.
* **Standalone Option**: Open `dashboard.html` directly in your MacBook browser.
* **Features**:
  * **Live Hardware Vitals**: Real-time battery meter, CPU load (1m/5m/15m), RAM meter, and disk space.
  * **Companion Nodes Grid**: Live CPU temperatures, 500GB HDD space, and load from Linux Mint laptop.
  * **Hardware Toolbar**: One-click Torch toggle, Camera snapshot, and Vibration.
  * **Habit Tracker**: Check off daily habits (+1 counter), view progress bars, and create new habits inline.
  * **Quick Finance**: Expense/income transaction forms, live net balance, and recent transaction log.
  * **AI Companion Chat**: Converse with Zia in real time, with text-to-speech audio playback buttons.
  * **Live Event Stream**: Ticker displaying incoming WebSocket events (`/ws`) in real time.

### 5. SQLite Database Backup & Export Routine
* **Atomic Online Snapshots**: Uses SQLite's online backup API (`src_conn.backup(dest_conn)`) after WAL truncation, preventing corrupted copies during active writes.
* **Manifest & Checksums**: Computes SHA256 hashes and row counts for each database into `manifest.json`.
* **Telegram Export**: `POST /api/system/backup` automatically zips `habits.db`, `finance.db`, and manifest, and pushes the `.zip` archive directly to your Telegram chat.
* **Automated Weekly Cron**: Automatically scheduled every Sunday at 04:00 AM.
* **Retention Pruning**: Automatically retains the last 10 backups and prunes older archives to prevent storage exhaustion.

### 6. AI Companion Brain (`google-genai`)
Routes user prompts to Gemini 2.5 Flash with **automatic function calling** enabling Zia to directly read and write to your local databases and control hardware:
* `POST /api/chat` or `POST /api/agent/query`:
  ```json
  {
    "message": "I drank 1 liter of water, spent $4.50 on coffee, and please turn on the flashlight."
  }
  ```
  Zia will automatically invoke `log_habit_progress`, `record_financial_expense`, and `toggle_flashlight`, updating the databases and toggling the hardware!

### 7. WebSocket Real-Time Telemetry Stream (`WS /ws`)
Bi-directional WebSocket event router connecting your Sony Xperia 1 III to client interfaces (e.g. your MacBook client):
* Connect to: `ws://<sony_ip>:5000/ws`
* **Heartbeat**: Emits `telemetry_tick` every 10 seconds with CPU load, RAM, and battery %.
* **Live Broadcast**: Instantly broadcasts all database events (`habit_logged`, `finance_transaction`, `backup_created`, `companion_node_heartbeat`) to connected clients.

---

## 5. API Endpoints Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | `GET` | Service status, uptime, network info, and route index |
| `/dashboard` | `GET` | Single-page MacBook client dashboard web application |
| `/status` | `GET` | Detailed device telemetry, battery, and database flags |
| `/api/system/network` | `GET` | Tailscale IP, local Wi-Fi IP, and wireless scrcpy command |
| `/api/system/tailscale/serve`| `GET`, `POST` | Configure Tailscale Serve automatic HTTPS proxy |
| `/api/system/backup` | `POST` | Generate atomic SQLite backup zip & export to Telegram |
| `/api/system/backups` | `GET` | List all local backup zip archives |
| `/api/system/backup/download/<file>` | `GET` | Download specific backup archive |
| `/api/hardware/torch` | `POST` | Toggle flashlight on / off |
| `/api/hardware/photo` | `POST` | Capture rear/front camera photo |
| `/api/hardware/location` | `GET` | Live device GPS/network location |
| `/api/hardware/sms` | `GET`, `POST` | View SMS inbox or transmit SMS message |
| `/api/hardware/vibrate` | `POST` | Trigger physical haptic vibration |
| `/api/hardware/status` | `GET` | Termux API hardware diagnostic |
| `/api/nodes/heartbeat` | `POST` | Ingest telemetry from Linux Mint companion node |
| `/api/nodes` | `GET` | List all registered companion nodes |
| `/ws` | `WS` | Bi-directional WebSocket telemetry stream |
| `/api/chat` | `POST` | Conversational AI companion query with tool execution |
| `/api/voice/tts` | `POST` | Synthesize text to MP3 audio stream |
| `/api/voice/voices` | `GET` | List available edge-tts neural voices |
| `/api/voice/speak` | `POST` | Play synthesized speech on device hardware speaker |
| `/api/voice/stt` | `POST` | Transcribe speech audio via Gemini multimodal API |
| `/api/notify` | `POST` | Push notification to Telegram chat |
| `/api/scheduler/jobs` | `GET` | List active background cron routines |
| `/api/scheduler/trigger/<name>` | `POST` | Trigger scheduled job on demand |
| `/api/events` | `GET` | Inspect recent event ring buffer |
| `/api/habits` | `GET`, `POST` | List or create habits |
| `/api/habits/<id>` | `GET`, `DELETE`| Retrieve habit history or delete |
| `/api/habits/<id>/log` | `POST` | Log habit completion |
| `/api/habits/summary` | `GET` | Daily habit summary |
| `/api/finance/transactions` | `GET`, `POST` | List or add finance transactions |
| `/api/finance/transactions/<id>` | `DELETE` | Delete transaction |
| `/api/finance/summary` | `GET` | Total income, expense, and category breakdown |
| `/api/system/info` | `GET` | Detailed OS, CPU (`aarch64`), and Termux diagnostics |
| `/api/system/wakelock` | `POST` | Acquire or release Android Termux wake-lock |

---

## 6. Screen-Off Persistence Note

Android battery optimization may kill background processes if wake-lock is not held:
1. `setup.sh` and `server.py` automatically invoke `termux-wake-lock` on startup if inside Termux.
2. Disable Android battery optimization for the **Termux** app:
   * **Settings → Apps → Termux → App battery usage → Unrestricted**.
