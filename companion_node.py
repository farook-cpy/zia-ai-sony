#!/usr/bin/env python3
"""
Project Zia - Companion Node Agent (Linux Mint / Legacy Laptop)
Lightweight, zero-dependency telemetry worker designed for legacy hardware
(e.g., 2007 MacBook Pro running Linux Mint XFCE).

Collects and reports:
- CPU load averages & Core temperature (°C)
- 500GB HDD storage capacity and free space
- RAM usage and uptime
- Hardware battery or AC adapter status

Synchronizes periodically with the master Sony Xperia 1 III node via HTTP.
"""

import os
import sys
import time
import glob
import json
import socket
import shutil
import platform
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional


def get_cpu_temperatures() -> List[float]:
    """Read hardware thermal sensors from /sys/class/thermal or /sys/class/hwmon."""
    temps = []
    # 1. Thermal zone sysfs
    for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(p, "r") as f:
                val = float(f.read().strip())
                if val > 1000:
                    val /= 1000.0
                if 10.0 <= val <= 115.0:
                    temps.append(round(val, 1))
        except Exception:
            pass

    # 2. Hwmon sysfs
    for p in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        try:
            with open(p, "r") as f:
                val = float(f.read().strip())
                if val > 1000:
                    val /= 1000.0
                if 10.0 <= val <= 115.0:
                    temps.append(round(val, 1))
        except Exception:
            pass

    return sorted(list(set(temps)))


def get_memory_info() -> Dict[str, Any]:
    """Read RAM stats from /proc/meminfo."""
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
                            mem_data[key] = int(val) // 1024  # kB to MB
        except Exception:
            pass
    return mem_data


def get_power_supply() -> Dict[str, Any]:
    """Inspect battery / AC adapter status."""
    info = {"type": "AC / Mains", "status": "online"}
    bat_path = "/sys/class/power_supply"
    if os.path.exists(bat_path):
        for sup in os.listdir(bat_path):
            cap_file = os.path.join(bat_path, sup, "capacity")
            stat_file = os.path.join(bat_path, sup, "status")
            if os.path.exists(cap_file):
                try:
                    with open(cap_file, "r") as f:
                        info["percentage"] = int(f.read().strip())
                    if os.path.exists(stat_file):
                        with open(stat_file, "r") as f:
                            info["status"] = f.read().strip()
                    info["type"] = "Battery"
                    break
                except Exception:
                    pass
    return info


def get_system_uptime() -> float:
    """Read uptime from /proc/uptime."""
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r") as f:
                return float(f.read().split()[0])
        except Exception:
            pass
    return 0.0


def collect_node_telemetry(node_id: str, node_name: str, hdd_path: str = "/") -> Dict[str, Any]:
    """Aggregate hardware vitals from legacy laptop."""
    # Disk usage (500GB HDD)
    disk = {}
    try:
        usage = shutil.disk_usage(hdd_path)
        disk = {
            "path": hdd_path,
            "total_gb": round(usage.total / (1024**3), 2),
            "used_gb": round(usage.used / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "free_percent": round((usage.free / usage.total) * 100, 1),
        }
    except Exception as exc:
        disk["error"] = str(exc)

    temps = get_cpu_temperatures()
    avg_temp = round(sum(temps) / len(temps), 1) if temps else None

    # Load average
    try:
        load_avg = list(os.getloadavg())
    except (OSError, AttributeError):
        load_avg = []

    return {
        "node_id": node_id,
        "node_name": node_name,
        "platform": {
            "hostname": socket.gethostname(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "telemetry": {
            "cpu_temperatures_c": temps,
            "cpu_temp_c": avg_temp,
            "load_average": load_avg,
            "memory": get_memory_info(),
            "hdd_storage": disk,
            "power": get_power_supply(),
            "uptime_seconds": round(get_system_uptime(), 1),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def send_telemetry_heartbeat(server_url: str, payload: Dict[str, Any], timeout: int = 5) -> bool:
    """Post telemetry heartbeat JSON to Zia master node."""
    endpoint = f"{server_url.rstrip('/')}/api/nodes/heartbeat"
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data_bytes,
        headers={"Content-Type": "application/json", "User-Agent": "ZiaCompanionNode/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.URLError as err:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Connection failed ({endpoint}): {err}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Unexpected error: {exc}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Zia Companion Node (Linux Mint / Legacy Hardware)")
    parser.add_argument("--server", default=os.getenv("ZIA_MASTER_URL", "http://127.0.0.1:5000"),
                        help="Master Zia server URL (default: http://127.0.0.1:5000 or Tailscale URL)")
    parser.add_argument("--node-name", default=os.getenv("ZIA_NODE_NAME", "Linux Mint MacBook Pro (2007)"),
                        help="Friendly name for this companion node")
    parser.add_argument("--node-id", default=os.getenv("ZIA_NODE_ID", socket.gethostname().lower()),
                        help="Unique ID for this companion node")
    parser.add_argument("--hdd-path", default="/", help="Mount point of primary HDD (default: /)")
    parser.add_argument("--interval", type=int, default=10, help="Heartbeat interval in seconds (default: 10)")
    parser.add_argument("--once", action="store_true", help="Collect telemetry once, print, and exit")
    args = parser.parse_args()

    print("=" * 65)
    print("  PROJECT ZIA - COMPANION NODE AGENT")
    print(f"  Node ID:    {args.node_id}")
    print(f"  Node Name:  {args.node_name}")
    print(f"  Target:     {args.server}")
    print(f"  Interval:   {args.interval}s")
    print("=" * 65)

    if args.once:
        data = collect_node_telemetry(args.node_id, args.node_name, args.hdd_path)
        print(json.dumps(data, indent=2))
        return

    print(f"Starting telemetry heartbeat stream to {args.server}...")
    success_count = 0
    fail_count = 0

    while True:
        try:
            payload = collect_node_telemetry(args.node_id, args.node_name, args.hdd_path)
            ok = send_telemetry_heartbeat(args.server, payload)
            if ok:
                success_count += 1
                temp_str = f"{payload['telemetry']['cpu_temp_c']}°C" if payload['telemetry']['cpu_temp_c'] else "N/A"
                free_hdd = f"{payload['telemetry']['hdd_storage'].get('free_gb', '?')}GB"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ Heartbeat synced (Temp: {temp_str} | HDD Free: {free_hdd} | Syncs: {success_count})")
            else:
                fail_count += 1
        except KeyboardInterrupt:
            print("\nShutting down companion node agent.")
            break
        except Exception as exc:
            print(f"Loop exception: {exc}", file=sys.stderr)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
