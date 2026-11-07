#!/usr/bin/env bash
# ==============================================================================
# PROJECT ZIA - AUTOMATED SETUP & DAEMON RUNNER
# Target: Sony Xperia 1 III (Android 13, aarch64, Termux)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DATA_DIR="$SCRIPT_DIR/data"
VENV_DIR="$SCRIPT_DIR/venv"
PID_FILE="$DATA_DIR/server.pid"
LOG_FILE="$DATA_DIR/zia.log"
PYTHON_BIN="$VENV_DIR/bin/python"

# Color helpers
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

log_info() {
    echo -e "${BLUE}[PROJECT ZIA]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PROJECT ZIA] SUCCESS:${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[PROJECT ZIA] WARNING:${NC} $1"
}

log_error() {
    echo -e "${RED}[PROJECT ZIA] ERROR:${NC} $1"
}

is_termux() {
    [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ] || [[ "$PREFIX" == *"com.termux"* ]]
}

get_local_ip() {
    if is_termux; then
        ifconfig 2>/dev/null | grep -E "inet " | grep -v "127.0.0.1" | awk '{print $2}' | head -n 1
    else
        hostname -I 2>/dev/null | awk '{print $1}'
    fi
}

get_tailscale_ip() {
    if command -v tailscale >/dev/null 2>&1; then
        tailscale ip -4 2>/dev/null | head -n 1
    elif [ -f "/data/data/com.termux/files/usr/bin/tailscale" ]; then
        /data/data/com.termux/files/usr/bin/tailscale ip -4 2>/dev/null | head -n 1
    else
        ip -4 addr show 2>/dev/null | grep -oE "inet 100\.[0-9]+\.[0-9]+\.[0-9]+" | awk '{print $2}' | head -n 1
    fi
}

cmd_network() {
    if [ -f "$PYTHON_BIN" ]; then
        "$PYTHON_BIN" -c "from modules.network import NetworkDiscovery; NetworkDiscovery.print_startup_banner()"
    else
        LOCAL_IP=$(get_local_ip)
        TS_IP=$(get_tailscale_ip)
        echo "=== Project Zia Network Discovery ==="
        echo "Local Wi-Fi IP:    $LOCAL_IP"
        echo "Tailscale IP:      ${TS_IP:-Not Connected}"
        echo "Local Dashboard:   http://$LOCAL_IP:5000/dashboard"
        if [ -n "$TS_IP" ]; then
            echo "Tailscale Dash:    http://$TS_IP:5000/dashboard"
            echo "Wireless scrcpy:   scrcpy --tcpip=$TS_IP:5555"
        fi
    fi
}

cmd_serve() {
    log_info "Configuring Tailscale Serve reverse proxy for HTTPS on port 5000..."
    if command -v tailscale >/dev/null 2>&1; then
        tailscale serve --bg 5000
        tailscale serve status
        log_success "Tailscale Serve configured. Access dashboard via HTTPS MagicDNS URL."
    elif [ -f "/data/data/com.termux/files/usr/bin/tailscale" ]; then
        /data/data/com.termux/files/usr/bin/tailscale serve --bg 5000
        /data/data/com.termux/files/usr/bin/tailscale serve status
    else
        log_warn "tailscale CLI not found. Install Tailscale from F-Droid or Play Store."
    fi
}

cmd_backup() {
    log_info "Creating atomic SQLite database backup..."
    if [ -f "$PYTHON_BIN" ]; then
        "$PYTHON_BIN" -c "from modules.backup import BackupManager; res = BackupManager.create_backup(send_to_telegram=True); print(f'Created archive: {res[\"filename\"]} ({res[\"size_kb\"]} KB)'); print(f'Telegram dispatched: {res[\"telegram_dispatched\"]}')"
    else
        log_error "Python binary not found. Run ./setup.sh first."
    fi
}

