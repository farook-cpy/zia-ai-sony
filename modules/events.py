"""
Project Zia - WebSocket Event Stream & Real-time Broadcaster
Enables bi-directional telemetry streaming between Sony Xperia 1 III and client UIs.
"""

import json
import time
import queue
import logging
import threading
from datetime import datetime
from typing import Dict, Any, Set

logger = logging.getLogger("project-zia.events")


class EventBroadcaster:
    """Thread-safe event broadcaster for WebSocket clients."""

    def __init__(self):
        self._clients: Set[Any] = set()
        self._lock = threading.Lock()
        self._heartbeat_thread = None
        self._running = False
        self._event_history_limit = 50
        self._recent_events: list = []

    def register_client(self, ws):
        with self._lock:
            self._clients.add(ws)
            count = len(self._clients)
        logger.info("WebSocket client connected. Active connections: %d", count)
        # Send welcome event and recent history
        try:
            ws.send(json.dumps({
                "event": "connection_established",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "data": {
                    "message": "Connected to Project Zia telemetry stream",
                    "client_count": count,
                }
            }))
        except Exception as exc:
            logger.warning("Failed to send welcome message: %s", exc)

    def unregister_client(self, ws):
        with self._lock:
            self._clients.discard(ws)
            count = len(self._clients)
        logger.info("WebSocket client disconnected. Active connections: %d", count)

    def broadcast(self, event_type: str, data: Any):
        """Broadcast JSON event payload to all currently connected clients."""
        payload = {
            "event": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }
        message = json.dumps(payload)

        with self._lock:
            # Store in recent event ring buffer
            self._recent_events.append(payload)
            if len(self._recent_events) > self._event_history_limit:
                self._recent_events.pop(0)

            dead_clients = []
            for ws in self._clients:
                try:
                    ws.send(message)
                except Exception:
                    dead_clients.append(ws)

            for ws in dead_clients:
                self._clients.discard(ws)

    def get_recent_events(self) -> list:
        with self._lock:
            return list(self._recent_events)

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def start_heartbeat(self, telemetry_getter, interval_seconds: int = 10):
        """Start a background telemetry heartbeat loop."""
        if self._running:
            return

        self._running = True

        def loop():
            logger.info("Telemetry heartbeat started (interval: %ds).", interval_seconds)
            while self._running:
                try:
                    if self.client_count() > 0:
                        telemetry = telemetry_getter()
                        self.broadcast("telemetry_tick", telemetry)
                except Exception as exc:
                    logger.debug("Heartbeat broadcast error: %s", exc)
                time.sleep(interval_seconds)

        self._heartbeat_thread = threading.Thread(target=loop, daemon=True, name="zia-telemetry-heartbeat")
        self._heartbeat_thread.start()

    def stop(self):
        self._running = False


# Global event broadcaster singleton
broadcaster = EventBroadcaster()

