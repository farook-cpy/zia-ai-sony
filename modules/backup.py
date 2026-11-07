"""
Project Zia - SQLite Database Backup & Export Routine
Creates atomic, WAL-consistent SQLite snapshots, archives data into timestamped zip files,
and pushes them to private Telegram channels or local backup directories.
"""

import os
import glob
import json
import zipfile
import hashlib
import logging
import sqlite3
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("project-zia.backup")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
HABITS_DB_PATH = os.path.join(DATA_DIR, "habits.db")
FINANCE_DB_PATH = os.path.join(DATA_DIR, "finance.db")

os.makedirs(BACKUP_DIR, exist_ok=True)


def calculate_sha256(filepath: str) -> str:
    """Calculate SHA256 hash of a file."""
    if not os.path.exists(filepath):
        return ""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class BackupManager:
    """Handles snapshotting, archiving, and remote distribution of SQLite data."""

    @staticmethod
    def snapshot_database(source_path: str, dest_path: str):
        """
        Create a WAL-safe atomic online SQLite snapshot using SQLite's backup API.
        Guarantees consistency without needing to stop active reader/writer threads.
        """
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source database not found: {source_path}")

        src_conn = sqlite3.connect(source_path, timeout=5.0)
        # Flush WAL journal pages into primary DB file
        try:
            src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception as exc:
            logger.warning("WAL checkpoint warning for %s: %s", source_path, exc)

        dest_conn = sqlite3.connect(dest_path)
        with dest_conn:
            src_conn.backup(dest_conn)

        dest_conn.close()
        src_conn.close()

    @classmethod
    def create_backup(cls, send_to_telegram: bool = False,
                      telegram_bot=None, chat_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Create a complete timestamped backup archive of habits and finance data.
        """
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"zia_backup_{timestamp_str}.zip"
        zip_filepath = os.path.join(BACKUP_DIR, zip_filename)

        temp_dir = os.path.join(BACKUP_DIR, f"tmp_{timestamp_str}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # 1. Snapshot databases
            temp_habits = os.path.join(temp_dir, "habits.db")
            temp_finance = os.path.join(temp_dir, "finance.db")

            cls.snapshot_database(HABITS_DB_PATH, temp_habits)
            cls.snapshot_database(FINANCE_DB_PATH, temp_finance)

            # 2. Gather database stats for manifest
            manifest = {
                "backup_timestamp": datetime.utcnow().isoformat() + "Z",
                "device": "Sony Xperia 1 III (aarch64)",
                "databases": {
                    "habits": {
                        "size_bytes": os.path.getsize(temp_habits),
                        "sha256": calculate_sha256(temp_habits),
                    },
                    "finance": {
                        "size_bytes": os.path.getsize(temp_finance),
                        "sha256": calculate_sha256(temp_finance),
                    },
                },
            }

            # Query row counts
            with sqlite3.connect(temp_habits) as conn:
                manifest["databases"]["habits"]["habits_count"] = conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
                manifest["databases"]["habits"]["logs_count"] = conn.execute("SELECT COUNT(*) FROM habit_logs").fetchone()[0]

            with sqlite3.connect(temp_finance) as conn:
                manifest["databases"]["finance"]["transactions_count"] = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

            manifest_path = os.path.join(temp_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)

            # 3. Create zip archive
            with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(temp_habits, arcname="habits.db")
                zip_file.write(temp_finance, arcname="finance.db")
                zip_file.write(manifest_path, arcname="manifest.json")

            file_size = os.path.getsize(zip_filepath)
            logger.info("Created backup archive: %s (%d bytes)", zip_filepath, file_size)

            # 4. Prune older backups (keep last 10)
            cls.prune_old_backups(keep_count=10)

            result: Dict[str, Any] = {
                "status": "success",
                "filename": zip_filename,
                "filepath": zip_filepath,
                "size_bytes": file_size,
                "size_kb": round(file_size / 1024, 2),
                "timestamp": manifest["backup_timestamp"],
                "manifest": manifest,
                "telegram_dispatched": False,
            }

            # 5. Push to Telegram if requested
            if send_to_telegram and telegram_bot and telegram_bot.is_configured():
                caption = (
                    f"📦 *Project Zia Database Backup*\n"
                    f"📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                    f"💾 Size: *{result['size_kb']} KB*\n"
                    f"📋 Habits: *{manifest['databases']['habits']['habits_count']}* | Logs: *{manifest['databases']['habits']['logs_count']}*\n"
                    f"💰 Transactions: *{manifest['databases']['finance']['transactions_count']}*"
                )
                sent = cls.send_backup_to_telegram(zip_filepath, caption, telegram_bot, chat_id)
                result["telegram_dispatched"] = sent

            return result

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @classmethod
    def send_backup_to_telegram(cls, zip_path: str, caption: str, telegram_bot, chat_id: Optional[int] = None) -> bool:
        """Push zip archive to Telegram via sendDocument API."""
        if not telegram_bot or not telegram_bot.is_configured() or not os.path.exists(zip_path):
            return False

        targets = [chat_id] if chat_id else telegram_bot.allowed_chats
        if not targets:
            logger.warning("No authorized Telegram chat ID available for backup delivery.")
            return False

        import requests
        success = True
        for cid in targets:
            try:
                with open(zip_path, "rb") as f:
                    resp = requests.post(
                        f"{telegram_bot.api_url}/sendDocument",
                        data={"chat_id": cid, "caption": caption, "parse_mode": "Markdown"},
                        files={"document": (os.path.basename(zip_path), f, "application/zip")},
                        timeout=60,
                    )
                    if not resp.ok:
                        logger.warning("Telegram sendDocument failed for %s: %s", cid, resp.text)
                        success = False
            except Exception as exc:
                logger.error("Failed to upload backup to Telegram: %s", exc)
                success = False

        return success

    @classmethod
    def list_backups(cls) -> List[Dict[str, Any]]:
        """List all available backup zip archives ordered by most recent."""
        files = glob.glob(os.path.join(BACKUP_DIR, "zia_backup_*.zip"))
        files.sort(key=os.path.getmtime, reverse=True)

        backups = []
        for f in files:
            stat = os.stat(f)
            backups.append({
                "filename": os.path.basename(f),
                "size_bytes": stat.st_size,
                "size_kb": round(stat.st_size / 1024, 2),
                "created_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
            })
        return backups

    @classmethod
    def prune_old_backups(cls, keep_count: int = 10):
        """Delete oldest backups beyond keep_count threshold to conserve mobile storage."""
        files = glob.glob(os.path.join(BACKUP_DIR, "zia_backup_*.zip"))
        files.sort(key=os.path.getmtime, reverse=True)
        if len(files) > keep_count:
            for old_file in files[keep_count:]:
                try:
                    os.remove(old_file)
                    logger.info("Pruned old backup: %s", old_file)
                except Exception as exc:
                    logger.warning("Failed to prune %s: %s", old_file, exc)

