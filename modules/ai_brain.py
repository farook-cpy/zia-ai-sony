"""
Project Zia - AI Companion Brain (google-genai integration)
Routes natural language prompts through Gemini API with automated tool-calling
to interact directly with local SQLite databases (habits, finance, system status).
"""

import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("project-zia.brain")

# Try importing google.genai
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("google-genai library not found. LLM integration disabled.")


class ZiaBrain:
    """Conversational AI core with tool calling for SQLite databases and device status."""

    def __init__(self, habits_db=None, finance_db=None, system_telemetry_getter=None,
                 telegram_sender=None, event_broadcaster=None):
        self.habits_db = habits_db
        self.finance_db = finance_db
        self.system_telemetry_getter = system_telemetry_getter
        self.telegram_sender = telegram_sender
        self.event_broadcaster = event_broadcaster

        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model_name = os.getenv("ZIA_AI_MODEL", "gemini-2.5-flash")
        self.client = None
        self._init_client()

    def _init_client(self):
        if not GENAI_AVAILABLE:
            return
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            logger.info("GEMINI_API_KEY not configured. AI brain is in standby mode.")
            self.client = None
            return

        try:
            self.client = genai.Client(api_key=self.api_key)
            logger.info("Gemini AI Client initialized with model: %s", self.model_name)
        except Exception as exc:
            logger.error("Failed to initialize Gemini Client: %s", exc)
            self.client = None

    def _build_tools(self) -> List[Any]:
        """Construct Python function tools for Gemini automatic function calling."""

        def get_habits_status() -> str:
            """Retrieve the user's active habits and how many times they have been logged today."""
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
                         target_count: int = 1, unit: str = "times") -> str:
            """Create a new habit to track. Name is required."""
            if not self.habits_db:
                return "Habits database not connected."
            habit_id = self.habits_db.create_habit(name, category, frequency, target_count, unit)
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("habit_created", {
                    "habit_id": habit_id, "name": name, "category": category
                })
            return f"Habit '{name}' created successfully with ID {habit_id}."

        def log_habit_progress(habit_name_or_id: str, count: int = 1, notes: str = "") -> str:
            """Log completion or progress for a habit by its name or ID."""
            if not self.habits_db:
                return "Habits database not connected."

            # Find habit ID
            target_id = None
            if habit_name_or_id.isdigit():
                target_id = int(habit_name_or_id)
            else:
                habits = self.habits_db.list_habits(include_inactive=False)
                for h in habits:
                    if habit_name_or_id.lower() in h["name"].lower():
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

        def record_financial_expense(amount: float, category: str, description: str = "") -> str:
            """Record a spending/expense transaction in the finance database."""
            if not self.finance_db:
                return "Finance database not connected."
            tx_id = self.finance_db.add_transaction(
                tx_type="expense",
                category=category,
                amount=amount,
                description=description
            )
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("finance_transaction", {
                    "type": "expense", "amount": amount, "category": category, "tx_id": tx_id
                })
            return f"Expense recorded: ${amount:.2f} under '{category}' ({description}). Tx ID: {tx_id}."

        def record_financial_income(amount: float, category: str, description: str = "") -> str:
            """Record an income transaction in the finance database."""
            if not self.finance_db:
                return "Finance database not connected."
            tx_id = self.finance_db.add_transaction(
                tx_type="income",
                category=category,
                amount=amount,
                description=description
            )
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("finance_transaction", {
                    "type": "income", "amount": amount, "category": category, "tx_id": tx_id
                })
            return f"Income recorded: +${amount:.2f} under '{category}' ({description}). Tx ID: {tx_id}."

        def get_financial_summary() -> str:
            """Retrieve the current total income, total expense, and net balance."""
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

        def get_device_health() -> str:
            """Get Sony Xperia 1 III system telemetry: battery, memory, load average, storage."""
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

        def send_telegram_message(message: str) -> str:
            """Send a notification message to the user via Telegram."""
            if not self.telegram_sender:
                return "Telegram bot not configured."
            success = self.telegram_sender(message)
            return "Telegram message sent successfully." if success else "Failed to send Telegram message."

        def toggle_flashlight(state: str = "on") -> str:
            """Turn the Sony Xperia phone's flashlight / torch on or off ('on' or 'off')."""
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.toggle_torch(action=state)
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("hardware_torch", res)
            if not res.get("supported"):
                return f"Flashlight control unavailable: {res.get('message')}"
            return f"Flashlight turned {res.get('torch_state')} successfully."

        def take_camera_photo(camera_id: int = 0) -> str:
            """Take a photo using the Xperia camera (camera_id: 0 = rear camera, 1 = front camera)."""
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.take_camera_photo(camera_id=camera_id)
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("hardware_photo", res)
            if not res.get("supported"):
                return f"Camera capture unavailable: {res.get('message')}"
            return f"Captured photo from camera #{camera_id} saved to: {res.get('filename')} ({res.get('size_bytes')} bytes)."

        def get_device_location() -> str:
            """Retrieve the current GPS or network geographic coordinates of the Xperia phone."""
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.get_location()
            if not res.get("supported"):
                return f"GPS location unavailable: {res.get('message')}"
            loc = res.get("location", {})
            return f"Device Location: Latitude {loc.get('latitude')}, Longitude {loc.get('longitude')}, Provider: {loc.get('provider')}."

        def read_recent_sms(limit: int = 5) -> str:
            """Read recent incoming SMS messages from the phone."""
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.get_recent_sms(limit=limit)
            if not res.get("supported"):
                return f"SMS reading unavailable: {res.get('message')}"
            msgs = res.get("messages", [])
            if not msgs:
                return "No recent SMS messages found in inbox."
            formatted = [f"From {m.get('number')}: '{m.get('body')}' ({m.get('received')})" for m in msgs]
            return "\n".join(formatted)

        def send_sms_message(phone_number: str, message: str) -> str:
            """Send an SMS text message to a phone number using the cellular SIM."""
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.send_sms(phone_number=phone_number, message=message)
            if not res.get("supported"):
                return f"SMS sending unavailable: {res.get('message')}"
            return f"SMS transmitted to {phone_number} successfully."

        def vibrate_device(duration_ms: int = 500) -> str:
            """Trigger physical haptic vibration motor on the Xperia device."""
            from modules.xperia_hardware import XperiaHardware
            res = XperiaHardware.vibrate(duration_ms=duration_ms)
            if not res.get("supported"):
                return f"Vibration unavailable: {res.get('message')}"
            return f"Device vibrated for {duration_ms}ms."

        return [
            get_habits_status,
            create_habit,
            log_habit_progress,
            record_financial_expense,
            record_financial_income,
            get_financial_summary,
            get_device_health,
            send_telegram_message,
            toggle_flashlight,
            take_camera_photo,
            get_device_location,
            read_recent_sms,
            send_sms_message,
            vibrate_device,
        ]

    def process_query(self, user_prompt: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        Process user prompt through Gemini model with automatic function execution.
        """
        # Re-check API key in case it was loaded dynamically from .env
        if not self.client:
            self._init_client()

        if not self.client:
            return self._fallback_rule_response(user_prompt)

        system_instruction = (
            "You are Zia, an ultra-responsive, intelligent personal AI companion running "
            "as a 24/7 background daemon on an unrooted Sony Xperia 1 III (Android 13, Termux).\n"
            "You have direct access to tools that manage the user's habits (habits.db), "
            "finances (finance.db), system diagnostics (battery, memory, CPU load), and Telegram alerts.\n"
            "When the user mentions habits or spending, proactively invoke the tools.\n"
            "Keep your tone sharp, helpful, concise, and technically accurate. Avoid fluff."
        )

        tools = self._build_tools()
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=False,
                maximum_remote_calls=5,
            ),
            temperature=0.4,
        )

        try:
            # Format chat context if provided
            contents = []
            if conversation_history:
                for msg in conversation_history[-6:]:  # Last 6 turns
                    role = "user" if msg.get("role") in ("user", "human") else "model"
                    contents.append(types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=msg.get("content", ""))]
                    ))

            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_prompt)]
            ))

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            response_text = response.text or "I completed the request."

            # Broadcast chat event to WebSocket clients
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
                "timestamp": datetime.utcnow().isoformat() + "Z",
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

