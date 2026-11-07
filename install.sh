#!/usr/bin/env bash
# ==============================================================================
# PROJECT ZIA - ZERO-TOUCH ONE-TIME INSTALLER & BOOTSTRAPPER
# Custom-tailored for unrooted Sony Xperia 1 III (Termux, Android 13, aarch64)
# ==============================================================================

set -e

# Terminal colors
CYAN="\033[0;36m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
BOLD="\033[1m"
NC="\033[0m"

echo -e "${CYAN}${BOLD}"
echo "  ███████╗██╗ █████╗     ███╗   ███╗ ██████╗ ██████╗ ██╗   ██╗██╗     ███████╗"
echo "  ╚══███╔╝██║██╔══██╗    ████╗ ████║██╔═══██╗██╔══██╗██║   ██║██║     ██╔════╝"
echo "    ███╔╝ ██║███████║    ██╔████╔██║██║   ██║██║  ██║██║   ██║██║     █████╗  "
echo "   ███╔╝  ██║██╔══██║    ██║╚██╔╝██║██║   ██║██║  ██║██║   ██║██║     ██╔══╝  "
echo "  ███████╗██║██║  ██║    ██║ ╚═╝ ██║╚██████╔╝██████╔╝╚██████╔╝███████╗███████╗"
echo "  ╚══════╝╚═╝╚═╝  ╚═╝    ╚═╝     ╚═╝ ╚═════╝ ╚═════╝  ╚═════╝ ╚══════╝╚══════╝"
echo -e "${NC}"
echo -e "${GREEN}${BOLD}>> Initializing One-Touch Automated Installation for Sony Xperia 1 III...${NC}\n"

# Detect directory context
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

is_termux() {
    [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ] || [[ "$PREFIX" == *"com.termux"* ]]
}

# ------------------------------------------------------------------------------
# 1. Native Package Manager Configuration
# ------------------------------------------------------------------------------
if is_termux; then
    echo -e "${CYAN}[1/7] Updating Termux repositories and unlocking scientific mirrors...${NC}"
    export DEBIAN_FRONTEND=noninteractive

    # Request Termux storage access if not already granted
    if [ ! -d "$HOME/storage" ] && command -v termux-setup-storage >/dev/null 2>&1; then
        echo -e "${YELLOW}>> Requesting Termux storage permissions...${NC}"
        termux-setup-storage || true
    fi

    # Prevent Termux prompts and ensure clean repository mirrors
    pkg update -y || true
    pkg install science-repo unstable-repo -y || true
    pkg update -y || true

    echo -e "${CYAN}[2/7] Installing pre-compiled native packages (bypassing Rust/C++ compilation)...${NC}"
    pkg install python python-cryptography python-numpy numpy python-pillow opencv python-opencv clang make termux-tools termux-api git curl jq -y || {
        echo -e "${YELLOW}Notice: Some packages were already present or skipped. Proceeding...${NC}"
    }

    # Verify Termux:API companion APK binding
    if command -v termux-battery-status >/dev/null 2>&1; then
        if ! timeout 2 termux-battery-status >/dev/null 2>&1; then
            echo -e "${YELLOW}Notice: Termux:API binary is installed, but the companion 'Termux:API' APK from F-Droid is not responding yet.${NC}"
            echo -e "${YELLOW}        (Install 'Termux:API' from F-Droid to enable camera, flashlight, and battery sensors).${NC}"
        fi
    fi
else
    echo -e "${CYAN}[1/7] Running in Linux environment (${NC}$(uname -m)${CYAN})...${NC}"
    echo -e "${CYAN}[2/7] Verifying python3 and essential development tools...${NC}"
fi

# ------------------------------------------------------------------------------
# 2. Directory Structure Setup
# ------------------------------------------------------------------------------
echo -e "${CYAN}[3/7] Setting up isolated project directories...${NC}"
mkdir -p data/audio
mkdir -p data/photos
mkdir -p data/backups
mkdir -p nodes
mkdir -p dashboard

# ------------------------------------------------------------------------------
# 3. Environment Configuration File
# ------------------------------------------------------------------------------
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo -e "${CYAN}[4/7] Generating configuration file from template (.env)...${NC}"
        cp .env.example .env
    else
        echo -e "${CYAN}[4/7] Creating default configuration (.env)...${NC}"
        cat << 'EOF' > .env
