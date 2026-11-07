"""
Project Zia - Xperia Hardware Tool-Calling & Termux API Integration
Interacts directly with Sony Xperia 1 III hardware via native Termux API bindings:
torch, camera, GPS location, SMS, vibration, volume, and clipboard.
"""

import os
import json
import shutil
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger("project-zia.hardware")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

os.makedirs(PHOTOS_DIR, exist_ok=True)


class XperiaHardware:
    """Native Termux API bindings tailored for Sony Xperia 1 III hardware."""

    @staticmethod
    def _find_binary(name: str) -> Optional[str]:
        """Locate Termux API tool in standard PATH or Termux prefix."""
        cmd = shutil.which(name)
        if cmd:
            return cmd
        termux_path = f"/data/data/com.termux/files/usr/bin/{name}"
        if os.path.exists(termux_path):
            return termux_path
        return None

    @classmethod
    def is_supported(cls, name: str) -> bool:
        return cls._find_binary(name) is not None

    # --------------------------------------------------------------------------
    # Flashlight / Torch Control
    # --------------------------------------------------------------------------
    @classmethod
    def toggle_torch(cls, action: str = "on") -> Dict[str, Any]:
        """Toggle device flashlight (on/off)."""
        bin_path = cls._find_binary("termux-torch")
        if not bin_path:
            return {
                "supported": False,
                "status": "unavailable",
                "message": "termux-torch not found. Install via: pkg install termux-api",
            }

        state = "on" if action.lower() in ("on", "true", "1") else "off"
        try:
            res = subprocess.run([bin_path, state], capture_output=True, text=True, timeout=3)
            success = res.returncode == 0
            return {
                "supported": True,
                "status": "success" if success else "failed",
                "torch_state": state,
                "output": res.stdout.strip() or res.stderr.strip(),
            }
        except Exception as exc:
            logger.error("Failed to toggle torch: %s", exc)
            return {"supported": True, "status": "error", "error": str(exc)}

    # --------------------------------------------------------------------------
    # Camera Snapshot
    # --------------------------------------------------------------------------
    @classmethod
    def take_camera_photo(cls, camera_id: int = 0, custom_filename: Optional[str] = None) -> Dict[str, Any]:
        """
        Trigger Xperia camera photo capture remotely.
        camera_id: 0 = Rear Camera (Zeiss Optic), 1 = Front Selfie Camera.
        """
        bin_path = cls._find_binary("termux-camera-photo")
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = custom_filename or f"xperia_cam{camera_id}_{timestamp}.jpg"
        out_path = os.path.join(PHOTOS_DIR, filename)

        if not bin_path:
            # Fallback simulated capture on non-termux systems
            return {
                "supported": False,
                "status": "unavailable",
                "message": "termux-camera-photo not found. Available on Sony Xperia Termux with termux-api.",
                "target_path": out_path,
            }

        try:
            res = subprocess.run(
                [bin_path, "-c", str(camera_id), out_path],
                capture_output=True,
                text=True,
                timeout=12,
            )
            file_exists = os.path.exists(out_path) and os.path.getsize(out_path) > 0
            return {
                "supported": True,
                "status": "success" if file_exists else "failed",
                "camera_id": camera_id,
                "filename": filename,
                "filepath": out_path,
                "size_bytes": os.path.getsize(out_path) if file_exists else 0,
                "output": res.stdout.strip() or res.stderr.strip(),
            }
        except Exception as exc:
            logger.error("Camera photo capture failed: %s", exc)
            return {"supported": True, "status": "error", "error": str(exc)}

    # --------------------------------------------------------------------------
    # Device GPS / Network Location
    # --------------------------------------------------------------------------
    @classmethod
    def get_location(cls, provider: str = "gps", request_type: str = "last") -> Dict[str, Any]:
        """
        Retrieve GPS/Network location fix from Xperia sensors.
        provider: 'gps', 'network', or 'passive'.
        request_type: 'last' (fast cached fix) or 'once' (fresh fix).
        """
        bin_path = cls._find_binary("termux-location")
        if not bin_path:
            return {
                "supported": False,
                "status": "unavailable",
                "message": "termux-location not found.",
            }

        try:
            res = subprocess.run(
                [bin_path, "-p", provider, "-r", request_type],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0 and res.stdout.strip():
                try:
                    data = json.loads(res.stdout.strip())
                    return {"supported": True, "status": "success", "location": data}
                except json.JSONDecodeError:
                    return {"supported": True, "status": "raw", "output": res.stdout.strip()}
            return {"supported": True, "status": "no_fix", "output": res.stderr.strip()}
        except Exception as exc:
            logger.error("Location query failed: %s", exc)
            return {"supported": True, "status": "error", "error": str(exc)}

    # --------------------------------------------------------------------------
    # SMS Messaging
    # --------------------------------------------------------------------------
    @classmethod
    def get_recent_sms(cls, limit: int = 5) -> Dict[str, Any]:
        """Fetch recent inbox SMS messages from device."""
        bin_path = cls._find_binary("termux-sms-list")
        if not bin_path:
            return {
                "supported": False,
                "status": "unavailable",
                "message": "termux-sms-list not found.",
            }

        try:
            res = subprocess.run(
                [bin_path, "-l", str(limit), "-t", "inbox"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if res.returncode == 0 and res.stdout.strip():
                try:
                    msgs = json.loads(res.stdout.strip())
                    return {"supported": True, "status": "success", "messages": msgs, "count": len(msgs)}
                except json.JSONDecodeError:
                    pass
            return {"supported": True, "status": "empty", "messages": [], "count": 0}
        except Exception as exc:
            logger.error("SMS list query failed: %s", exc)
            return {"supported": True, "status": "error", "error": str(exc)}

    @classmethod
    def send_sms(cls, phone_number: str, message: str) -> Dict[str, Any]:
        """Transmit an SMS text message directly through cellular SIM."""
        bin_path = cls._find_binary("termux-sms-send")
        if not bin_path:
            return {
                "supported": False,
                "status": "unavailable",
                "message": "termux-sms-send not found.",
            }

        try:
            res = subprocess.run(
                [bin_path, "-n", phone_number, message],
                capture_output=True,
                text=True,
                timeout=8,
            )
            success = res.returncode == 0
            return {
                "supported": True,
                "status": "success" if success else "failed",
                "recipient": phone_number,
                "message_length": len(message),
                "output": res.stdout.strip() or res.stderr.strip(),
            }
        except Exception as exc:
            logger.error("SMS send failed: %s", exc)
            return {"supported": True, "status": "error", "error": str(exc)}

    # --------------------------------------------------------------------------
    # Haptic Vibration & Feedback
    # --------------------------------------------------------------------------
    @classmethod
    def vibrate(cls, duration_ms: int = 500) -> Dict[str, Any]:
        """Trigger device haptic vibration motor."""
        bin_path = cls._find_binary("termux-vibrate")
        if not bin_path:
            return {
                "supported": False,
                "status": "unavailable",
                "message": "termux-vibrate not found.",
            }

        try:
            res = subprocess.run(
                [bin_path, "-d", str(duration_ms)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return {"supported": True, "status": "success", "duration_ms": duration_ms}
        except Exception as exc:
            return {"supported": True, "status": "error", "error": str(exc)}

    # --------------------------------------------------------------------------
    # Clipboard Integration
    # --------------------------------------------------------------------------
    @classmethod
    def get_clipboard(cls) -> Dict[str, Any]:
        """Read text currently in the Android clipboard."""
        bin_path = cls._find_binary("termux-clipboard-get")
        if not bin_path:
            return {"supported": False, "status": "unavailable", "text": ""}

        try:
            res = subprocess.run([bin_path], capture_output=True, text=True, timeout=2)
            return {"supported": True, "status": "success", "text": res.stdout}
        except Exception as exc:
            return {"supported": True, "status": "error", "error": str(exc)}

    @classmethod
    def set_clipboard(cls, text: str) -> Dict[str, Any]:
        """Copy text into the Android clipboard."""
        bin_path = cls._find_binary("termux-clipboard-set")
        if not bin_path:
            return {"supported": False, "status": "unavailable"}

        try:
            res = subprocess.run([bin_path, text], capture_output=True, text=True, timeout=2)
            return {"supported": True, "status": "success", "text": text}
        except Exception as exc:
            return {"supported": True, "status": "error", "error": str(exc)}

    # --------------------------------------------------------------------------
    # Hardware Status Diagnostic
    # --------------------------------------------------------------------------
    @classmethod
    def get_hardware_diagnostic(cls) -> Dict[str, Any]:
        """Check availability of all Termux API hardware tools."""
        tools = [
            "termux-torch",
            "termux-camera-photo",
            "termux-location",
            "termux-sms-list",
            "termux-sms-send",
            "termux-vibrate",
            "termux-clipboard-get",
            "termux-tts-speak",
            "termux-battery-status",
        ]
        status = {}
        for t in tools:
            status[t] = cls.is_supported(t)

        all_ready = any(status.values())
        return {
            "device": "Sony Xperia 1 III (Android 13 / Termux)",
            "termux_api_installed": all_ready,
            "bindings": status,
            "photos_count": len(os.listdir(PHOTOS_DIR)),
        }

