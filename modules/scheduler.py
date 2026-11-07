"""
Project Zia - Background Automation & Cron Scheduler
Handles daily habit briefings, midnight summaries, battery telemetry watchdogs,
and SQLite WAL database maintenance.
"""

import time
import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional

import schedule

logger = logging.getLogger("project-zia.scheduler")


class ZiaScheduler:
    """Manages recurring cron and background maintenance jobs inside Project Zia."""

    def __init__(self, habits_db=None, finance_db=None, system_telemetry_getter=None,
                 telegram_sender=None, telegram_bot=None, event_broadcaster=None):
        self.habits_db = habits_db
        self.finance_db = finance_db
        self.system_telemetry_getter = system_telemetry_getter
        self.telegram_sender = telegram_sender
        self.telegram_bot = telegram_bot
        self.event_broadcaster = event_broadcaster

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_battery_alert_time = 0

    def setup_jobs(self):
        """Register daily routines, weekly backups, and monitoring intervals."""
        schedule.clear()

        # 1. Morning habit briefing (08:00 every day)
        schedule.every().day.at("08:00").do(self.run_morning_briefing).tag("morning_briefing")

        # 2. Nightly habit & financial recap (23:59 every day)
        schedule.every().day.at("23:59").do(self.run_nightly_summary).tag("nightly_summary")

        # 3. Hardware battery & thermal watchdog (every 15 minutes)
        schedule.every(15).minutes.do(self.run_battery_watchdog).tag("battery_watchdog")

        # 4. SQLite database maintenance & WAL truncation (daily at 03:00)
        schedule.every().day.at("03:00").do(self.run_db_maintenance).tag("db_maintenance")

        # 5. Automated weekly database backup & Telegram export (Sundays at 04:00)
        schedule.every().sunday.at("04:00").do(self.run_weekly_backup).tag("weekly_backup")

        logger.info("Zia automated cron schedules registered (%d jobs).", len(schedule.jobs))

    def run_morning_briefing(self) -> Dict[str, Any]:
        """Dispatch daily habit targets."""
        logger.info("Executing morning habit briefing...")
        if not self.habits_db:
            return {"status": "error", "message": "habits_db not available"}

        habits = self.habits_db.list_habits()
        lines = [f"☀️ *Good Morning! Project Zia Habit Briefing* ({datetime.now().strftime('%A, %b %d')})\n"]
        if habits:
            lines.append("Here are your targets for today:")
            for h in habits:
                lines.append(f"• *{h['name']}*: Target {h['target_count']} {h['unit']}")
        else:
            lines.append("No active habits configured. Use `/log` or `/api/habits` to add one.")

        msg = "\n".join(lines)
        if self.telegram_sender:
            self.telegram_sender(msg)

        if self.event_broadcaster:
            self.event_broadcaster.broadcast("morning_briefing", {"habits_count": len(habits), "text": msg})

        return {"status": "success", "habits_count": len(habits)}

    def run_nightly_summary(self) -> Dict[str, Any]:
        """Compile and send daily recap."""
        logger.info("Executing nightly summary recap...")
        lines = [f"🌙 *Project Zia Daily Wrap-Up* ({datetime.now().strftime('%A, %b %d')})\n"]

        # Habits summary
        if self.habits_db:
            h_summary = self.habits_db.get_summary()
            lines.append(
                f"📋 *Habits Progress:*\n"
                f"• Active Habits: {h_summary['active_habits_count']}\n"
                f"• Completed Today: {h_summary['habits_logged_today']}"
            )

        # Finance summary
        if self.finance_db:
            f_summary = self.finance_db.get_summary()
            lines.append(
                f"\n💰 *Financial Overview:*\n"
                f"• Today's Expenses: ${f_summary['total_expense']:.2f}\n"
                f"• Net Balance: ${f_summary['net_balance']:.2f}"
            )

        msg = "\n".join(lines)
        if self.telegram_sender:
            self.telegram_sender(msg)

        if self.event_broadcaster:
            self.event_broadcaster.broadcast("nightly_summary", {"text": msg})

        return {"status": "success"}

    def run_battery_watchdog(self) -> Dict[str, Any]:
        """Inspect battery level and alert if below threshold while unplugged."""
        if not self.system_telemetry_getter:
            return {"status": "skipped", "message": "no telemetry getter"}

        telemetry = self.system_telemetry_getter()
        bat = telemetry.get("battery", {})
        percentage = bat.get("percentage")
        status = str(bat.get("status", "")).lower()

        logger.debug("Battery watchdog check: %s%%, status: %s", percentage, status)

        if percentage is not None and isinstance(percentage, (int, float)):
            # If battery drops <= 15% and discharging, send warning (throttle to once per 2 hours)
            if percentage <= 15 and "charging" not in status:
                now = time.time()
                if now - self._last_battery_alert_time > 7200:
                    self._last_battery_alert_time = now
                    alert_msg = (
                        f"⚠️ *Sony Xperia 1 III Low Battery Alert!*\n"
                        f"Battery is at *{percentage}%* ({status}). "
                        f"Connect charger to prevent background daemon interruption."
                    )
                    logger.warning("Low battery detected (%d%%). Sending notification.", percentage)
                    if self.telegram_sender:
                        self.telegram_sender(alert_msg)
                    if self.event_broadcaster:
                        self.event_broadcaster.broadcast("battery_warning", {"percentage": percentage, "status": status})

        return {"status": "success", "percentage": percentage, "battery_status": status}

    def run_db_maintenance(self) -> Dict[str, Any]:
        """Perform SQLite WAL checkpoints and optimize database size."""
        logger.info("Executing SQLite database maintenance...")
        results = {}

        for db_name, db_handler in [("habits", self.habits_db), ("finance", self.finance_db)]:
            if db_handler:
                try:
                    # Execute pragma directly using a raw connection
                    from server import get_db_connection, HABITS_DB_PATH, FINANCE_DB_PATH
                    target_path = HABITS_DB_PATH if db_name == "habits" else FINANCE_DB_PATH
                    with get_db_connection(target_path) as conn:
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                        conn.execute("PRAGMA optimize;")
                    results[db_name] = "wal_truncated_and_optimized"
                except Exception as exc:
                    logger.error("DB maintenance error for %s: %s", db_name, exc)
                    results[db_name] = f"error: {exc}"

        if self.event_broadcaster:
            self.event_broadcaster.broadcast("db_maintenance", results)

        return {"status": "success", "maintenance": results}

    def run_weekly_backup(self) -> Dict[str, Any]:
        """Execute weekly automated backup and push archive to Telegram."""
        logger.info("Executing scheduled weekly database backup...")
        try:
            from modules.backup import BackupManager
            result = BackupManager.create_backup(
                send_to_telegram=True,
                telegram_bot=self.telegram_bot
            )
            if self.event_broadcaster:
                self.event_broadcaster.broadcast("backup_created", {
                    "filename": result.get("filename"),
                    "size_kb": result.get("size_kb"),
                    "telegram_dispatched": result.get("telegram_dispatched", False),
                })
            return result
        except Exception as exc:
            logger.error("Weekly backup failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def trigger_job(self, job_name: str) -> Dict[str, Any]:
        """Manually trigger a job by its tag name."""
        handlers = {
            "morning_briefing": self.run_morning_briefing,
            "nightly_summary": self.run_nightly_summary,
            "battery_watchdog": self.run_battery_watchdog,
            "db_maintenance": self.run_db_maintenance,
            "weekly_backup": self.run_weekly_backup,
        }
        handler = handlers.get(job_name)
        if not handler:
            return {"status": "error", "message": f"Unknown job name '{job_name}'."}
        return handler()

    def list_jobs(self) -> List[Dict[str, Any]]:
        """Return list of active jobs and next execution timestamps."""
        job_info = []
        for j in schedule.jobs:
            job_info.append({
                "tags": list(j.tags),
                "interval": j.interval,
                "unit": j.unit,
                "next_run": j.next_run.isoformat() if j.next_run else None,
                "last_run": j.last_run.isoformat() if j.last_run else None,
            })
        return job_info

    def _loop(self):
        logger.info("Scheduler worker thread started.")
        while self._running:
            try:
                schedule.run_pending()
            except Exception as exc:
                logger.error("Scheduler loop execution error: %s", exc)
            time.sleep(1)

    def start(self):
        """Start scheduler in background thread."""
        if self._running:
            return
        self.setup_jobs()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="zia-cron-scheduler")
        self._thread.start()
        logger.info("Zia Cron Scheduler started successfully.")

    def stop(self):
        self._running = False

