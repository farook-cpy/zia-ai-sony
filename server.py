#!/usr/bin/env python3
"""
Project Zia - Headless Android Backend Daemon & AI Companion
Custom-tailored for Termux on Sony Xperia 1 III (Android 13, aarch64)

Features:
- Headless HTTP JSON API on 0.0.0.0:5000 (Flask)
- WebSocket real-time event router & telemetry stream (/ws)
- AI Companion Brain with tool calling via google-genai (/api/chat)
- Voice pipeline (TTS with edge-tts, on-device playback, multimodal STT)
- Background Telegram bot & push notification gateway
- Automated cron scheduler (briefings, night summaries, battery watchdog)
- Direct SQLite3 storage with WAL mode for habits and finance
- Screen-off persistence hooks (Termux wake-lock integration)
- POSIX double-fork daemonization and graceful lifecycle management
"""

import os
import sys
import time
import json
import signal
import shutil
import logging
import sqlite3
import platform
import subprocess
from datetime import datetime, date
from contextlib import contextmanager
from typing import Dict, Any, Optional, List

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, request, send_file
from flask_sock import Sock

# ==============================================================================
# Configuration & Constants
# ==============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HABITS_DB_PATH = os.path.join(DATA_DIR, "habits.db")
FINANCE_DB_PATH = os.path.join(DATA_DIR, "finance.db")
PID_FILE_PATH = os.path.join(DATA_DIR, "server.pid")
LOG_FILE_PATH = os.path.join(DATA_DIR, "zia.log")

DEFAULT_HOST = os.getenv("ZIA_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("ZIA_PORT", "5000"))
START_TIME = time.time()

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "audio"), exist_ok=True)

# Configure logging
logger = logging.getLogger("project-zia")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] [ZIA] %(message)s")
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    _fh = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    _fh.setFormatter(_fmt)
    logger.addHandler(_sh)
    logger.addHandler(_fh)

# ==============================================================================
# Termux & System Persistence Helpers
# ==============================================================================

def is_termux_environment() -> bool:
    """Detect if running inside Termux on Android."""
    return (
        "TERMUX_VERSION" in os.environ
        or "/data/data/com.termux" in os.environ.get("PREFIX", "")
        or os.path.exists("/data/data/com.termux")
    )


