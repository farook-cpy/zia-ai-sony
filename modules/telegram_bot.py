"""
Project Zia - Telegram Bot & Notification Gateway
Provides a background long-polling Telegram bot to control habits, finances,
system status, voice notes, and receive push notifications from anywhere.
"""

import os
import time
import logging
import threading
from typing import Optional, List, Dict, Any

import requests

logger = logging.getLogger("project-zia.telegram")

# Default hardcoded credentials for zero-friction private operation
HARDCODED_TELEGRAM_TOKEN = "8981971325:AAEHsxHynEQJ42ZrMNyyrtpCA7omi2Ro8Is"
HARDCODED_TELEGRAM_USER = "farookkkk"


class TelegramBotRunner:
    """Lightweight, zero-bloat Telegram bot daemon utilizing raw HTTP polling."""

    def __init__(self, token: Optional[str] = None, allowed_chats: Optional[List[int]] = None,
                 ai_brain=None, habits_db=None, finance_db=None,
                 system_telemetry_getter=None, voice_pipeline=None,
                 event_broadcaster=None):
        raw_token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not raw_token or len(raw_token.strip()) < 15 or "your-" in raw_token.lower():
            raw_token = HARDCODED_TELEGRAM_TOKEN

        self.token = raw_token.strip().strip("'\"")
        self.allowed_chats = allowed_chats or self._parse_allowed_chats()
        self.ai_brain = ai_brain
        self.habits_db = habits_db
        self.finance_db = finance_db
        self.system_telemetry_getter = system_telemetry_getter
        self.voice_pipeline = voice_pipeline
        self.event_broadcaster = event_broadcaster

        self.api_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0

    def _parse_allowed_chats(self) -> List[int]:
        raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
        ids = []
        for x in raw.split(","):
            x = x.strip()
            if x.isdigit() or (x.startswith("-") and x[1:].isdigit()):
                ids.append(int(x))
        return ids

    def _parse_allowed_usernames(self) -> List[str]:
        raw = os.getenv("TELEGRAM_ALLOWED_USERNAMES", "Farookkkk")
        users = [u.strip().lstrip("@").lower() for u in raw.split(",") if u.strip()]
        if HARDCODED_TELEGRAM_USER not in users:
            users.append(HARDCODED_TELEGRAM_USER)
        return users

    def is_configured(self) -> bool:
        return bool(self.token and len(self.token) > 15)

    def is_authorized(self, chat_id: int, username: Optional[str] = None) -> bool:
        if username:
            clean_u = username.lstrip("@").lower()
            allowed_users = self._parse_allowed_usernames()
            if clean_u in allowed_users:
                if chat_id not in self.allowed_chats:
                    self.allowed_chats.append(chat_id)
                    logger.info("Authorized user @%s with primary Chat ID: %d", username, chat_id)
                return True

        if not self.allowed_chats:
            # If no chat IDs specified, allow the first incoming chat and pin it
            self.allowed_chats.append(chat_id)
            logger.info("Automatically authorized primary Telegram chat ID: %d", chat_id)
            return True
        return chat_id in self.allowed_chats

    def send_message(self, text: str, chat_id: Optional[int] = None, parse_mode: str = "Markdown") -> bool:
        """Send a message to a specific chat or all authorized chats."""
        if not self.is_configured():
            return False

        targets = [chat_id] if chat_id else self.allowed_chats
        if not targets:
            logger.warning("No authorized Telegram chat ID available to send message.")
            return False

        success = True
        for cid in targets:
            try:
                payload = {
                    "chat_id": cid,
                    "text": text,
                    "parse_mode": parse_mode,
                }
                res = requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=10)
                if not res.ok:
                    # Retry without markdown if formatting error
                    payload.pop("parse_mode", None)
                    requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=10)
            except Exception as exc:
                logger.error("Failed to send Telegram message to %s: %s", cid, exc)
                success = False

        return success

    def send_voice(self, audio_file_path: str, caption: str = "", chat_id: Optional[int] = None) -> bool:
        """Send an audio/voice message to authorized chat."""
        if not self.is_configured() or not os.path.exists(audio_file_path):
            return False

        targets = [chat_id] if chat_id else self.allowed_chats
        for cid in targets:
            try:
                with open(audio_file_path, "rb") as f:
                    requests.post(
                        f"{self.api_url}/sendVoice",
                        data={"chat_id": cid, "caption": caption},
                        files={"voice": f},
                        timeout=30,
                    )
            except Exception as exc:
                logger.error("Failed to send voice file to %s: %s", cid, exc)
                return False
        return True

    def _handle_command(self, text: str, chat_id: int) -> str:
        """Process slash commands or route directly to Gemini AI brain."""
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""
        args = parts[1:]

        if cmd in ("/start", "/help"):
            return (
                "🤖 *Welcome to Project Zia (Sony Xperia 1 III Node)*\n\n"
                "*Commands:*\n"
                "• `/status` - Device health & database connectivity\n"
                "• `/habits` - View today's habit status\n"
                "• `/log <id_or_name>` - Log habit progress\n"
                "• `/expense <amt> <cat> [desc]` - Record expense\n"
                "• `/income <amt> <cat> [desc]` - Record income\n"
                "• `/summary` - Financial overview\n"
                "• `/tts <text>` - Hear Zia speak as a voice note\n"
                "• `/torch [on/off]` - Toggle flashlight\n"
                "• `/photo [0/1]` - Capture camera snapshot\n"
                "• `/location` - Get GPS coordinates\n"
                "• `/sms` - View recent SMS messages\n"
                "• `/vibrate` - Trigger haptic feedback\n"
                "• `/backup` - Create & upload SQLite backup\n"
                "• Any normal message will be answered by Zia's AI brain!"
            )

        if cmd == "/status":
            if self.system_telemetry_getter:
                t = self.system_telemetry_getter()
                bat = t.get("battery", {})
                mem = t.get("memory", {})
                load = t.get("load_average", [])
                storage = t.get("storage", {})
                return (
                    f"📱 *Sony Xperia 1 III Status*\n"
                    f"🔋 Battery: {bat.get('percentage', 'N/A')}% ({bat.get('status', 'N/A')})\n"
                    f"🧠 Memory Available: {mem.get('MemAvailable', 'N/A')} MB\n"
                    f"⚡ Load Average: {load}\n"
                    f"💾 Storage Free: {storage.get('free_gb', 'N/A')} GB\n"
                    f"⏱️ Uptime: {t.get('uptime_seconds', 0):.0f}s"
                )
            return "Status unavailable."

        if cmd == "/habits":
            if not self.habits_db:
                return "Habits database unavailable."
            habits = self.habits_db.list_habits()
            if not habits:
                return "No habits configured yet."
            lines = ["📋 *Active Habits:*"]
            for h in habits:
                lines.append(f"• *{h['name']}*: {h.get('completed_today', 0)}/{h['target_count']} {h['unit']}")
            return "\n".join(lines)

        if cmd == "/log":
            if not args:
                return "Usage: `/log <habit_id_or_name>`"
            target = args[0]
            if self.ai_brain:
                # Use brain tool dispatch
                res = self.ai_brain.process_query(f"Log 1 count for habit {target}")
                return res.get("response", "Habit logged.")
            return "Brain not initialized."

        if cmd == "/expense":
            if len(args) < 2:
                return "Usage: `/expense <amount> <category> [description]`"
            try:
                amt = float(args[0])
                cat = args[1]
                desc = " ".join(args[2:]) if len(args) > 2 else ""
                if self.finance_db:
                    tx_id = self.finance_db.add_transaction("expense", cat, amt, description=desc)
                    return f"✅ Recorded expense: *${amt:.2f}* for _{cat}_ (`#{tx_id}`)"
            except ValueError:
                return "Amount must be a valid number."

        if cmd == "/income":
            if len(args) < 2:
                return "Usage: `/income <amount> <category> [description]`"
            try:
                amt = float(args[0])
                cat = args[1]
                desc = " ".join(args[2:]) if len(args) > 2 else ""
                if self.finance_db:
                    tx_id = self.finance_db.add_transaction("income", cat, amt, description=desc)
                    return f"✅ Recorded income: *+${amt:.2f}* from _{cat}_ (`#{tx_id}`)"
            except ValueError:
                return "Amount must be a valid number."

        if cmd == "/summary":
            if not self.finance_db:
                return "Finance database unavailable."
            s = self.finance_db.get_summary()
            return (
                f"💰 *Financial Summary*\n"
                f"• Income: *${s['total_income']:.2f}*\n"
                f"• Expenses: *${s['total_expense']:.2f}*\n"
                f"• Net Balance: *${s['net_balance']:.2f}*"
            )

        if cmd == "/tts":
            speech_text = " ".join(args)
            if not speech_text:
                return "Usage: `/tts <text to speak>`"
            if self.voice_pipeline:
                audio_path = self.voice_pipeline.synthesize_to_file(speech_text)
                self.send_voice(audio_path, caption=f"🗣️ {speech_text[:50]}", chat_id=chat_id)
                return "Synthesizing voice note..."
            return "Voice pipeline unavailable."

        if cmd == "/torch":
            from modules.xperia_hardware import XperiaHardware
            action = args[0] if args else "on"
            res = XperiaHardware.toggle_torch(action)
            return f"🔦 Flashlight: {res.get('status')} ({res.get('torch_state', action)})"

        if cmd == "/photo":
            from modules.xperia_hardware import XperiaHardware
            cam_id = int(args[0]) if args and args[0].isdigit() else 0
            res = XperiaHardware.take_camera_photo(camera_id=cam_id)
            if res.get("status") == "success" and os.path.exists(res.get("filepath", "")):
                # Send photo to Telegram
                try:
                    with open(res["filepath"], "rb") as f:
                        requests.post(
                            f"{self.api_url}/sendPhoto",
                            data={"chat_id": chat_id, "caption": f"📸 Photo from Camera #{cam_id}"},
                            files={"photo": f},
                            timeout=30,
                        )
                    return f"📸 Photo captured (Camera #{cam_id})"
                except Exception as e:
                    return f"Photo saved to {res['filename']} but upload failed: {e}"
            return f"Camera capture: {res.get('status')} ({res.get('message', 'done')})"

        if cmd == "/location":
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.get_location()
            if res.get("status") == "success":
                loc = res.get("location", {})
                lat = loc.get("latitude")
                lon = loc.get("longitude")
                # Send native Telegram location
                try:
                    requests.post(
                        f"{self.api_url}/sendLocation",
                        data={"chat_id": chat_id, "latitude": lat, "longitude": lon},
                        timeout=10,
                    )
                except Exception:
                    pass
                return f"📍 Location: `{lat}, {lon}` (Provider: {loc.get('provider')})"
            return f"Location: {res.get('status')} ({res.get('message', 'no fix')})"

        if cmd == "/sms":
            from modules.xperia_hardware import XperiaHardware
            limit = int(args[0]) if args and args[0].isdigit() else 3
            res = XperiaHardware.get_recent_sms(limit=limit)
            if res.get("status") == "success":
                msgs = res.get("messages", [])
                if not msgs:
                    return "No SMS messages found."
                lines = [f"📩 *Recent SMS ({len(msgs)}):*"]
                for m in msgs:
                    lines.append(f"• *{m.get('number')}*: {m.get('body')}")
                return "\n".join(lines)
            return f"SMS query: {res.get('status')} ({res.get('message', 'unavailable')})"

        if cmd == "/vibrate":
            from modules.xperia_hardware import XperiaHardware
            ms = int(args[0]) if args and args[0].isdigit() else 500
            res = XperiaHardware.vibrate(duration_ms=ms)
            return f"📳 Vibrated: {res.get('status')} ({ms}ms)"

        if cmd == "/backup":
            from modules.backup import BackupManager
            res = BackupManager.create_backup(send_to_telegram=True, telegram_bot=self, chat_id=chat_id)
            return f"💾 Backup `{res.get('filename')}` created ({res.get('size_kb')} KB) and uploaded."

        # Any general prompt: route to AI companion brain
        if self.ai_brain:
            res = self.ai_brain.process_query(text)
            return res.get("response", "Request processed.")

        return "Command not recognized. Type /help for available commands."

    def _process_voice_message(self, voice_info: Dict[str, Any], chat_id: int):
        """Download voice note from Telegram, transcribe, and execute with AI brain."""
        file_id = voice_info.get("file_id")
        if not file_id or not self.voice_pipeline:
            self.send_message("Voice processing unavailable.", chat_id)
            return

        try:
            # 1. Get file path
            file_meta = requests.get(f"{self.api_url}/getFile", params={"file_id": file_id}, timeout=10).json()
            if not file_meta.get("ok"):
                self.send_message("Could not retrieve voice file from Telegram.", chat_id)
                return

            file_path = file_meta["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            audio_bytes = requests.get(download_url, timeout=30).content

            # 2. Transcribe & execute
            res = self.voice_pipeline.transcribe_audio(
                audio_bytes,
                mime_type="audio/ogg",
                execute_with_brain=True
            )

            transcription = res.get("transcription", "")
            execution = res.get("execution", {})
            reply = (
                f"🎙️ *Heard:* _{transcription}_\n\n"
                f"{execution.get('response', 'Processed.')}"
            )
            self.send_message(reply, chat_id)

        except Exception as exc:
            logger.error("Error processing incoming Telegram voice message: %s", exc)
            self.send_message(f"Error processing voice message: {exc}", chat_id)

    def _poll_loop(self):
        """Long polling daemon loop."""
        logger.info("Telegram Bot poller started.")
        while self._running:
            try:
                params = {
                    "offset": self._last_update_id + 1,
                    "timeout": 25,
                }
                resp = requests.get(f"{self.api_url}/getUpdates", params=params, timeout=30)
                if not resp.ok:
                    time.sleep(5)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    self._last_update_id = update["update_id"]
                    msg = update.get("message")
                    if not msg:
                        continue

                    chat_id = msg.get("chat", {}).get("id")
                    from_user = msg.get("from", {}).get("username")
                    if not chat_id or not self.is_authorized(chat_id, username=from_user):
                        logger.warning("Ignored message from unauthorized Telegram chat: %s (user: @%s)", chat_id, from_user)
                        continue

                    # Handle incoming voice note
                    if "voice" in msg or "audio" in msg:
                        voice_info = msg.get("voice") or msg.get("audio")
                        self._process_voice_message(voice_info, chat_id)
                        continue

                    # Handle incoming text
                    text = msg.get("text", "").strip()
                    if text:
                        if self.event_broadcaster:
                            self.event_broadcaster.broadcast("telegram_incoming", {
                                "chat_id": chat_id,
                                "text": text,
                            })

                        reply_text = self._handle_command(text, chat_id)
                        if reply_text:
                            self.send_message(reply_text, chat_id)

            except requests.exceptions.RequestException as net_exc:
                logger.debug("Telegram polling network timeout/error: %s", net_exc)
                time.sleep(4)
            except Exception as exc:
                logger.error("Telegram poller unexpected exception: %s", exc)
                time.sleep(5)

    def start(self):
        """Start poller in background daemon thread."""
        if not self.is_configured():
            logger.info("Telegram Bot not configured (TELEGRAM_BOT_TOKEN missing). Gateway inactive.")
            return

        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="zia-telegram-bot")
        self._thread.start()
        logger.info("Telegram Bot gateway initialized successfully.")

    def stop(self):
        self._running = False