cmd_setup() {
    log_info "Starting Project Zia automated setup for unrooted Sony Xperia 1 III..."

    # 1. Termux package manager updates & native dependencies
    if is_termux; then
        log_info "Detected Termux environment on $(uname -m)."
        log_info "Enabling science-repo & unstable-repo to access pre-compiled aarch64 wheels..."
        pkg update -y || log_warn "pkg update returned non-zero, continuing..."
        pkg install science-repo unstable-repo -y || log_warn "Could not enable extra repos, continuing..."
        pkg update -y || true

        log_info "Installing native packages (python, clang, make, cryptography, numpy, opencv)..."
        pkg install python python-cryptography numpy opencv python-opencv clang make termux-tools -y || {
            log_warn "Some native packages could not be installed via pkg. Continuing with standard python installation..."
        }
    else
        log_info "Running in standard Linux environment ($(uname -m))."
    fi

    # 2. Directory initialization
    log_info "Ensuring directory structure..."
    mkdir -p "$DATA_DIR"

    # 3. Virtual environment with system site-packages
    if [ ! -d "$VENV_DIR" ]; then
        log_info "Creating Python virtual environment..."
        if command -v python3 >/dev/null 2>&1; then
            python3 -m venv --system-site-packages "$VENV_DIR"
        elif command -v python >/dev/null 2>&1; then
            python -m venv --system-site-packages "$VENV_DIR"
        else
            log_error "Python 3 is not installed or not in PATH."
            exit 1
        fi
    fi

    # Ensure system-site-packages is explicitly enabled in pyvenv.cfg
    if [ -f "$VENV_DIR/pyvenv.cfg" ]; then
        if ! grep -q "include-system-site-packages = true" "$VENV_DIR/pyvenv.cfg"; then
            sed -i '/include-system-site-packages/d' "$VENV_DIR/pyvenv.cfg" 2>/dev/null || true
            echo "include-system-site-packages = true" >> "$VENV_DIR/pyvenv.cfg"
        fi
    fi

    # 4. Dependency installation
    log_info "Upgrading pip, setuptools, and wheel in virtual environment..."
    "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel

    if [ -f "requirements.txt" ]; then
        log_info "Installing Python dependencies from requirements.txt..."
        "$PYTHON_BIN" -m pip install --no-cache-dir -r requirements.txt
    fi

    # 5. Database initialization
    log_info "Initializing SQLite3 schemas (habits.db and finance.db)..."
    "$PYTHON_BIN" server.py --init-only

    # 6. Secure Screen-Off Execution (Wake-Lock)
    if is_termux; then
        if command -v termux-wake-lock >/dev/null 2>&1; then
            log_info "Acquiring Android Termux wake-lock to keep daemon alive when screen is locked..."
            termux-wake-lock || log_warn "Failed to acquire termux-wake-lock."
        else
            log_warn "termux-wake-lock not found. Please install termux-tools or acquire wake-lock via Termux notification."
        fi
    fi

    log_success "Project Zia setup completed successfully!"
    echo ""
    echo "To start the server in foreground:"
    echo "  ./setup.sh --start"
    echo ""
    echo "To start the server as a background daemon (screen-off persistent):"
    echo "  ./setup.sh --daemon"
    echo ""
    echo "To check daemon status:"
    echo "  ./setup.sh --status"
    echo ""
}

cmd_start() {
    if [ ! -f "$PYTHON_BIN" ]; then
        log_warn "Virtual environment not detected. Running setup first..."
        cmd_setup
    fi

    if is_termux && command -v termux-wake-lock >/dev/null 2>&1; then
        termux-wake-lock 2>/dev/null || true
    fi

    LOCAL_IP=$(get_local_ip)
    TS_IP=$(get_tailscale_ip)
    log_info "Starting Project Zia HTTP daemon on 0.0.0.0:5000..."
    if [ -n "$LOCAL_IP" ]; then
        log_info "Local Dashboard:     http://$LOCAL_IP:5000/dashboard"
    fi
    if [ -n "$TS_IP" ]; then
        log_info "Tailscale Dashboard: http://$TS_IP:5000/dashboard"
        log_info "Wireless scrcpy:     scrcpy --tcpip=$TS_IP:5555"
    fi
    exec "$PYTHON_BIN" server.py "$@"
}