def manage_termux_wakelock(action: str = "acquire") -> Dict[str, Any]:
    """
    Acquire or release Android wake-lock via Termux command-line tool.
    Prevents CPU deep sleep when device screen is turned off.
    """
    cmd = "termux-wake-lock" if action == "acquire" else "termux-wake-unlock"
    wakelock_bin = shutil.which(cmd)

    if not wakelock_bin:
        termux_bin = f"/data/data/com.termux/files/usr/bin/{cmd}"
        if os.path.exists(termux_bin):
            wakelock_bin = termux_bin

    if not wakelock_bin:
        return {
            "supported": False,
            "status": "binary_not_found",
            "message": f"'{cmd}' not found in PATH. Install via Termux: pkg install termux-tools",
        }

    try:
        res = subprocess.run(
            [wakelock_bin],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        success = res.returncode == 0
        return {
            "supported": True,
            "status": "success" if success else "failed",
            "action": action,
            "exit_code": res.returncode,
            "output": res.stdout.strip() or res.stderr.strip(),
        }
    except Exception as exc:
        logger.warning("Wake-lock error (%s): %s", action, exc)
        return {
            "supported": True,
            "status": "error",
            "action": action,
            "error": str(exc),
        }


def get_battery_info() -> Dict[str, Any]:
    """Retrieve device battery information via Termux API or /sys/class/power_supply."""
    # 1. Try termux-battery-status if available
    termux_bat = shutil.which("termux-battery-status")
    if termux_bat:
        try:
            res = subprocess.run(
                [termux_bat],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                return json.loads(res.stdout.strip())
        except Exception:
            pass

    # 2. Direct Linux/Android sysfs fallback
    bat_info: Dict[str, Any] = {}
    bat_path = "/sys/class/power_supply/battery"
    if os.path.exists(bat_path):
        try:
            cap_file = os.path.join(bat_path, "capacity")
            if os.path.isfile(cap_file):
                with open(cap_file, "r") as f:
                    bat_info["percentage"] = int(f.read().strip())

            status_file = os.path.join(bat_path, "status")
            if os.path.isfile(status_file):
                with open(status_file, "r") as f:
                    bat_info["status"] = f.read().strip()

            temp_file = os.path.join(bat_path, "temp")
            if os.path.isfile(temp_file):
                with open(temp_file, "r") as f:
                    bat_info["temperature_c"] = float(f.read().strip()) / 10.0
        except Exception as exc:
            bat_info["sysfs_error"] = str(exc)

    return bat_info or {"status": "unavailable"}


def get_memory_info() -> Dict[str, Any]:
    """Read memory stats from /proc/meminfo or system metrics."""
    mem_data: Dict[str, Any] = {}
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        if key in ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached"):
                            mem_data[key] = int(val) // 1024  # Convert kB to MB
        except Exception:
            pass
    return mem_data


def get_system_telemetry() -> Dict[str, Any]:
    """Compile aggregated device telemetry for status queries and WebSocket streaming."""
    uptime_sec = time.time() - START_TIME
    try:
        load_avg = list(os.getloadavg())
    except (OSError, AttributeError):
        load_avg = []

    disk_data = {}
    try:
        usage = shutil.disk_usage(DATA_DIR)
        disk_data = {
            "total_gb": round(usage.total / (1024**3), 2),
            "used_gb": round(usage.used / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "free_percent": round((usage.free / usage.total) * 100, 1),
        }
    except Exception:
        pass

    return {
        "uptime_seconds": round(uptime_sec, 2),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "load_average": load_avg,
        "memory": get_memory_info(),
        "storage": disk_data,
        "battery": get_battery_info(),
        "pid": os.getpid(),
        "is_termux": is_termux_environment(),
    }


# ==============================================================================
# Direct SQLite3 Connection & Management Routines
# ==============================================================================

@contextmanager
def get_db_connection(db_path: str):
    """
    Context manager for SQLite3 connections.
    Enforces WAL journal mode, busy timeouts, and foreign key constraints.
    """
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class HabitsDB:
    """Direct SQLite3 handler for habit management and logging."""

    @staticmethod
    def init_schema() -> None:
        with get_db_connection(HABITS_DB_PATH) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS habits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    frequency TEXT DEFAULT 'daily',
                    target_count INTEGER DEFAULT 1,
                    unit TEXT DEFAULT 'times',
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS habit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    habit_id INTEGER NOT NULL,
                    count INTEGER DEFAULT 1,
                    notes TEXT DEFAULT '',
                    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (habit_id) REFERENCES habits (id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_habits_active ON habits(active);
                CREATE INDEX IF NOT EXISTS idx_habit_logs_habit ON habit_logs(habit_id);
                CREATE INDEX IF NOT EXISTS idx_habit_logs_logged_at ON habit_logs(logged_at);
            """)

    @staticmethod
    def list_habits(include_inactive: bool = False) -> List[Dict[str, Any]]:
        with get_db_connection(HABITS_DB_PATH) as conn:
            query = """
                SELECT 
                    h.id, h.name, h.category, h.frequency, h.target_count, h.unit, 
                    h.active, h.created_at,
                    COALESCE(SUM(CASE WHEN DATE(hl.logged_at) = DATE('now') THEN hl.count ELSE 0 END), 0) AS completed_today,
                    COUNT(hl.id) AS total_logs
                FROM habits h
                LEFT JOIN habit_logs hl ON h.id = hl.habit_id
                WHERE (? = 1 OR h.active = 1)
                GROUP BY h.id
                ORDER BY h.active DESC, h.id ASC
            """
            cursor = conn.execute(query, (1 if include_inactive else 0,))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def create_habit(name: str, category: str = "general", frequency: str = "daily",
                     target_count: int = 1, unit: str = "times") -> int:
        with get_db_connection(HABITS_DB_PATH) as conn:
            cur = conn.execute(
                """
                INSERT INTO habits (name, category, frequency, target_count, unit, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (name, category, frequency, target_count, unit),
            )
            return cur.lastrowid

    @staticmethod
    def get_habit(habit_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection(HABITS_DB_PATH) as conn:
            cur = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,))
            row = cur.fetchone()
            if not row:
                return None
            habit = dict(row)

            log_cur = conn.execute(
                "SELECT id, count, notes, logged_at FROM habit_logs WHERE habit_id = ? ORDER BY logged_at DESC LIMIT 10",
                (habit_id,),
            )
            habit["recent_logs"] = [dict(r) for r in log_cur.fetchall()]
            return habit

    @staticmethod
    def log_habit(habit_id: int, count: int = 1, notes: str = "", logged_at: Optional[str] = None) -> int:
        with get_db_connection(HABITS_DB_PATH) as conn:
            check = conn.execute("SELECT id FROM habits WHERE id = ?", (habit_id,)).fetchone()
            if not check:
                raise ValueError(f"Habit with ID {habit_id} does not exist.")

            if logged_at:
                cur = conn.execute(
                    "INSERT INTO habit_logs (habit_id, count, notes, logged_at) VALUES (?, ?, ?, ?)",
                    (habit_id, count, notes, logged_at),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO habit_logs (habit_id, count, notes) VALUES (?, ?, ?)",
                    (habit_id, count, notes),
                )
            return cur.lastrowid

    @staticmethod
    def delete_habit(habit_id: int, hard_delete: bool = False) -> bool:
        with get_db_connection(HABITS_DB_PATH) as conn:
            if hard_delete:
                cur = conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
            else:
                cur = conn.execute("UPDATE habits SET active = 0 WHERE id = ?", (habit_id,))
            return cur.rowcount > 0

    @staticmethod
    def get_summary() -> Dict[str, Any]:
        with get_db_connection(HABITS_DB_PATH) as conn:
            total_active = conn.execute("SELECT COUNT(*) FROM habits WHERE active = 1").fetchone()[0]
            completed_today = conn.execute("""
                SELECT COUNT(DISTINCT habit_id) 
                FROM habit_logs 
                WHERE DATE(logged_at) = DATE('now')
            """).fetchone()[0]
            total_logs = conn.execute("SELECT COUNT(*) FROM habit_logs").fetchone()[0]

            return {
                "active_habits_count": total_active,
                "habits_logged_today": completed_today,
                "total_historical_logs": total_logs,
                "date": date.today().isoformat(),
            }


class FinanceDB:
    """Direct SQLite3 handler for transactions and financial tracking."""

    @staticmethod
    def init_schema() -> None:
        with get_db_connection(FINANCE_DB_PATH) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL CHECK(type IN ('income', 'expense', 'transfer')),
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT DEFAULT 'USD',
                    description TEXT DEFAULT '',
                    payment_method TEXT DEFAULT 'cash',
                    tags TEXT DEFAULT '',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL UNIQUE,
                    monthly_limit REAL NOT NULL,
                    currency TEXT DEFAULT 'USD',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type);
                CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
                CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp);
            """)

    @staticmethod
    def add_transaction(tx_type: str, category: str, amount: float,
                        currency: str = "USD", description: str = "",
                        payment_method: str = "cash", tags: str = "",
                        timestamp: Optional[str] = None) -> int:
        if tx_type not in ("income", "expense", "transfer"):
            raise ValueError(f"Invalid transaction type '{tx_type}'. Must be income, expense, or transfer.")

        with get_db_connection(FINANCE_DB_PATH) as conn:
            if timestamp:
                cur = conn.execute(
                    """
                    INSERT INTO transactions (type, category, amount, currency, description, payment_method, tags, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tx_type, category, amount, currency, description, payment_method, tags, timestamp),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO transactions (type, category, amount, currency, description, payment_method, tags)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tx_type, category, amount, currency, description, payment_method, tags),
                )
            return cur.lastrowid

    @staticmethod
    def list_transactions(limit: int = 50, offset: int = 0,
                          tx_type: Optional[str] = None,
                          category: Optional[str] = None) -> List[Dict[str, Any]]:
        with get_db_connection(FINANCE_DB_PATH) as conn:
            query = "SELECT * FROM transactions WHERE 1=1"
            params: List[Any] = []

            if tx_type:
                query += " AND type = ?"
                params.append(tx_type)
            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cur = conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def delete_transaction(tx_id: int) -> bool:
        with get_db_connection(FINANCE_DB_PATH) as conn:
            cur = conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
            return cur.rowcount > 0

    @staticmethod
    def get_summary() -> Dict[str, Any]:
        with get_db_connection(FINANCE_DB_PATH) as conn:
            totals = conn.execute("""
                SELECT 
                    COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS total_income,
                    COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense,
                    COUNT(id) AS transaction_count
                FROM transactions
            """).fetchone()

            total_income = totals["total_income"]
            total_expense = totals["total_expense"]
            net_balance = total_income - total_expense

            cur_cat = conn.execute("""
                SELECT category, SUM(amount) AS total_spent, COUNT(id) AS tx_count
                FROM transactions
                WHERE type = 'expense'
                GROUP BY category
                ORDER BY total_spent DESC
            """)
            category_breakdown = [dict(r) for r in cur_cat.fetchall()]

            return {
                "total_income": round(total_income, 2),
                "total_expense": round(total_expense, 2),
                "net_balance": round(net_balance, 2),
                "total_transactions": totals["transaction_count"],
                "expense_categories": category_breakdown,
            }


