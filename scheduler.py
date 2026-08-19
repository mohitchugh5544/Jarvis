"""
Background reminder scheduler with persistent catch-up (Phase 5).

Checks reminders.json every 20 seconds and fires Windows desktop notifications.
On startup, catches up on any reminders that came due while Jarvis was closed.
"""
import json
import threading
import time
from datetime import datetime

from plyer import notification

from config import DATA_DIR

REMINDERS_FILE = DATA_DIR / "reminders.json"
CHECK_INTERVAL_SECONDS = 20
_started = False


def _load():
    if not REMINDERS_FILE.exists():
        return []
    try:
        return json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(data):
    try:
        REMINDERS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _check_once():
    reminders = _load()
    now = datetime.now()
    changed = False
    for r in reminders:
        if r.get("fired") or not r.get("due"):
            continue
        try:
            due = datetime.fromisoformat(r["due"])
        except Exception:
            continue

        if now >= due:
            time_diff = (now - due).total_seconds()
            title = "Missed Jarvis Reminder" if time_diff > 120 else "Jarvis Reminder"
            try:
                notification.notify(
                    title=title,
                    message=r["text"],
                    timeout=15,
                )
            except Exception as e:
                print(f"[scheduler] couldn't show notification: {e}")
            r["fired"] = True
            changed = True
    if changed:
        _save(reminders)


def _loop():
    # Initial immediate check on startup (catches missed reminders)
    try:
        _check_once()
    except Exception as e:
        print(f"[scheduler startup check] error: {e}")

    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        try:
            _check_once()
        except Exception as e:
            print(f"[scheduler] error: {e}")


def start():
    """Call once at startup to begin checking for due reminders."""
    global _started
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