cmd_daemon() {
    if [ ! -f "$PYTHON_BIN" ]; then
        cmd_setup
    fi

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            log_warn "Project Zia daemon is already running (PID: $PID)."
            exit 0
        fi
        rm -f "$PID_FILE"
    fi

    if is_termux && command -v termux-wake-lock >/dev/null 2>&1; then
        termux-wake-lock 2>/dev/null || true
    fi

    log_info "Spawning Project Zia daemon in background..."
    "$PYTHON_BIN" server.py --daemon

    sleep 1.0

    if [ -f "$PID_FILE" ]; then
        DAEMON_PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
            LOCAL_IP=$(get_local_ip)
            TS_IP=$(get_tailscale_ip)
            log_success "Project Zia daemon is running in background (PID: $DAEMON_PID)"
            log_info "Log file: $LOG_FILE"
            if [ -n "$LOCAL_IP" ]; then
                log_info "Local Dashboard:     http://$LOCAL_IP:5000/dashboard"
            fi
            if [ -n "$TS_IP" ]; then
                log_info "Tailscale Dashboard: http://$TS_IP:5000/dashboard"
                log_info "Wireless scrcpy:     scrcpy --tcpip=$TS_IP:5555"
            fi
            return 0
        fi
    fi

    log_error "Daemon failed to start. Showing last 20 log lines:"
    tail -n 20 "$LOG_FILE" 2>/dev/null || true
    exit 1
}

cmd_stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            log_info "Stopping Project Zia daemon (PID: $PID)..."
            kill "$PID" || kill -9 "$PID"
            rm -f "$PID_FILE"
            log_success "Daemon stopped."
            return 0
        fi
        rm -f "$PID_FILE"
    fi

    # Fallback search by process name
    PIDS=$(pgrep -f "server.py" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        log_info "Stopping server processes: $PIDS"
        kill $PIDS 2>/dev/null || true
        log_success "Daemon stopped."
    else
        log_warn "No running Project Zia process detected."
    fi
}

cmd_status() {
    RUNNING=0
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            RUNNING=1
            log_success "Project Zia daemon is RUNNING (PID: $PID)."
        fi
    fi

    if [ "$RUNNING" -eq 0 ]; then
        PIDS=$(pgrep -f "server.py" 2>/dev/null || true)
        if [ -n "$PIDS" ]; then
            RUNNING=1
            log_success "Project Zia process is RUNNING (PID(s): $PIDS)."
        fi
    fi

    if [ "$RUNNING" -eq 1 ]; then
        echo ""
        log_info "Querying local health endpoint (GET http://127.0.0.1:5000/status)..."
        if command -v curl >/dev/null 2>&1; then
            curl -s http://127.0.0.1:5000/status || log_warn "Could not connect to http://127.0.0.1:5000/status"
            echo ""
        else
            log_warn "curl not installed to probe endpoint."
        fi
    else
        log_warn "Project Zia daemon is STOPPED."
    fi
}

cmd_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f -n 50 "$LOG_FILE"
    else
        log_warn "No log file found at $LOG_FILE"
    fi
}