def check_db_connectivity() -> Dict[str, Any]:
    """Test read/write connectivity and return table statistics for both databases."""
    status: Dict[str, Any] = {}

    try:
        with get_db_connection(HABITS_DB_PATH) as conn:
            habits_count = conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
            logs_count = conn.execute("SELECT COUNT(*) FROM habit_logs").fetchone()[0]
            status["habits_db"] = {
                "status": "connected",
                "path": HABITS_DB_PATH,
                "size_bytes": os.path.getsize(HABITS_DB_PATH) if os.path.exists(HABITS_DB_PATH) else 0,
                "habits_count": habits_count,
                "logs_count": logs_count,
            }
    except Exception as exc:
        status["habits_db"] = {"status": "error", "error": str(exc)}

    try:
        with get_db_connection(FINANCE_DB_PATH) as conn:
            tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            status["finance_db"] = {
                "status": "connected",
                "path": FINANCE_DB_PATH,
                "size_bytes": os.path.getsize(FINANCE_DB_PATH) if os.path.exists(FINANCE_DB_PATH) else 0,
                "transactions_count": tx_count,
            }
    except Exception as exc:
        status["finance_db"] = {"status": "error", "error": str(exc)}

    return status


# ==============================================================================
# Subsystem Initialization & Companion Modules
# ==============================================================================

# 1. Event Broadcaster (WebSocket Bus)
from modules.events import broadcaster

# 2. AI Companion Brain (google-genai)
from modules.ai_brain import ZiaBrain

# 3. Voice Pipeline (edge-tts + Gemini STT)
from modules.voice import VoicePipeline

# 4. Telegram Bot Runner
from modules.telegram_bot import TelegramBotRunner

# 5. Background Cron Scheduler
from modules.scheduler import ZiaScheduler

# 6. Network & Tailscale Discovery Helper
from modules.network import NetworkDiscovery

# 7. SQLite Database Backup & Export Manager
from modules.backup import BackupManager

# 8. Xperia Hardware Termux API Bindings
from modules.xperia_hardware import XperiaHardware

# Companion Nodes In-Memory Registry (Linux Mint, Legacy Laptops, Workers)
companion_nodes: Dict[str, Any] = {}

# Initialize subsystem instances
ai_brain = ZiaBrain(
    habits_db=HabitsDB,
    finance_db=FinanceDB,
    system_telemetry_getter=get_system_telemetry,
    telegram_sender=lambda msg: telegram_bot.send_message(msg),
    event_broadcaster=broadcaster,
)

voice_pipeline = VoicePipeline(ai_brain=ai_brain)

