"""
Project Zia - Network Discovery Helper (Tailscale & Local Wi-Fi)
Automatically resolves LAN IP, Tailscale CGNAT IP (100.x.y.z), direct access URLs,
and scrcpy wireless debugging commands.
"""

import os
import re
import json
import socket
import shutil
import logging
import subprocess
from typing import Dict, Any, Optional, List

logger = logging.getLogger("project-zia.network")


class NetworkDiscovery:
    """Detects active Wi-Fi and Tailscale network connections."""

    @staticmethod
    def get_tailscale_ip() -> Optional[str]:
        """Detect Tailscale IPv4 address via CLI or network interface inspection."""
        # 1. Tailscale CLI binary check (standard paths + Termux paths)
        candidate_bins = [
            shutil.which("tailscale"),
            "/data/data/com.termux/files/usr/bin/tailscale",
            "/usr/bin/tailscale",
            "/usr/local/bin/tailscale",
        ]
        for ts_bin in candidate_bins:
            if ts_bin and os.path.exists(ts_bin):
                try:
                    res = subprocess.run(
                        [ts_bin, "ip", "-4"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if res.returncode == 0:
                        ip = res.stdout.strip().split("\n")[0].strip()
                        if ip and ip.startswith("100."):
                            return ip
                except Exception:
                    pass

        # 2. Inspect 'ip -4 addr' for 100.x.y.z or tailscale/tun interfaces
        ip_bin = shutil.which("ip") or "/system/bin/ip"
        if os.path.exists(ip_bin):
            try:
                res = subprocess.run(
                    [ip_bin, "-4", "addr", "show"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if "inet 100." in line:
                            m = re.search(r"inet\s+(100\.\d{1,3}\.\d{1,3}\.\d{1,3})", line)
                            if m:
                                return m.group(1)
            except Exception:
                pass

        # 3. Fallback to ifconfig
        ifconfig_bin = shutil.which("ifconfig")
        if ifconfig_bin:
            try:
                res = subprocess.run([ifconfig_bin], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    m = re.search(r"inet\s+(?:addr:)?(100\.\d{1,3}\.\d{1,3}\.\d{1,3})", res.stdout)
                    if m:
                        return m.group(1)
            except Exception:
                pass

        return None

    @staticmethod
    def get_magicdns_name() -> Optional[str]:
        """Fetch node's Tailscale MagicDNS FQDN (e.g. node.tailnet.ts.net)."""
        ts_bin = shutil.which("tailscale") or "/data/data/com.termux/files/usr/bin/tailscale"
        if os.path.exists(ts_bin):
            try:
                res = subprocess.run([ts_bin, "status", "--json"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout.strip():
                    data = json.loads(res.stdout)
                    dns = data.get("Self", {}).get("DNSName", "").rstrip(".")
                    if dns:
                        return dns
            except Exception:
                pass
        return None

    @classmethod
    def get_tailscale_serve_status(cls) -> Dict[str, Any]:
        """Query active Tailscale Serve reverse-proxy configuration."""
        ts_bin = shutil.which("tailscale") or "/data/data/com.termux/files/usr/bin/tailscale"
        if not os.path.exists(ts_bin):
            return {"supported": False, "status": "unavailable", "message": "tailscale CLI not found"}

        try:
            res = subprocess.run([ts_bin, "serve", "status", "--json"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    data = json.loads(res.stdout)
                    return {"supported": True, "active": True, "config": data}
                except json.JSONDecodeError:
                    pass

            # Fallback to plain text status
            res_txt = subprocess.run([ts_bin, "serve", "status"], capture_output=True, text=True, timeout=3)
            out = res_txt.stdout.strip()
            is_active = bool(out and "No serve config" not in out)
            return {"supported": True, "active": is_active, "output": out}
        except Exception as exc:
            return {"supported": True, "status": "error", "error": str(exc)}

    @classmethod
    def configure_tailscale_serve(cls, target_port: int = 5000) -> Dict[str, Any]:
        """
        Configure Tailscale's built-in reverse proxy with automatic HTTPS encryption:
        tailscale serve --bg <port>
        """
        ts_bin = shutil.which("tailscale") or "/data/data/com.termux/files/usr/bin/tailscale"
        if not os.path.exists(ts_bin):
            return {
                "supported": False,
                "status": "failed",
                "message": "tailscale CLI not found in PATH or Termux prefix.",
            }

        try:
            res = subprocess.run(
                [ts_bin, "serve", "--bg", str(target_port)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            success = res.returncode == 0
            magicdns = cls.get_magicdns_name()
            https_url = f"https://{magicdns}/dashboard" if magicdns else None

            return {
                "supported": True,
                "status": "success" if success else "failed",
                "target_port": target_port,
                "magicdns": magicdns,
                "https_url": https_url,
                "output": res.stdout.strip() or res.stderr.strip(),
            }
        except Exception as exc:
            logger.error("Failed to configure tailscale serve: %s", exc)
            return {"supported": True, "status": "error", "error": str(exc)}

    @classmethod
    def reset_tailscale_serve(cls) -> Dict[str, Any]:
        """Reset Tailscale Serve configuration."""
        ts_bin = shutil.which("tailscale") or "/data/data/com.termux/files/usr/bin/tailscale"
        if not os.path.exists(ts_bin):
            return {"supported": False, "status": "unavailable"}

        try:
            res = subprocess.run([ts_bin, "serve", "reset"], capture_output=True, text=True, timeout=5)
            return {"supported": True, "status": "reset", "output": res.stdout.strip() or res.stderr.strip()}
        except Exception as exc:
            return {"supported": True, "status": "error", "error": str(exc)}

    @staticmethod
    def get_local_ip() -> str:
        """Resolve primary local Wi-Fi / LAN IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

        # Fallback to hostname
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"

    @classmethod
    def get_network_summary(cls, port: int = 5000) -> Dict[str, Any]:
        """Aggregate all discovery parameters and client connection URLs."""
        local_ip = cls.get_local_ip()
        tailscale_ip = cls.get_tailscale_ip()
        magicdns = cls.get_magicdns_name()
        serve_status = cls.get_tailscale_serve_status()

        urls: Dict[str, str] = {
            "local_http": f"http://{local_ip}:{port}/",
            "local_dashboard": f"http://{local_ip}:{port}/dashboard",
            "local_ws": f"ws://{local_ip}:{port}/ws",
        }

        scrcpy: Dict[str, str] = {
            "local": f"scrcpy --tcpip={local_ip}:5555",
        }

        if tailscale_ip:
            urls["tailscale_http"] = f"http://{tailscale_ip}:{port}/"
            urls["tailscale_dashboard"] = f"http://{tailscale_ip}:{port}/dashboard"
            urls["tailscale_ws"] = f"ws://{tailscale_ip}:{port}/ws"
            scrcpy["tailscale"] = f"scrcpy --tcpip={tailscale_ip}:5555"

        https_url = None
        if magicdns:
            https_url = f"https://{magicdns}/dashboard"
            urls["tailscale_https_dashboard"] = https_url

        return {
            "local_ip": local_ip,
            "tailscale_ip": tailscale_ip,
            "magicdns": magicdns,
            "port": port,
            "urls": urls,
            "scrcpy_commands": scrcpy,
            "tailscale_connected": tailscale_ip is not None,
            "tailscale_serve_active": serve_status.get("active", False),
            "tailscale_https_url": https_url,
        }

    @classmethod
    def print_startup_banner(cls, port: int = 5000):
        """Print formatted terminal banner with connection links."""
        info = cls.get_network_summary(port)
        banner = [
            "",
            "┌─────────────────────────────────────────────────────────────┐",
            "│             PROJECT ZIA - NETWORK DISCOVERY                 │",
            "├─────────────────────────────────────────────────────────────┤",
            f"│  Local Wi-Fi IP:    {info['local_ip']:<40}│",
            f"│  Dashboard (Local): {info['urls']['local_dashboard']:<40}│",
            f"│  WebSocket (Local): {info['urls']['local_ws']:<40}│",
        ]

        if info["tailscale_ip"]:
            banner.extend([
                "├─────────────────────────────────────────────────────────────┤",
                f"│  Tailscale IP:      {info['tailscale_ip']:<40}│",
                f"│  Dashboard (VPN):   {info['urls']['tailscale_dashboard']:<40}│",
            ])
            if info["magicdns"]:
                banner.append(f"│  MagicDNS FQDN:     {info['magicdns']:<40}│")
                banner.append(f"│  Tailscale HTTPS:   {info['urls']['tailscale_https_dashboard']:<40}│")
            banner.extend([
                "├─────────────────────────────────────────────────────────────┤",
                f"│  scrcpy (Wireless): {info['scrcpy_commands']['tailscale']:<40}│",
            ])
        else:
            banner.extend([
                "├─────────────────────────────────────────────────────────────┤",
                f"│  Tailscale:         Not Connected                           │",
                f"│  scrcpy (Wireless): {info['scrcpy_commands']['local']:<40}│",
            ])

        banner.append("└─────────────────────────────────────────────────────────────┘")
        print("\n".join(banner))
        print("\n".join(banner))