cmd_test() {
    log_info "Running comprehensive self-test against local Project Zia server..."
    if command -v curl >/dev/null 2>&1; then
        echo -e "\n1. Test GET /"
        curl -s http://127.0.0.1:5000/

        echo -e "\n\n2. Test GET /status"
        curl -s http://127.0.0.1:5000/status

        echo -e "\n\n3. Test GET /api/system/network (Tailscale & Local Discovery)"
        curl -s http://127.0.0.1:5000/api/system/network

        echo -e "\n\n4. Test GET /dashboard (Web Client App)"
        curl -s -I http://127.0.0.1:5000/dashboard | head -n 1

        echo -e "\n\n5. Test POST /api/chat (AI Companion Brain)"
        curl -s -X POST http://127.0.0.1:5000/api/chat \
            -H "Content-Type: application/json" \
            -d '{"message": "Status check"}'

        echo -e "\n\n6. Test POST /api/voice/tts (Neural Voice Synthesis)"
        curl -s -X POST http://127.0.0.1:5000/api/voice/tts \
            -H "Content-Type: application/json" \
            -d '{"text": "Project Zia voice synthesis test", "format": "json"}'

        echo -e "\n\n7. Test POST /api/system/backup (SQLite Backup Routine)"
        curl -s -X POST http://127.0.0.1:5000/api/system/backup \
            -H "Content-Type: application/json" \
            -d '{"send_telegram": false}'

        echo -e "\n\n8. Test GET /api/scheduler/jobs (Cron Scheduler)"
        curl -s http://127.0.0.1:5000/api/scheduler/jobs

        echo -e "\n\n9. Test POST /api/habits"
        curl -s -X POST http://127.0.0.1:5000/api/habits \
            -H "Content-Type: application/json" \
            -d '{"name": "Hydration Test", "category": "health", "target_count": 2, "unit": "liters"}'

        echo -e "\n\n10. Test POST /api/finance/transactions"
        curl -s -X POST http://127.0.0.1:5000/api/finance/transactions \
            -H "Content-Type: application/json" \
            -d '{"type": "expense", "category": "cloud", "amount": 9.99, "description": "Cloud Backup"}'

        echo -e "\n\n11. Test GET /api/hardware/status (Xperia Termux-API Tools)"
        curl -s http://127.0.0.1:5000/api/hardware/status

        echo -e "\n\n12. Test GET /api/system/tailscale/status (Tailscale HTTPS Status)"
        curl -s http://127.0.0.1:5000/api/system/tailscale/status

        echo -e "\n\n13. Test POST /api/nodes/heartbeat (Companion Node Heartbeat)"
        curl -s -X POST http://127.0.0.1:5000/api/nodes/heartbeat \
            -H "Content-Type: application/json" \
            -d '{"node_id": "test-mint-laptop", "node_name": "Linux Mint 2007 MacBook Pro", "telemetry": {"cpu_temp_c": 64.5, "hdd_storage": {"free_gb": 340.2}}}'

        echo -e "\n\n14. Test GET /api/nodes (Registered Companion Nodes)"
        curl -s http://127.0.0.1:5000/api/nodes

        echo -e "\n\n15. Test GET /api/events (WebSocket Event Buffer)"
        curl -s http://127.0.0.1:5000/api/events
        echo ""
    else
        log_error "curl is required to run cmd_test."
    fi
}

# Main command dispatcher
case "${1:-setup}" in
    setup|--setup)
        cmd_setup
        ;;
    start|--start)
        shift || true
        cmd_start "$@"
        ;;
    daemon|--daemon)
        cmd_daemon
        ;;
    stop|--stop)
        cmd_stop
        ;;
    status|--status)
        cmd_status
        ;;
    network|--network|ip|--ip)
        cmd_network
        ;;
    serve|--serve|https|--https)
        cmd_serve
        ;;
    backup|--backup)
        cmd_backup
        ;;
    logs|--logs)
        cmd_logs
        ;;
    test|--test)
        cmd_test
        ;;
    help|--help|-h)
        echo "Usage: ./setup.sh [COMMAND]"
        echo ""
        echo "Commands:"
        echo "  setup, --setup     Run complete setup (virtualenv, dependencies, db init)"
        echo "  start, --start     Start HTTP server in foreground"
        echo "  daemon, --daemon   Start HTTP server in background (screen-off daemon)"
        echo "  stop, --stop       Stop background daemon"
        echo "  status, --status   Check status and ping /status endpoint"
        echo "  network, --network Display Tailscale IP, local IP, and scrcpy command"
        echo "  serve, --serve     Enable Tailscale Serve HTTPS reverse proxy (no port numbers)"
        echo "  backup, --backup   Trigger immediate SQLite database backup"
        echo "  logs, --logs       Tail daemon logs"
        echo "  test, --test       Run local API endpoint validation tests"
        ;;
    *)
        log_error "Unknown option: $1"
        echo "Run './setup.sh --help' for usage."
        exit 1
        ;;
esac