telegram_bot = TelegramBotRunner(
    ai_brain=ai_brain,
    habits_db=HabitsDB,
    finance_db=FinanceDB,
    system_telemetry_getter=get_system_telemetry,
    voice_pipeline=voice_pipeline,
    event_broadcaster=broadcaster,
)

scheduler = ZiaScheduler(
    habits_db=HabitsDB,
    finance_db=FinanceDB,
    system_telemetry_getter=get_system_telemetry,
    telegram_sender=lambda msg: telegram_bot.send_message(msg),
    telegram_bot=telegram_bot,
    event_broadcaster=broadcaster,
)


# ==============================================================================
# Flask Application & Routing
# ==============================================================================

app = Flask("Project-Zia")
sock = Sock(app)


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "endpoint_not_found", "message": str(e)}), 404


@app.errorhandler(500)
def handle_500(e):
    logger.error("Internal Server Error: %s", e)
    return jsonify({"error": "internal_server_error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# WebSocket Real-Time Telemetry & Event Router
# ------------------------------------------------------------------------------

@sock.route("/ws")
def websocket_route(ws):
    """
    Bi-directional telemetry stream between Sony Xperia node and client UI (MacBook).
    Broadcasts real-time events, heartbeats, and receives interactive commands.
    """
    broadcaster.register_client(ws)
    try:
        while True:
            raw_msg = ws.receive()
            if raw_msg is None:
                break

            try:
                payload = json.loads(raw_msg)
            except Exception:
                payload = {"action": "chat", "message": str(raw_msg)}

            action = payload.get("action", "ping")

            if action == "ping":
                ws.send(json.dumps({"event": "pong", "timestamp": datetime.utcnow().isoformat() + "Z"}))
            elif action == "status":
                ws.send(json.dumps({"event": "status", "data": get_system_telemetry()}))
            elif action in ("chat", "query"):
                prompt = payload.get("message") or payload.get("query", "")
                result = ai_brain.process_query(prompt)
                ws.send(json.dumps({"event": "chat_response", "data": result}))
            elif action == "subscribe":
                ws.send(json.dumps({"event": "subscribed", "status": "active"}))
            else:
                ws.send(json.dumps({"event": "ack", "action": action}))

    except Exception as exc:
        logger.debug("WebSocket client disconnected or errored: %s", exc)
    finally:
        broadcaster.unregister_client(ws)


# ------------------------------------------------------------------------------
# Core System Endpoints
# ------------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """Returns basic service status, metadata, and uptime."""
    uptime_sec = time.time() - START_TIME
    net_info = NetworkDiscovery.get_network_summary(port=DEFAULT_PORT)
    return jsonify({
        "app": "Project Zia",
        "description": "Headless Android Backend Daemon & AI Companion",
        "status": "online",
        "architecture": platform.machine(),
        "platform": platform.platform(),
        "is_termux": is_termux_environment(),
        "uptime_seconds": round(uptime_sec, 2),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "network": {
            "local_ip": net_info["local_ip"],
            "tailscale_ip": net_info["tailscale_ip"],
            "dashboard_url": net_info["urls"].get("tailscale_dashboard") or net_info["urls"].get("local_dashboard"),
        },
        "active_modules": {
            "ai_brain": ai_brain.client is not None,
            "voice_pipeline": True,
            "telegram_bot": telegram_bot.is_configured(),
            "scheduler": True,
            "websocket_stream": True,
            "tailscale_discovery": net_info["tailscale_connected"],
            "backup_manager": True,
        },
        "endpoints": {
            "root": "GET /",
            "dashboard": "GET /dashboard",
            "status": "GET /status",
            "network": "GET /api/system/network",
            "websocket": "WS /ws",
            "chat": "POST /api/chat",
            "voice_tts": "POST /api/voice/tts",
            "voice_stt": "POST /api/voice/stt",
            "voice_speak": "POST /api/voice/speak",
            "habits": "GET|POST /api/habits",
            "finance": "GET|POST /api/finance/transactions",
            "backup": "POST /api/system/backup",
            "backups_list": "GET /api/system/backups",
            "hardware_torch": "POST /api/hardware/torch",
            "hardware_photo": "POST /api/hardware/photo",
            "hardware_location": "GET /api/hardware/location",
            "hardware_sms": "GET|POST /api/hardware/sms",
            "hardware_vibrate": "POST /api/hardware/vibrate",
            "hardware_status": "GET /api/hardware/status",
            "tailscale_serve": "GET|POST /api/system/tailscale/serve",
            "nodes_heartbeat": "POST /api/nodes/heartbeat",
            "nodes_list": "GET /api/nodes",
            "scheduler_jobs": "GET /api/scheduler/jobs",
            "notify": "POST /api/notify",
            "events": "GET /api/events",
        },
    })


@app.route("/dashboard", methods=["GET"])
@app.route("/app", methods=["GET"])
def serve_dashboard():
    """Serve the single-page client dashboard web application for MacBook / browser."""
    dash_path = os.path.join(BASE_DIR, "dashboard", "index.html")
    if not os.path.exists(dash_path):
        dash_path = os.path.join(BASE_DIR, "dashboard.html")
    if os.path.exists(dash_path):
        return send_file(dash_path)
    return jsonify({"error": "Dashboard template not found on server"}), 404


@app.route("/status", methods=["GET"])
def status():
    """Returns system health, resource usage, and database connectivity flags."""
    db_status = check_db_connectivity()
    telemetry = get_system_telemetry()

    all_dbs_ok = (
        db_status.get("habits_db", {}).get("status") == "connected"
        and db_status.get("finance_db", {}).get("status") == "connected"
    )

    return jsonify({
        "status": "healthy" if all_dbs_ok else "degraded",
        "uptime_seconds": telemetry["uptime_seconds"],
        "timestamp": telemetry["timestamp"],
        "databases": db_status,
        "system": telemetry,
        "subsystems": {
            "ai_brain": "ready" if ai_brain.client else "standby",
            "telegram_bot": "running" if telegram_bot._running else "inactive",
            "scheduler_jobs": len(scheduler.list_jobs()),
            "connected_ws_clients": broadcaster.client_count(),
        },
    })


@app.route("/api/events", methods=["GET"])
def get_events():
    """Retrieve recent event buffer and active client count."""
    return jsonify({
        "connected_clients": broadcaster.client_count(),
        "recent_events": broadcaster.get_recent_events(),
    })


# ------------------------------------------------------------------------------
# AI Companion Brain Endpoints
# ------------------------------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
@app.route("/api/agent/query", methods=["POST"])
def api_chat():
    """
    Route conversational queries through Gemini with tool-execution for local databases.
    """
    data = request.get_json(silent=True) or {}
    prompt = data.get("message") or data.get("prompt") or data.get("query")
    if not prompt or not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "message is required and must be non-empty string"}), 400

    history = data.get("conversation_history") or data.get("history")
    result = ai_brain.process_query(prompt.strip(), conversation_history=history)
    return jsonify(result)


