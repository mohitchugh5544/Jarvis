"""
Background reminder & countdown timer scheduler with persistent catch-up.

Checks reminders.json and timers.json every 3 seconds and fires Windows desktop
notifications (plus an audible chime when a timer completes).
On startup, catches up on any reminders that came due while Jarvis was closed.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from plyer import notification

try:
    import winsound
except Exception:
    winsound = None

from config import DATA_DIR

REMINDERS_FILE = DATA_DIR / "reminders.json"
TIMERS_FILE = DATA_DIR / "timers.json"
CHECK_INTERVAL_SECONDS = 3
_started = False


def _load(path: Path):
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _ring_chime():
    if winsound is None:
        return
    try:
        for _ in range(2):
            winsound.Beep(1000, 140)
            winsound.Beep(1400, 180)
    except Exception:
        pass


def _check_once():
    now = datetime.now()

    # 1. Check Reminders
    reminders = _load(REMINDERS_FILE)
    rem_changed = False
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
            _ring_chime()
            r["fired"] = True
            rem_changed = True
    if rem_changed:
        _save(REMINDERS_FILE, reminders)

    # 2. Check Countdown Timers
    timers = _load(TIMERS_FILE)
    tim_changed = False
    for t in timers:
        if t.get("fired") or not t.get("due"):
            continue
        try:
            due = datetime.fromisoformat(t["due"])
        except Exception:
            continue

        if now >= due:
            label = t.get("label", "Timer")
            try:
                notification.notify(
                    title="Jarvis Timer Complete",
                    message=f"Your timer '{label}' is up!",
                    timeout=15,
                )
            except Exception as e:
                print(f"[scheduler] couldn't show timer notification: {e}")
            _ring_chime()
            t["fired"] = True
            tim_changed = True
    if tim_changed:
        _save(TIMERS_FILE, timers)


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


start_scheduler = start
