"""
Project Zia - Pure-Python AI Companion Brain (Gemini REST Engine)
Routes natural language prompts through Google's Gemini API with automated tool-calling
to interact directly with local SQLite databases (habits, finance, system status) and Xperia hardware.

Designed specifically for Termux / aarch64 with ZERO Rust/C++ compilation bottlenecks.
Uses native Python standard library + requests (no pydantic-core, no maturin, no compiler required).
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import requests

logger = logging.getLogger("project-zia.brain")

GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


class ZiaBrain:
    """Conversational AI core with automated function-calling via direct Gemini REST API."""

    def __init__(self, habits_db=None, finance_db=None, system_telemetry_getter=None,
                 telegram_sender=None, event_broadcaster=None):
        self.habits_db = habits_db
        self.finance_db = finance_db
        self.system_telemetry_getter = system_telemetry_getter
        self.telegram_sender = telegram_sender
        self.event_broadcaster = event_broadcaster

        self.api_key = None
        self.model_name = os.getenv("ZIA_AI_MODEL", "gemini-2.5-flash")
        self.client = None
        self.tool_handlers: Dict[str, Any] = {}
        self.tool_declarations: List[Dict[str, Any]] = []

        self._init_tools()
        self._init_client()

    def _reload_env(self):
        """Dynamically reload .env from disk so changes apply immediately without daemon restarts."""
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.isfile(env_path):
            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=env_path, override=True)
            except Exception:
                pass

            # Direct fallback parser to handle quotes, inline comments, or spaces
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip()
                            if " #" in v:
                                v = v.split(" #", 1)[0].strip()
                            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                                v = v[1:-1].strip()
                            if k and v:
                                os.environ[k] = v
            except Exception as e:
                logger.warning("Direct .env read warning: %s", e)

    def _init_client(self):
        """Validate API key and initialize client status."""
        self._reload_env()
        raw_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if raw_key:
            raw_key = raw_key.strip().strip("'\"")
            if " #" in raw_key:
                raw_key = raw_key.split(" #", 1)[0].strip()

        # Check for placeholder or empty key
        if not raw_key or raw_key.startswith("AIzaSyYour") or "your-" in raw_key.lower():
            logger.info("GEMINI_API_KEY not configured or is a placeholder. AI brain is in standby mode.")
            self.client = None
            self.api_key = None
            return

        self.api_key = raw_key
        self.model_name = os.getenv("ZIA_AI_MODEL", "gemini-2.5-flash")
        self.client = True
        logger.info("Gemini AI REST Engine ready with model: %s (API key length: %d)", self.model_name, len(self.api_key))

    def _init_tools(self):
        """Define Python function tools and their JSON schemas for Gemini function calling."""

        # ----------------------------------------------------------------------
        # Tool implementations
        # ----------------------------------------------------------------------
        def get_habits_status(**kwargs) -> str:
            if not self.habits_db:
                return "Habits database not connected."
            habits = self.habits_db.list_habits(include_inactive=False)
            if not habits:
                return "No habits currently tracked."
            res = []
            for h in habits:
                res.append(
                    f"Habit #{h['id']}: '{h['name']}' ({h['category']}) - "
                    f"Target: {h['target_count']} {h['unit']}/{h['frequency']} | "
                    f"Logged today: {h.get('completed_today', 0)} times"
                )
            return "\n".join(res)

        def create_habit(name: str, category: str = "general", frequency: str = "daily",
                         target_count: int = 1, unit: str = "times", **kwargs) -> str:
            if not self.habits_db:
                return "Habits database not connected."
            habit_id = self.habits_db.create_habit(name, category, frequency, target_count, unit)
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("habit_created", {
                    "habit_id": habit_id, "name": name, "category": category
                })
            return f"Habit '{name}' created successfully with ID {habit_id}."

        def log_habit_progress(habit_name_or_id: str, count: int = 1, notes: str = "", **kwargs) -> str:
            if not self.habits_db:
                return "Habits database not connected."

            target_id = None
            if str(habit_name_or_id).isdigit():
                target_id = int(habit_name_or_id)
            else:
                habits = self.habits_db.list_habits(include_inactive=False)
                for h in habits:
                    if str(habit_name_or_id).lower() in h["name"].lower():
                        target_id = h["id"]
                        break

            if not target_id:
                return f"Could not find any active habit matching '{habit_name_or_id}'."

            log_id = self.habits_db.log_habit(target_id, count=count, notes=notes)
            habit = self.habits_db.get_habit(target_id)
            name = habit["name"] if habit else f"#{target_id}"

            if self.event_broadcaster:
                self.event_broadcaster.broadcast("habit_logged", {
                    "habit_id": target_id, "name": name, "count": count, "log_id": log_id
                })
            return f"Logged progress for '{name}': +{count}. (Log ID: {log_id})"

        def record_financial_expense(amount: float, category: str, description: str = "", **kwargs) -> str:
            if not self.finance_db:
                return "Finance database not connected."
            tx_id = self.finance_db.add_transaction(
                tx_type="expense", category=category, amount=float(amount), description=description
            )
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("finance_transaction", {
                    "type": "expense", "amount": float(amount), "category": category, "tx_id": tx_id
                })
            return f"Expense recorded: ${float(amount):.2f} under '{category}' ({description}). Tx ID: {tx_id}."

        def record_financial_income(amount: float, category: str, description: str = "", **kwargs) -> str:
            if not self.finance_db:
                return "Finance database not connected."
            tx_id = self.finance_db.add_transaction(
                tx_type="income", category=category, amount=float(amount), description=description
            )
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("finance_transaction", {
                    "type": "income", "amount": float(amount), "category": category, "tx_id": tx_id
                })
            return f"Income recorded: +${float(amount):.2f} under '{category}' ({description}). Tx ID: {tx_id}."

        def get_financial_summary(**kwargs) -> str:
            if not self.finance_db:
                return "Finance database not connected."
            summary = self.finance_db.get_summary()
            cats = [f"- {c['category']}: ${c['total_spent']:.2f}" for c in summary.get("expense_categories", [])]
            cats_str = "\n".join(cats) if cats else "None"
            return (
                f"Financial Summary:\n"
                f"Total Income: ${summary['total_income']:.2f}\n"
                f"Total Expenses: ${summary['total_expense']:.2f}\n"
                f"Net Balance: ${summary['net_balance']:.2f}\n"
                f"Top Expense Categories:\n{cats_str}"
            )

        def get_device_health(**kwargs) -> str:
            if not self.system_telemetry_getter:
                return "Telemetry getter not available."
            telem = self.system_telemetry_getter()
            bat = telem.get("battery", {})
            mem = telem.get("memory", {})
            load = telem.get("load_average", [])
            storage = telem.get("storage", {})
            return (
                f"Device Health (Sony Xperia 1 III):\n"
                f"Battery: {bat.get('percentage', 'N/A')}% ({bat.get('status', 'N/A')})\n"
                f"Memory Free: {mem.get('MemAvailable', 'N/A')} MB / Total: {mem.get('MemTotal', 'N/A')} MB\n"
                f"Load Average: {load}\n"
                f"Storage Free: {storage.get('free_gb', 'N/A')} GB ({storage.get('free_percent', 'N/A')}%)\n"
                f"Uptime: {telem.get('uptime_seconds', 0):.1f}s"
            )

        def send_telegram_message(message: str, **kwargs) -> str:
            if not self.telegram_sender:
                return "Telegram bot not configured."
            success = self.telegram_sender(message)
            return "Telegram message sent successfully." if success else "Failed to send Telegram message."

        def toggle_flashlight(state: str = "on", **kwargs) -> str:
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.toggle_torch(action=state)
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("hardware_torch", res)
            if not res.get("supported"):
                return f"Flashlight control unavailable: {res.get('message')}"
            return f"Flashlight turned {res.get('torch_state')} successfully."

        def take_camera_photo(camera_id: int = 0, **kwargs) -> str:
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.take_camera_photo(camera_id=int(camera_id))
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("hardware_photo", res)
            if not res.get("supported"):
                return f"Camera capture unavailable: {res.get('message')}"
            return f"Captured photo from camera #{camera_id} saved to: {res.get('filename')} ({res.get('size_bytes')} bytes)."

        def get_device_location(**kwargs) -> str:
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.get_location()
            if not res.get("supported"):
                return f"GPS location unavailable: {res.get('message')}"
            loc = res.get("location", {})
            return f"Device Location: Latitude {loc.get('latitude')}, Longitude {loc.get('longitude')}, Provider: {loc.get('provider')}."

        def read_recent_sms(limit: int = 5, **kwargs) -> str:
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.get_recent_sms(limit=int(limit))
            if not res.get("supported"):
                return f"SMS reading unavailable: {res.get('message')}"
            msgs = res.get("messages", [])
            if not msgs:
                return "No recent SMS messages found in inbox."
            formatted = [f"From {m.get('number')}: '{m.get('body')}' ({m.get('received')})" for m in msgs]
            return "\n".join(formatted)

        def send_sms_message(phone_number: str, message: str, **kwargs) -> str:
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.send_sms(phone_number=str(phone_number), message=str(message))
            if not res.get("supported"):
                return f"SMS sending unavailable: {res.get('message')}"
            return f"SMS transmitted to {phone_number} successfully."

        def vibrate_device(duration_ms: int = 500, **kwargs) -> str:
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.vibrate(duration_ms=int(duration_ms))
            if not res.get("supported"):
                return f"Vibration unavailable: {res.get('message')}"
            return f"Device vibrated for {duration_ms}ms."

        self.tool_handlers = {
            "get_habits_status": get_habits_status,
            "create_habit": create_habit,
            "log_habit_progress": log_habit_progress,
            "record_financial_expense": record_financial_expense,
            "record_financial_income": record_financial_income,
            "get_financial_summary": get_financial_summary,
            "get_device_health": get_device_health,
            "send_telegram_message": send_telegram_message,
            "toggle_flashlight": toggle_flashlight,
            "take_camera_photo": take_camera_photo,
            "get_device_location": get_device_location,
            "read_recent_sms": read_recent_sms,
            "send_sms_message": send_sms_message,
            "vibrate_device": vibrate_device,
        }

        # ----------------------------------------------------------------------
        # Tool declarations in Gemini OpenAPI format
        # ----------------------------------------------------------------------
        self.tool_declarations = [
            {
                "name": "get_habits_status",
                "description": "Retrieve the user's active habits and completion counts for today.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "create_habit",
                "description": "Create a new habit to track.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING", "description": "Name of habit (e.g. Drink Water)"},
                        "category": {"type": "STRING", "description": "Category (health, fitness, study)"},
                        "frequency": {"type": "STRING", "description": "daily or weekly"},
                        "target_count": {"type": "INTEGER", "description": "Target per frequency period"},
                        "unit": {"type": "STRING", "description": "Unit of measurement (times, glasses, pages)"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "log_habit_progress",
                "description": "Log completion or increment count for a habit by name or ID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "habit_name_or_id": {"type": "STRING", "description": "Habit name or numeric ID"},
                        "count": {"type": "INTEGER", "description": "Amount completed to log"},
                        "notes": {"type": "STRING", "description": "Optional notes"},
                    },
                    "required": ["habit_name_or_id"],
                },
            },
            {
                "name": "record_financial_expense",
                "description": "Record a spending/expense transaction in the finance database.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "amount": {"type": "NUMBER", "description": "Amount spent"},
                        "category": {"type": "STRING", "description": "Category (food, transport, server, tech)"},
                        "description": {"type": "STRING", "description": "Brief description of purchase"},
                    },
                    "required": ["amount", "category"],
                },
            },
            {
                "name": "record_financial_income",
                "description": "Record an income transaction in the finance database.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "amount": {"type": "NUMBER", "description": "Amount earned"},
                        "category": {"type": "STRING", "description": "Category (salary, freelance, gift)"},
                        "description": {"type": "STRING", "description": "Brief description of income"},
                    },
                    "required": ["amount", "category"],
                },
            },
            {
                "name": "get_financial_summary",
                "description": "Retrieve current financial summary: total income, total expense, balance.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "get_device_health",
                "description": "Get Sony Xperia 1 III system vitals: battery percentage, memory, CPU load, and storage.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "send_telegram_message",
                "description": "Send an alert or message to the user via Telegram.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "message": {"type": "STRING", "description": "Message content to send"},
                    },
                    "required": ["message"],
                },
            },
            {
                "name": "toggle_flashlight",
                "description": "Turn the Sony Xperia torch / flashlight on or off.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "state": {"type": "STRING", "description": "'on' or 'off'"},
                    },
                },
            },
            {
                "name": "take_camera_photo",
                "description": "Capture a physical photo using the phone's cameras.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "camera_id": {"type": "INTEGER", "description": "0 = rear camera, 1 = front camera"},
                    },
                },
            },
            {
                "name": "get_device_location",
                "description": "Retrieve current GPS latitude, longitude, and provider from the phone.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "read_recent_sms",
                "description": "Read recent incoming text messages from the cellular inbox.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "limit": {"type": "INTEGER", "description": "Number of messages to read (1-10)"},
                    },
                },
            },
            {
                "name": "send_sms_message",
                "description": "Send an SMS text message using the Xperia cellular connection.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "phone_number": {"type": "STRING", "description": "Destination phone number"},
                        "message": {"type": "STRING", "description": "Text message content"},
                    },
                    "required": ["phone_number", "message"],
                },
            },
            {
                "name": "vibrate_device",
                "description": "Trigger physical haptic vibration motor on the Xperia device.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "duration_ms": {"type": "INTEGER", "description": "Vibration duration in milliseconds"},
                    },
                },
            },
        ]

    def process_query(self, user_prompt: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        Process user prompt through Gemini REST API with automated multi-turn tool calling.
        """
        if not self.client:
            self._init_client()

        if not self.client or not self.api_key:
            return self._fallback_rule_response(user_prompt)

        system_instruction = (
            "You are Zia, an ultra-responsive, intelligent personal AI companion running "
            "as a 24/7 background daemon on an unrooted Sony Xperia 1 III (Android 13, Termux).\n"
            "You have direct access to tools that manage the user's habits (habits.db), "
            "finances (finance.db), system diagnostics (battery, memory, CPU load), and Telegram alerts.\n"
            "When the user mentions habits, spending, hardware actions (torch, photo, location), invoke tools.\n"
            "Keep your tone sharp, helpful, concise, and technically accurate. Avoid fluff."
        )

        contents = []

        # Add history
        if conversation_history:
            for msg in conversation_history[-6:]:
                role = "user" if msg.get("role") in ("user", "human") else "model"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg.get("content", "")}]
                })

        # Add current user prompt
        contents.append({
            "role": "user",
            "parts": [{"text": user_prompt}]
        })

        url = GEMINI_API_ENDPOINT.format(model=self.model_name, key=self.api_key)

        headers = {"Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "system_instruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": contents,
            "tools": [{"function_declarations": self.tool_declarations}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 1024,
            }
        }

        # Multi-turn tool execution loop (up to 5 turns)
        try:
            for _ in range(5):
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                if resp.status_code != 200:
                    err_msg = f"Gemini API HTTP {resp.status_code}: {resp.text}"
                    logger.error(err_msg)
                    return {
                        "status": "error",
                        "error": err_msg,
                        "fallback": self._fallback_rule_response(user_prompt)["response"]
                    }

                resp_data = resp.json()
                candidates = resp_data.get("candidates", [])
                if not candidates:
                    break

                first_cand = candidates[0]
                cand_content = first_cand.get("content", {})
                parts = cand_content.get("parts", [])

                # Check if Gemini requested a function call
                has_function_call = False
                for part in parts:
                    if "functionCall" in part:
                        has_function_call = True
                        fn_call = part["functionCall"]
                        fn_name = fn_call.get("name")
                        fn_args = fn_call.get("args", {})

                        logger.info("Gemini invoking tool: %s with args: %s", fn_name, fn_args)
                        handler = self.tool_handlers.get(fn_name)
                        if handler:
                            try:
                                fn_result = handler(**fn_args)
                            except Exception as fn_err:
                                fn_result = f"Tool execution failed: {fn_err}"
                        else:
                            fn_result = f"Unknown tool: {fn_name}"

                        # Append model's functionCall turn
                        payload["contents"].append({
                            "role": "model",
                            "parts": [{"functionCall": fn_call}]
                        })

                        # Append function response turn
                        payload["contents"].append({
                            "role": "function",
                            "parts": [{
                                "functionResponse": {
                                    "name": fn_name,
                                    "response": {"result": str(fn_result)}
                                }
                            }]
                        })
                        break

                if not has_function_call:
                    # Final text response received
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    response_text = "".join(text_parts).strip() or "I completed the request."

                    if self.event_broadcaster:
                        self.event_broadcaster.broadcast("ai_chat", {
                            "user_prompt": user_prompt,
                            "response": response_text,
                            "model": self.model_name,
                        })

                    return {
                        "status": "success",
                        "response": response_text,
                        "model": self.model_name,
                        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                    }

            return {
                "status": "success",
                "response": "Request processed with active tools.",
                "model": self.model_name,
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            }

        except Exception as exc:
            logger.error("Gemini API generation error: %s", exc)
            return {
                "status": "error",
                "error": str(exc),
                "fallback": self._fallback_rule_response(user_prompt)["response"],
            }

    def _fallback_rule_response(self, prompt: str) -> Dict[str, Any]:
        """Graceful offline / key-missing fallback response for standard commands."""
        lowered = prompt.lower().strip()
        response = ""

        if "status" in lowered or "health" in lowered:
            if self.system_telemetry_getter:
                t = self.system_telemetry_getter()
                response = f"[Offline Fallback] System healthy. Battery: {t.get('battery', {}).get('percentage', 'N/A')}%. Uptime: {t.get('uptime_seconds', 0):.0f}s."
            else:
                response = "[Offline Fallback] Server is online."
        elif "habit" in lowered:
            if self.habits_db:
                habits = self.habits_db.list_habits()
                response = f"[Offline Fallback] You have {len(habits)} habits tracked. Add GEMINI_API_KEY to .env for AI processing."
            else:
                response = "Habits DB offline."
        elif "finance" in lowered or "money" in lowered or "expense" in lowered:
            if self.finance_db:
                summary = self.finance_db.get_summary()
                response = f"[Offline Fallback] Total income: ${summary['total_income']}, Total expense: ${summary['total_expense']}, Balance: ${summary['net_balance']}."
            else:
                response = "Finance DB offline."
        else:
            response = (
                "Zia AI brain is currently in standby. To unlock full conversational intelligence "
                "and natural language database controls, please add your Gemini API key to .env:\n"
                "GEMINI_API_KEY=AIzaSy..."
            )

        return {
            "status": "fallback",
            "response": response,
            "message": "Configure GEMINI_API_KEY in .env for full conversational AI.",
        }