# ------------------------------------------------------------------------------
# Voice & Audio Processing Endpoints
# ------------------------------------------------------------------------------

@app.route("/api/voice/tts", methods=["POST"])
def api_voice_tts():
    """
    Synthesize text into speech via edge-tts and stream MP3 or return file metadata.
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if not text:
        return jsonify({"error": "text is required"}), 400

    voice = data.get("voice")
    fmt = data.get("format", "stream")

    try:
        audio_path = voice_pipeline.synthesize_to_file(text, voice)
        if fmt == "json":
            return jsonify({
                "status": "success",
                "audio_file": os.path.basename(audio_path),
                "path": audio_path,
            })
        return send_file(audio_path, mimetype="audio/mpeg")
    except Exception as exc:
        logger.error("TTS generation error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/voice/voices", methods=["GET"])
def api_voice_voices():
    """List available neural voices from edge-tts."""
    lang = request.args.get("lang", "en")
    return jsonify({"voices": voice_pipeline.list_voices(lang)})


@app.route("/api/voice/speak", methods=["POST"])
def api_voice_speak():
    """Synthesize text and play directly on the Sony Xperia 1 III speaker."""
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if not text:
        return jsonify({"error": "text is required"}), 400
    voice = data.get("voice")
    result = voice_pipeline.speak_on_device(text, voice)
    return jsonify(result)


@app.route("/api/voice/stt", methods=["POST"])
def api_voice_stt():
    """
    Transcribe incoming audio bytes using Gemini multimodal audio understanding.
    Optionally executes transcribed command with Zia AI brain (?execute=1).
    """
    audio_bytes = None
    mime_type = "audio/mp3"

    if "audio" in request.files:
        audio_file = request.files["audio"]
        audio_bytes = audio_file.read()
        mime_type = audio_file.content_type or "audio/mp3"
    elif request.data:
        audio_bytes = request.data
        mime_type = request.content_type or "audio/mp3"

    if not audio_bytes:
        return jsonify({"error": "No audio file provided in request."}), 400

    execute = request.args.get("execute", "0") in ("1", "true")
    res = voice_pipeline.transcribe_audio(audio_bytes, mime_type=mime_type, execute_with_brain=execute)
    return jsonify(res)


# ------------------------------------------------------------------------------
# Push Notification & Remote Alert Endpoints
# ------------------------------------------------------------------------------

@app.route("/api/notify", methods=["POST"])
def api_notify():
    """Send an instant push notification to authorized Telegram chats."""
    data = request.get_json(silent=True) or {}
    message = data.get("message") or data.get("text")
    if not message:
        return jsonify({"error": "message is required"}), 400

    chat_id = data.get("chat_id")
    success = telegram_bot.send_message(message, chat_id=chat_id)

    broadcaster.broadcast("notification_sent", {
        "message": message,
        "chat_id": chat_id,
        "success": success,
    })

    return jsonify({
        "status": "success" if success else "failed",
        "telegram_configured": telegram_bot.is_configured(),
    })


# ------------------------------------------------------------------------------
# Automated Scheduler Endpoints
# ------------------------------------------------------------------------------

@app.route("/api/scheduler/jobs", methods=["GET"])
def api_scheduler_jobs():
    """List all registered background cron routines and next execution times."""
    return jsonify({"jobs": scheduler.list_jobs()})


@app.route("/api/scheduler/trigger/<job_name>", methods=["POST"])
def api_scheduler_trigger(job_name: str):
    """Manually invoke a scheduled routine (e.g. morning_briefing, nightly_summary)."""
    result = scheduler.trigger_job(job_name)
    return jsonify(result)


# ------------------------------------------------------------------------------
# Habits API Endpoints
# ------------------------------------------------------------------------------

@app.route("/api/habits", methods=["GET"])
def list_habits():
    include_inactive = request.args.get("all", "0") in ("1", "true")
    habits = HabitsDB.list_habits(include_inactive=include_inactive)
    return jsonify({"habits": habits, "count": len(habits)})


@app.route("/api/habits", methods=["POST"])
def create_habit():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return jsonify({"error": "name is required and must be non-empty string"}), 400

    category = data.get("category", "general")
    frequency = data.get("frequency", "daily")
    target_count = int(data.get("target_count", 1))
    unit = data.get("unit", "times")

    habit_id = HabitsDB.create_habit(name.strip(), category, frequency, target_count, unit)
    broadcaster.broadcast("habit_created", {
        "habit_id": habit_id, "name": name.strip(), "category": category
    })

    return jsonify({
        "message": "habit created successfully",
        "habit_id": habit_id,
        "name": name.strip(),
    }), 201


@app.route("/api/habits/<int:habit_id>", methods=["GET"])
def get_habit(habit_id: int):
    habit = HabitsDB.get_habit(habit_id)
    if not habit:
        return jsonify({"error": f"Habit {habit_id} not found"}), 404
    return jsonify(habit)


@app.route("/api/habits/<int:habit_id>/log", methods=["POST"])
def log_habit(habit_id: int):
    data = request.get_json(silent=True) or {}
    count = int(data.get("count", 1))
    notes = data.get("notes", "")
    logged_at = data.get("logged_at")

    try:
        log_id = HabitsDB.log_habit(habit_id, count=count, notes=notes, logged_at=logged_at)
        broadcaster.broadcast("habit_logged", {
            "habit_id": habit_id, "count": count, "log_id": log_id, "notes": notes
        })
        return jsonify({
            "message": "habit progress logged",
            "log_id": log_id,
            "habit_id": habit_id,
            "count": count,
        }), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/habits/<int:habit_id>", methods=["DELETE"])
def delete_habit(habit_id: int):
    hard = request.args.get("hard", "0") in ("1", "true")
    success = HabitsDB.delete_habit(habit_id, hard_delete=hard)
    if not success:
        return jsonify({"error": f"Habit {habit_id} not found"}), 404

    broadcaster.broadcast("habit_deleted", {"habit_id": habit_id, "hard_delete": hard})
    return jsonify({
        "message": f"Habit {habit_id} {'deleted' if hard else 'deactivated'} successfully"
    })


@app.route("/api/habits/summary", methods=["GET"])
def habits_summary():
    return jsonify(HabitsDB.get_summary())


# ------------------------------------------------------------------------------
# Finance API Endpoints
# ------------------------------------------------------------------------------

@app.route("/api/finance/transactions", methods=["GET"])
def list_transactions():
    limit = min(int(request.args.get("limit", 50)), 500)
    offset = int(request.args.get("offset", 0))
    tx_type = request.args.get("type")
    category = request.args.get("category")

    records = FinanceDB.list_transactions(limit=limit, offset=offset, tx_type=tx_type, category=category)
    return jsonify({"transactions": records, "count": len(records), "limit": limit, "offset": offset})


@app.route("/api/finance/transactions", methods=["POST"])
def add_transaction():
    data = request.get_json(silent=True) or {}
    tx_type = data.get("type", "expense")
    category = data.get("category")
    amount = data.get("amount")

    if not category or amount is None:
        return jsonify({"error": "category and amount are required fields"}), 400

    try:
        amount_float = float(amount)
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be a valid number"}), 400

    currency = data.get("currency", "USD")
    description = data.get("description", "")
    payment_method = data.get("payment_method", "cash")
    tags = data.get("tags", "")
    timestamp = data.get("timestamp")

    try:
        tx_id = FinanceDB.add_transaction(
            tx_type=tx_type,
            category=category,
            amount=amount_float,
            currency=currency,
            description=description,
            payment_method=payment_method,
            tags=tags,
            timestamp=timestamp,
        )

        broadcaster.broadcast("finance_transaction", {
            "tx_id": tx_id,
            "type": tx_type,
            "category": category,
            "amount": amount_float,
            "currency": currency,
        })

        return jsonify({
            "message": "transaction recorded successfully",
            "transaction_id": tx_id,
            "type": tx_type,
            "category": category,
            "amount": amount_float,
        }), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/finance/transactions/<int:tx_id>", methods=["DELETE"])
def delete_transaction(tx_id: int):
    success = FinanceDB.delete_transaction(tx_id)
    if not success:
        return jsonify({"error": f"Transaction {tx_id} not found"}), 404

    broadcaster.broadcast("finance_transaction_deleted", {"tx_id": tx_id})
    return jsonify({"message": f"Transaction {tx_id} deleted successfully"})


@app.route("/api/finance/summary", methods=["GET"])
def finance_summary():
    return jsonify(FinanceDB.get_summary())


# ------------------------------------------------------------------------------
# System Diagnostics & Persistence Hooks
# ------------------------------------------------------------------------------

@app.route("/api/system/info", methods=["GET"])
def system_info():
    """Detailed host diagnostics tailored for unrooted Android 13 on Sony Xperia 1 III."""
    return jsonify({
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "node": platform.node(),
        },
        "environment": {
            "is_termux": is_termux_environment(),
            "prefix": os.environ.get("PREFIX", "standard_linux"),
            "pid": os.getpid(),
        },
        "telemetry": get_system_telemetry(),
    })


@app.route("/api/system/wakelock", methods=["POST"])
def toggle_wakelock():
    """Acquire or release Termux wake-lock to support persistent screen-off operation."""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "acquire")
    if action not in ("acquire", "release"):
        return jsonify({"error": "action must be 'acquire' or 'release'"}), 400

    result = manage_termux_wakelock(action)
    return jsonify(result)


# ------------------------------------------------------------------------------
# Tailscale & Local Network Discovery
# ------------------------------------------------------------------------------

@app.route("/api/system/network", methods=["GET"])
def system_network():
    """Fetch local Wi-Fi IP, Tailscale VPN IP, dashboard URLs, and wireless scrcpy command."""
    info = NetworkDiscovery.get_network_summary(port=DEFAULT_PORT)
    return jsonify(info)


# ------------------------------------------------------------------------------
# SQLite Database Backup & Export Endpoints
# ------------------------------------------------------------------------------

@app.route("/api/system/backup", methods=["POST"])
def trigger_backup():
    """
    Generate an atomic WAL-safe snapshot of habits.db and finance.db,
    bundle into a zip file, and optionally push to Telegram.
    """
    data = request.get_json(silent=True) or {}
    send_tg = data.get("send_telegram", False)
    chat_id = data.get("chat_id")

    try:
        res = BackupManager.create_backup(
            send_to_telegram=send_tg,
            telegram_bot=telegram_bot,
            chat_id=chat_id,
        )
        broadcaster.broadcast("backup_created", {
            "filename": res.get("filename"),
            "size_kb": res.get("size_kb"),
            "telegram_dispatched": res.get("telegram_dispatched", False),
        })
        return jsonify(res), 201
    except Exception as exc:
        logger.error("Database backup generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/system/backups", methods=["GET"])
def list_backups():
    """List all available backup zip archives stored locally."""
    return jsonify({"backups": BackupManager.list_backups()})


@app.route("/api/system/backup/download/<filename>", methods=["GET"])
def download_backup(filename: str):
    """Securely download a specific backup archive by filename."""
    clean_filename = os.path.basename(filename)
    backup_path = os.path.join(DATA_DIR, "backups", clean_filename)
    if os.path.isfile(backup_path) and clean_filename.endswith(".zip"):
        return send_file(backup_path, as_attachment=True, download_name=clean_filename)
    return jsonify({"error": "Backup file not found"}), 404


# ------------------------------------------------------------------------------
# Xperia Hardware Endpoints (Termux API)
# ------------------------------------------------------------------------------

@app.route("/api/hardware/torch", methods=["POST"])
def api_hardware_torch():
    """Toggle Xperia camera flash / torch (on/off)."""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "on")
    res = XperiaHardware.toggle_torch(action)
    broadcaster.broadcast("hardware_torch", res)
    return jsonify(res)


@app.route("/api/hardware/photo", methods=["POST"])
def api_hardware_photo():
    """Trigger Xperia camera snapshot (camera_id: 0 = rear, 1 = front)."""
    data = request.get_json(silent=True) or {}
    cam_id = int(data.get("camera_id", 0))
    res = XperiaHardware.take_camera_photo(camera_id=cam_id)
    broadcaster.broadcast("hardware_photo", res)
    return jsonify(res)


@app.route("/api/hardware/location", methods=["GET"])
def api_hardware_location():
    """Query current device GPS or network coordinates."""
    prov = request.args.get("provider", "gps")
    req_type = request.args.get("type", "last")
    res = XperiaHardware.get_location(provider=prov, request_type=req_type)
    return jsonify(res)


@app.route("/api/hardware/sms", methods=["GET", "POST"])
def api_hardware_sms():
    """List inbox SMS messages or send an SMS text message."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        num = data.get("number")
        msg = data.get("message")
        if not num or not msg:
            return jsonify({"error": "number and message are required"}), 400
        res = XperiaHardware.send_sms(num, msg)
        return jsonify(res)

    limit = int(request.args.get("limit", 5))
    return jsonify(XperiaHardware.get_recent_sms(limit=limit))