ZIA_HOST=0.0.0.0
ZIA_PORT=5000
GEMINI_API_KEY=
ZIA_AI_MODEL=gemini-2.5-flash
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_IDS=
ZIA_VOICE=en-US-AvaNeural
EOF
    fi
else
    echo -e "${GREEN}[4/7] Existing configuration file (.env) detected.${NC}"
fi

# Auto-inject keys if passed via environment variables during installation
if [ -n "$GEMINI_API_KEY" ]; then
    sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=$GEMINI_API_KEY|" .env
    echo -e "${GREEN}>> GEMINI_API_KEY automatically saved to .env${NC}"
fi
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN|" .env
    echo -e "${GREEN}>> TELEGRAM_BOT_TOKEN automatically saved to .env${NC}"
fi
if [ -n "$TELEGRAM_ALLOWED_CHAT_IDS" ]; then
    sed -i "s|^TELEGRAM_ALLOWED_CHAT_IDS=.*|TELEGRAM_ALLOWED_CHAT_IDS=$TELEGRAM_ALLOWED_CHAT_IDS|" .env
    echo -e "${GREEN}>> TELEGRAM_ALLOWED_CHAT_IDS automatically saved to .env${NC}"
fi

# ------------------------------------------------------------------------------
# 4. Virtual Environment & Pre-compiled Wheels
# ------------------------------------------------------------------------------
echo -e "${CYAN}[5/7] Configuring Python virtual environment with system site-packages...${NC}"
if [ ! -d "venv" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv --system-site-packages venv
    else
        python -m venv --system-site-packages venv
    fi
fi

# Guarantee system-site-packages linkage to native numpy/opencv/cryptography
if [ -f "venv/pyvenv.cfg" ]; then
    sed -i '/include-system-site-packages/d' venv/pyvenv.cfg 2>/dev/null || true
    echo "include-system-site-packages = true" >> venv/pyvenv.cfg
fi

# Upgrade pip and install dependencies
VENV_PYTHON="venv/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel --quiet

echo -e "${CYAN}[6/7] Installing pure-Python dependencies from requirements.txt...${NC}"
"$VENV_PYTHON" -m pip install --no-cache-dir -r requirements.txt --quiet

# Pre-initialize SQLite schemas
"$VENV_PYTHON" server.py --init-only >/dev/null 2>&1 || true

# ------------------------------------------------------------------------------
# 5. Acquire Screen-Off Wake-Lock (Android / Termux)
# ------------------------------------------------------------------------------
if is_termux; then
    if command -v termux-wake-lock >/dev/null 2>&1; then
        echo -e "${CYAN}[7/7] Acquiring Termux wake-lock to maintain 24/7 background operation...${NC}"
        termux-wake-lock 2>/dev/null || true
    fi
else
    echo -e "${CYAN}[7/7] Finalizing system configuration...${NC}"
fi

# Make scripts executable
chmod +x setup.sh 2>/dev/null || true
chmod +x companion_node.py 2>/dev/null || true

# ------------------------------------------------------------------------------
# 6. Launch Project Zia Background Daemon
# ------------------------------------------------------------------------------
echo -e "\n${GREEN}${BOLD}>> Starting Project Zia background daemon...${NC}"
./setup.sh --daemon

sleep 1.5

# ------------------------------------------------------------------------------
# 7. Verification & Network Summary
# ------------------------------------------------------------------------------
echo ""
./setup.sh --network

echo -e "\n${GREEN}${BOLD}========================================================================${NC}"
echo -e "${GREEN}${BOLD}             PROJECT ZIA IS INSTALLED & RUNNING 24/7!                   ${NC}"
echo -e "${GREEN}${BOLD}========================================================================${NC}"
echo -e "You can now open the Web Dashboard in your MacBook or mobile browser."
echo ""
echo -e "${BOLD}Next Steps (Configure API Keys & Settings):${NC}"
echo -e "  1. Edit settings and insert your keys:  ${CYAN}nano .env${NC}"
echo -e "  2. Restart daemon to apply new keys:   ${CYAN}./setup.sh --daemon${NC}"
echo -e "  3. Check live status anytime:          ${CYAN}./setup.sh --status${NC}"
echo -e "  4. Tail real-time logs:                ${CYAN}./setup.sh --logs${NC}"
echo -e "  5. Enable Tailscale HTTPS:             ${CYAN}./setup.sh --serve${NC}"
echo ""