@app.route("/api/hardware/vibrate", methods=["POST"])
def api_hardware_vibrate():
    """Trigger physical vibration motor on device."""
    data = request.get_json(silent=True) or {}
    dur = int(data.get("duration_ms", 500))
    res = XperiaHardware.vibrate(duration_ms=dur)
    return jsonify(res)


@app.route("/api/hardware/clipboard", methods=["GET", "POST"])
def api_hardware_clipboard():
    """Get or set Android system clipboard contents."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        txt = data.get("text", "")
        return jsonify(XperiaHardware.set_clipboard(txt))
    return jsonify(XperiaHardware.get_clipboard())


@app.route("/api/hardware/status", methods=["GET"])
def api_hardware_status():
    """Check availability of native Termux API bindings."""
    return jsonify(XperiaHardware.get_hardware_diagnostic())


# ------------------------------------------------------------------------------
# Tailscale Serve Reverse Proxy Controls
# ------------------------------------------------------------------------------

@app.route("/api/system/tailscale/status", methods=["GET"])
def api_tailscale_status():
    """Inspect Tailscale connection and active Serve configuration."""
    return jsonify({
        "serve": NetworkDiscovery.get_tailscale_serve_status(),
        "magicdns": NetworkDiscovery.get_magicdns_name(),
        "network": NetworkDiscovery.get_network_summary(port=DEFAULT_PORT),
    })


@app.route("/api/system/tailscale/serve", methods=["POST"])
def api_tailscale_serve():
    """Configure or reset Tailscale Serve reverse proxy for HTTPS dashboard access."""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "start")
    port = int(data.get("port", DEFAULT_PORT))
    if action in ("stop", "reset"):
        res = NetworkDiscovery.reset_tailscale_serve()
    else:
        res = NetworkDiscovery.configure_tailscale_serve(target_port=port)
    return jsonify(res)


# ------------------------------------------------------------------------------
# Companion Nodes Registry (Linux Mint / Legacy Hardware)
# ------------------------------------------------------------------------------

@app.route("/api/nodes/heartbeat", methods=["POST"])
def api_nodes_heartbeat():
    """Ingest hardware vitals from remote companion nodes (e.g. Linux Mint laptop)."""
    data = request.get_json(silent=True) or {}
    node_id = data.get("node_id") or "anonymous"
    data["last_seen_epoch"] = time.time()
    data["last_seen"] = datetime.utcnow().isoformat() + "Z"
    companion_nodes[node_id] = data

    broadcaster.broadcast("companion_node_heartbeat", data)
    return jsonify({"status": "received", "node_id": node_id}), 200


@app.route("/api/nodes", methods=["GET"])
def api_nodes_list():
    """List all registered companion nodes and their live vitals."""
    now = time.time()
    for nid, node in companion_nodes.items():
        node["online"] = (now - node.get("last_seen_epoch", 0)) < 45
    return jsonify({"nodes": list(companion_nodes.values()), "count": len(companion_nodes)})


# ==============================================================================
# Lifecycle & Signal Management
# ==============================================================================

def write_pid_file():
    """Record current process ID to support daemon monitoring and stopping."""
    try:
        with open(PID_FILE_PATH, "w") as f:
            f.write(str(os.getpid()))
    except Exception as exc:
        logger.warning("Could not write PID file: %s", exc)


def remove_pid_file():
    """Remove PID file upon clean termination."""
    try:
        if os.path.exists(PID_FILE_PATH):
            os.remove(PID_FILE_PATH)
    except Exception:
        pass


def daemonize():
    """
    Standard Unix double-fork to detach process from controlling terminal,
    making it completely resilient to session disconnects.
    """
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        logger.error("Fork #1 failed: %s", e)
        sys.exit(1)

    os.setsid()
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    try:
        pid = os.fork()
        if pid > 0:
            with open(PID_FILE_PATH, "w") as f:
                f.write(str(pid))
            sys.exit(0)
    except OSError as e:
        logger.error("Fork #2 failed: %s", e)
        sys.exit(1)

    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "r") as devnull_r:
        os.dup2(devnull_r.fileno(), sys.stdin.fileno())
    with open(LOG_FILE_PATH, "a+", encoding="utf-8") as devnull_w:
        os.dup2(devnull_w.fileno(), sys.stdout.fileno())
        os.dup2(devnull_w.fileno(), sys.stderr.fileno())


def signal_handler(signum, frame):
    """Clean exit handler on SIGINT / SIGTERM."""
    sig_name = signal.Signals(signum).name
    logger.info("Received termination signal %s. Shutting down Project Zia daemon...", sig_name)

    # Stop background companion runners
    broadcaster.stop()
    telegram_bot.stop()
    scheduler.stop()

    remove_pid_file()
    sys.exit(0)


# ==============================================================================
# Initialization & Main Entrypoint
# ==============================================================================

def initialize_application():
    """Bootstrap databases, start background workers, and auto-acquire Termux wakelock."""
    logger.info("Initializing Project Zia master components...")
    HabitsDB.init_schema()
    FinanceDB.init_schema()
    logger.info("SQLite databases initialized: %s, %s", HABITS_DB_PATH, FINANCE_DB_PATH)

    # If running in Termux, attempt to acquire wake-lock on startup
    if is_termux_environment():
        logger.info("Detected Termux environment. Attempting screen-off wake-lock acquisition...")
        lock_res = manage_termux_wakelock("acquire")
        logger.info("Termux wake-lock status: %s", lock_res.get("status"))

    # Start WebSocket telemetry heartbeat thread (emits every 10s)
    broadcaster.start_heartbeat(get_system_telemetry, interval_seconds=10)

    # Start background Telegram bot poller if configured
    telegram_bot.start()

    # Start background cron scheduler
    scheduler.start()

    write_pid_file()


def main():
    """Parse CLI arguments and start Flask HTTP daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="Project Zia Headless Backend Daemon & AI Companion")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--daemon", action="store_true", help="Run as detached background daemon")
    parser.add_argument("--init-only", action="store_true", help="Initialize databases and exit")
    args = parser.parse_args()

    # SIGHUP: Ignore terminal hangup
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    # Register termination signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.daemon:
        logger.info("Daemonizing Project Zia process...")
        daemonize()

    initialize_application()

    if args.init_only:
        logger.info("Initialization completed successfully. Exiting (--init-only).")
        return

    logger.info("Starting Project Zia backend on http://%s:%d (WebSocket on /ws)", args.host, args.port)
    logger.info("Target Architecture: %s | Zero-Bloat Mode: ACTIVE", platform.machine())

    # Log and print network discovery connection parameters
    NetworkDiscovery.print_startup_banner(port=args.port)
    net_summary = NetworkDiscovery.get_network_summary(port=args.port)
    logger.info("Local Access URL: %s", net_summary['urls']['local_dashboard'])
    if net_summary['tailscale_ip']:
        logger.info("Tailscale Access URL: %s", net_summary['urls']['tailscale_dashboard'])
        logger.info("Wireless scrcpy: %s", net_summary['scrcpy_commands']['tailscale'])

    # Run lightweight Flask daemon
    app.run(
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
