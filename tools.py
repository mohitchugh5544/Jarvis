"""
Jarvis tools.

Each tool has two parts:
1. A schema in TOOL_DEFINITIONS (tells the model the tool exists and how to call it)
2. A Python function in TOOL_FUNCTIONS (what actually runs on your machine)

To add a new tool: write the function, add its schema, add it to TOOL_FUNCTIONS.
That's the whole extension pattern for the rest of this project.
"""
import base64
import ctypes
import io
import json
import os
import platform
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

from PIL import Image
import dateparser
from duckduckgo_search import DDGS
from openai import OpenAI
from PIL import ImageGrab
import psutil
from pycaw.pycaw import AudioUtilities
import pyperclip
import requests
from bs4 import BeautifulSoup
import screen_brightness_control as sbc

from config import DATA_DIR, GOOGLE_API_KEY, GOOGLE_BASE_URL, JARVIS_MODEL

NOTES_FILE = DATA_DIR / "notes.json"
REMINDERS_FILE = DATA_DIR / "reminders.json"
TIMERS_FILE = DATA_DIR / "timers.json"
ROUTINES_FILE = DATA_DIR / "routines.json"
HISTORY_FILE = DATA_DIR / "history.json"
MEMORIES_FILE = DATA_DIR / "memories.json"
CHAT_LOG_FILE = DATA_DIR / "chat_log.json"

# Common app names -> how to launch them on Windows.
APP_MAP = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "spotify": "spotify.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "opera": "opera.exe",
    "brave": "brave.exe",
    "firefox": "firefox.exe",
    "vlc": "vlc.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "paint": "mspaint.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "vs code": "code.exe",
    "vscode": "code.exe",
    "visual studio code": "code.exe",
    "discord": "discord.exe",
    "steam": "steam.exe",
    "whatsapp": "whatsapp.exe",
}


_JSON_CACHE: dict[str, tuple[int, object]] = {}
_SHORTCUT_CACHE: dict[str, Path | None] = {}


def _load_json(path: Path, default):
    key = str(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        _JSON_CACHE.pop(key, None)
        return default
    except Exception:
        return default

    cached = _JSON_CACHE.get(key)
    if cached and cached[0] == st.st_mtime_ns:
        val = cached[1]
        if isinstance(val, list):
            return list(val)
        if isinstance(val, dict):
            return dict(val)
        return val

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _JSON_CACHE[key] = (st.st_mtime_ns, data)
        if isinstance(data, list):
            return list(data)
        if isinstance(data, dict):
            return dict(data)
        return data
    except Exception:
        return default


def _save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            st = path.stat()
            _JSON_CACHE[str(path)] = (st.st_mtime_ns, data)
        except Exception:
            pass
    except Exception:
        pass


def _get_user_folder(folder_name: str) -> Path:
    name = folder_name.lower().strip()
    candidates = [
        Path.home() / "OneDrive" / name.capitalize(),
        Path.home() / name.capitalize(),
        Path.home() / "OneDrive" / name,
        Path.home() / name,
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return Path.home()


# ---- Apps & Local File Discovery ---------------------------------------

def _find_start_menu_shortcut(app_name: str) -> Path | None:
    """Scans Windows Start Menu and Desktop folders for a matching .lnk or .url shortcut."""
    target = app_name.strip().lower()
    if not target:
        return None
    if target in _SHORTCUT_CACHE:
        cached = _SHORTCUT_CACHE[target]
        if cached is None or cached.exists():
            return cached

    roots = [
        Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        _get_user_folder("Desktop"),
    ]
    exact_match = None
    partial_match = None

    for root in roots:
        if not root or not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                if p.suffix.lower() not in (".lnk", ".url", ".exe"):
                    continue
                stem = p.stem.lower()
                if any(skip in stem for skip in ("uninstall", "readme", "help", "website", "manual")):
                    continue
                if stem == target:
                    exact_match = p
                    break
                if target in stem and partial_match is None:
                    partial_match = p
        except Exception:
            continue
        if exact_match:
            _SHORTCUT_CACHE[target] = exact_match
            return exact_match

    _SHORTCUT_CACHE[target] = partial_match
    return partial_match


def open_app(app_name: str) -> str:
    if platform.system() != "Windows":
        return "open_app is Windows-only right now."
    key = app_name.strip().lower()
    exe = APP_MAP.get(key)
    try:
        if exe:
            subprocess.Popen(exe, shell=True)
            return f"Opened {app_name}."

        # Auto-discover installed app via Start Menu / Desktop shortcuts
        shortcut = _find_start_menu_shortcut(key)
        if shortcut and shortcut.exists():
            os.startfile(shortcut)
            return f"Opened {shortcut.stem}."

        subprocess.Popen(f'start "" "{app_name}"', shell=True)
        return f"Launched '{app_name}'."
    except Exception as e:
        return f"Failed to open {app_name}: {e}"


def close_app(app_name: str) -> str:
    if platform.system() != "Windows":
        return "close_app is Windows-only right now."
    key = app_name.strip().lower()
    exe = APP_MAP.get(key)
    if not exe:
        # Check running processes for a matching name
        for proc in psutil.process_iter(["name"]):
            pname = (proc.info.get("name") or "").lower()
            if key in pname and pname.endswith(".exe"):
                exe = proc.info["name"]
                break
    if not exe:
        exe = app_name if app_name.lower().endswith(".exe") else app_name + ".exe"
    try:
        result = subprocess.run(
            ["taskkill", "/IM", exe, "/F"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Closed {app_name}."
        return f"Couldn't close {app_name}: {(result.stderr or result.stdout).strip()}"
    except Exception as e:
        return f"Failed to close {app_name}: {e}"


def list_running_apps(limit: int = 12) -> str:
    """Lists top running applications/processes sorted by memory usage."""
    try:
        agg: dict[str, float] = {}
        skip = {
            "svchost.exe", "system idle process", "system", "registry", "smss.exe",
            "csrss.exe", "wininit.exe", "services.exe", "lsass.exe", "fontdrvhost.exe",
            "dwm.exe", "conhost.exe", "runtimebroker.exe", "searchhost.exe",
            "startmenuexperiencehost.exe", "textinputhost.exe", "sihost.exe",
        }
        for p in psutil.process_iter(["name", "memory_info"]):
            name = p.info.get("name") or ""
            if not name or name.lower() in skip:
                continue
            mem_mb = (p.info["memory_info"].rss / (1024 * 1024)) if p.info.get("memory_info") else 0.0
            agg[name] = agg.get(name, 0.0) + mem_mb
        top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[: max(1, min(25, int(limit)))]
        if not top:
            return "No active user applications detected."
        lines = [f"- {name}: {mb:.0f} MB" for name, mb in top]
        return "Top running apps by memory:\n" + "\n".join(lines)
    except Exception as e:
        return f"Failed to list running apps: {e}"


def search_files(query: str, folder: str = "", open_first: bool = False) -> str:
    """Searches user folders (Desktop, Documents, Downloads, Pictures) for files or folders matching query."""
    q = (query or "").strip().lower()
    if not q:
        return "Provide a file or folder name to search for."

    if folder and folder.strip():
        roots = [_get_user_folder(folder.strip())]
    else:
        roots = []
        for fn in ("Desktop", "Documents", "Downloads", "Pictures"):
            r = _get_user_folder(fn)
            if r not in roots and r.exists():
                roots.append(r)

    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "appdata"}
    matches: list[Path] = []

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs and not d.startswith(".")]
            # Check matching folder names
            for d in dirnames:
                if q in d.lower():
                    matches.append(Path(dirpath) / d)
                    if len(matches) >= 10:
                        break
            if len(matches) >= 10:
                break
            # Check matching file names
            for f in filenames:
                if q in f.lower():
                    matches.append(Path(dirpath) / f)
                    if len(matches) >= 10:
                        break
            if len(matches) >= 10:
                break
        if len(matches) >= 10:
            break

    if not matches:
        return f"No files or folders found matching '{query}'."

    if open_first:
        try:
            os.startfile(matches[0])
            return f"Opened '{matches[0].name}' ({matches[0]})."
        except Exception as e:
            return f"Found '{matches[0]}', but failed to open it: {e}"

    lines = [f"- {m.name} ({m})" for m in matches[:8]]
    return f"Found {len(matches)} match(es):\n" + "\n".join(lines)


# ---- Web & Search ------------------------------------------------------

def open_url(url: str, browser: str = "") -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if browser and browser.strip():
        b_key = browser.strip().lower()
        exe = APP_MAP.get(b_key, b_key if b_key.endswith(".exe") else f"{b_key}.exe")
        # Also check common per-user browser install locations on Windows (e.g. Opera / Brave)
        local_app = Path(os.environ.get("LOCALAPPDATA", ""))
        prog_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        candidates = [
            local_app / "Programs" / "Opera" / "opera.exe" if "opera" in b_key else None,
            local_app / "Programs" / "Opera GX" / "opera.exe" if "opera" in b_key else None,
            local_app / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe" if "brave" in b_key else None,
            prog_files / "Google" / "Chrome" / "Application" / "chrome.exe" if "chrome" in b_key else None,
            prog_files / "Mozilla Firefox" / "firefox.exe" if "firefox" in b_key else None,
        ]
        for c in candidates:
            if c and c.exists():
                exe = str(c)
                break
        try:
            subprocess.Popen(f'start "" "{exe}" "{url}"', shell=True)
            return f"Opened {url} in {browser}."
        except Exception:
            pass
    webbrowser.open(url)
    return f"Opened {url} in your browser."


def fetch_webpage_text(url: str, max_chars: int = 2000) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        return f"Couldn't fetch {url}: {e}"
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text or "That page didn't have any readable text content."


def search_web(query: str, max_results: int = 5) -> str:
    """Searches the live web using DuckDuckGo and returns search result snippets."""
    try:
        results = list(DDGS().text(query, max_results=max_results))
        if not results:
            return f"No search results found for '{query}'."
        formatted = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "No Title")
            snippet = r.get("body", "")
            link = r.get("href", "")
            formatted.append(f"{i}. {title}\n   {snippet}\n   Source: {link}")
        return "\n\n".join(formatted)
    except Exception as e:
        return f"Web search failed: {e}"


# ---- Clipboard AI -------------------------------------------------------

def get_clipboard_text() -> str:
    """Reads text from Windows clipboard."""
    try:
        text = pyperclip.paste()
        if not text or not text.strip():
            return "The clipboard is currently empty or contains non-text data."
        if len(text) > 4000:
            return f"Clipboard content ({len(text)} chars):\n" + text[:4000] + "\n[Truncated]"
        return f"Clipboard content:\n{text}"
    except Exception as e:
        return f"Failed to read clipboard: {e}"


def set_clipboard_text(text: str) -> str:
    """Copies text to the Windows clipboard."""
    try:
        pyperclip.copy(text)
        return "Copied text to clipboard."
    except Exception as e:
        return f"Failed to copy to clipboard: {e}"


# ---- Screen Vision Multimodal -------------------------------------------

def _capture_screen_image() -> Image.Image:
    """Captures screen using native GDI BitBlt."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)

    hdesktop = user32.GetDesktopWindow()
    desktop_dc = user32.GetWindowDC(hdesktop)
    img_dc = gdi32.CreateCompatibleDC(desktop_dc)
    mem_bitmap = gdi32.CreateCompatibleBitmap(desktop_dc, width, height)
    gdi32.SelectObject(img_dc, mem_bitmap)

    gdi32.BitBlt(img_dc, 0, 0, width, height, desktop_dc, 0, 0, 0x00CC0020)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32), ('biHeight', ctypes.c_int32),
            ('biPlanes', ctypes.c_uint16), ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
            ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32), ('biYPelsPerMeter', ctypes.c_int32),
            ('biClrUsed', ctypes.c_uint32), ('biClrImportant', ctypes.c_uint32)
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(img_dc, mem_bitmap, 0, height, buffer, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(mem_bitmap)
    gdi32.DeleteDC(img_dc)
    user32.ReleaseDC(hdesktop, desktop_dc)

    img = Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1)
    return img.convert('RGB')


def analyze_screen(prompt: str = "Explain what is on the screen.") -> str:
    """Captures the current screen and uses Gemini Vision to explain, analyze errors, or describe what is visible."""
    try:
        img = _capture_screen_image()
        img.thumbnail((1024, 768))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")

        vision_client = OpenAI(api_key=GOOGLE_API_KEY, base_url=GOOGLE_BASE_URL)
        response = vision_client.chat.completions.create(
            model=JARVIS_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"You are Jarvis analyzing the user's Windows computer screen. {prompt}",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_data}"
                        },
                    },
                ],
            }],
        )
        return response.choices[0].message.content or "No visual details returned."
    except Exception as e:
        return f"Screen vision analysis failed: {e}"


# ---- Notes / reminders --------------------------------------------------

def take_note(text: str) -> str:
    notes = _load_json(NOTES_FILE, [])
    notes.append({"text": text, "timestamp": datetime.now().isoformat()})
    _save_json(NOTES_FILE, notes)
    return "Noted."


def list_notes(limit: int = 10) -> str:
    notes = _load_json(NOTES_FILE, [])
    if not notes:
        return "You have no notes yet."
    recent = notes[-limit:]
    lines = [f"- ({n['timestamp'][:16]}) {n['text']}" for n in recent]
    return "\n".join(lines)


def delete_note(query: str) -> str:
    """Deletes saved note(s) matching query text."""
    q = (query or "").strip().lower()
    if not q:
        return "Specify which note to delete."
    notes = _load_json(NOTES_FILE, [])
    kept = [n for n in notes if q not in n.get("text", "").lower()]
    removed = len(notes) - len(kept)
    if not removed:
        return f"No note found matching '{query}'."
    _save_json(NOTES_FILE, kept)
    return f"Deleted {removed} note(s)."


def set_reminder(text: str, when: str) -> str:
    """when: free-text time, e.g. 'in 20 minutes', '5pm today', 'tomorrow at 9am'."""
    due = dateparser.parse(when, settings={"PREFER_DATES_FROM": "future"})
    reminders = _load_json(REMINDERS_FILE, [])
    reminders.append({
        "text": text,
        "when": when,
        "created": datetime.now().isoformat(),
        "due": due.isoformat() if due else None,
        "fired": False,
    })
    _save_json(REMINDERS_FILE, reminders)
    if due:
        return f"Reminder set for {due.strftime('%a %b %d, %I:%M %p')}: '{text}'."
    return (
        f"Saved '{text}', but I couldn't work out a specific time from "
        f"'{when}' — it won't fire automatically. Try rephrasing, e.g. "
        f"'in 20 minutes' or '5pm today'."
    )


def list_reminders() -> str:
    reminders = _load_json(REMINDERS_FILE, [])
    pending = [r for r in reminders if not r.get("fired")]
    if not pending:
        return "You have no pending reminders."
    lines = []
    for r in pending:
        due_str = "unscheduled"
        if r.get("due"):
            due_str = datetime.fromisoformat(r["due"]).strftime("%a %b %d, %I:%M %p")
        lines.append(f"- {r['text']} (due {due_str})")
    return "\n".join(lines)


def delete_reminder(query: str) -> str:
    """Cancels/deletes pending reminder(s) matching query text."""
    q = (query or "").strip().lower()
    if not q:
        return "Specify which reminder to delete."
    reminders = _load_json(REMINDERS_FILE, [])
    kept = [
        r for r in reminders
        if r.get("fired") or q not in r.get("text", "").lower()
    ]
    removed = len(reminders) - len(kept)
    if not removed:
        return f"No pending reminder found matching '{query}'."
    _save_json(REMINDERS_FILE, kept)
    return f"Cancelled {removed} reminder(s)."


# ---- Countdown Timers ---------------------------------------------------

def set_timer(duration: str, label: str = "Timer") -> str:
    """Sets a countdown timer (e.g. '10 minutes', '30 seconds', '25 minutes')."""
    raw = (duration or "").strip()
    if not raw.lower().startswith("in "):
        raw = "in " + raw
    due = dateparser.parse(raw, settings={"PREFER_DATES_FROM": "future"})
    now = datetime.now()
    if not due or due <= now:
        return f"Couldn't parse timer duration '{duration}'. Try e.g. '10 minutes' or '45 seconds'."
    secs = int(round((due - now).total_seconds()))
    timers = _load_json(TIMERS_FILE, [])
    timers.append({
        "label": (label or "Timer").strip(),
        "created": now.isoformat(),
        "due": due.isoformat(),
        "duration_seconds": secs,
        "fired": False,
    })
    _save_json(TIMERS_FILE, timers)
    mins, rem_s = divmod(secs, 60)
    dur_str = f"{mins}m {rem_s}s" if mins else f"{rem_s}s"
    return f"Timer '{label}' set for {dur_str} (rings at {due.strftime('%I:%M:%S %p').lstrip('0')})."


def list_timers() -> str:
    """Lists active countdown timers and their remaining time."""
    timers = _load_json(TIMERS_FILE, [])
    now = datetime.now()
    active = []
    for t in timers:
        if t.get("fired") or not t.get("due"):
            continue
        try:
            due = datetime.fromisoformat(t["due"])
            rem = int((due - now).total_seconds())
            if rem > 0:
                mins, secs = divmod(rem, 60)
                active.append(f"- {t.get('label', 'Timer')}: {mins}m {secs}s remaining")
        except Exception:
            continue
    if not active:
        return "You have no active timers."
    return "\n".join(active)


def cancel_timer(label: str = "") -> str:
    """Cancels active timer(s) matching label (or all active timers if label is empty)."""
    timers = _load_json(TIMERS_FILE, [])
    q = (label or "").strip().lower()
    cancelled = 0
    for t in timers:
        if not t.get("fired"):
            if not q or q in t.get("label", "").lower():
                t["fired"] = True
                cancelled += 1
    if not cancelled:
        return "No matching active timers to cancel."
    _save_json(TIMERS_FILE, timers)
    return f"Cancelled {cancelled} timer(s)."


def get_current_time() -> str:
    return datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")


# ---- Windows Volume & Audio ---------------------------------------------

def _get_speaker_volume():
    speakers = AudioUtilities.GetSpeakers()
    return speakers.EndpointVolume


def set_volume(level: int) -> str:
    """Sets master audio volume (0 to 100)."""
    try:
        level = max(0, min(100, int(level)))
        vol = _get_speaker_volume()
        vol.SetMasterVolumeLevelScalar(level / 100.0, None)
        if vol.GetMute():
            vol.SetMute(0, None)
        return f"Volume set to {level}%."
    except Exception as e:
        return f"Failed to set volume: {e}"


def get_volume() -> str:
    """Gets the current master volume percentage and mute status."""
    try:
        vol = _get_speaker_volume()
        level = int(round(vol.GetMasterVolumeLevelScalar() * 100))
        is_muted = bool(vol.GetMute())
        status = " (Muted)" if is_muted else ""
        return f"Current volume is {level}%{status}."
    except Exception as e:
        return f"Failed to get volume: {e}"


def mute_audio() -> str:
    """Mutes the master audio."""
    try:
        vol = _get_speaker_volume()
        vol.SetMute(1, None)
        return "Audio muted."
    except Exception as e:
        return f"Failed to mute audio: {e}"


def unmute_audio() -> str:
    """Unmutes the master audio."""
    try:
        vol = _get_speaker_volume()
        vol.SetMute(0, None)
        return "Audio unmuted."
    except Exception as e:
        return f"Failed to unmute audio: {e}"


# ---- Media Controls -----------------------------------------------------

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3


def control_media(action: str) -> str:
    """Control media playback (play_pause, next, previous)."""
    action_key = action.lower().strip()
    key_map = {
        "play": VK_MEDIA_PLAY_PAUSE,
        "pause": VK_MEDIA_PLAY_PAUSE,
        "play_pause": VK_MEDIA_PLAY_PAUSE,
        "next": VK_MEDIA_NEXT_TRACK,
        "skip": VK_MEDIA_NEXT_TRACK,
        "previous": VK_MEDIA_PREV_TRACK,
        "prev": VK_MEDIA_PREV_TRACK,
    }
    vk = key_map.get(action_key)
    if not vk:
        return f"Unknown media action '{action}'. Use play, pause, next, or previous."
    try:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return f"Media {action} triggered."
    except Exception as e:
        return f"Failed to execute media {action}: {e}"


# ---- System & Battery ---------------------------------------------------

def get_battery_status() -> str:
    """Gets battery percentage and charging status."""
    try:
        battery = psutil.sensors_battery()
        if not battery:
            return "No battery detected (Desktop PC or unsupported hardware)."
        state = "charging" if battery.power_plugged else "discharging"
        return f"Battery is at {battery.percent}%, currently {state}."
    except Exception as e:
        return f"Failed to get battery status: {e}"


def get_system_status() -> str:
    """Gets CPU usage, RAM usage, free disk space, and battery status."""
    try:
        cpu = psutil.cpu_percent(interval=0.2)
        mem = psutil.virtual_memory()
        used_gb = mem.used / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        disk = psutil.disk_usage("C:\\")
        free_disk_gb = disk.free / (1024 ** 3)
        bat = psutil.sensors_battery()
        bat_str = (
            f"{bat.percent}% ({'charging' if bat.power_plugged else 'on battery'})"
            if bat else "N/A"
        )
        return (
            f"CPU: {cpu:.0f}% | RAM: {mem.percent:.0f}% ({used_gb:.1f}/{total_gb:.1f} GB) | "
            f"Disk Free: {free_disk_gb:.0f} GB | Battery: {bat_str}."
        )
    except Exception as e:
        return f"Failed to get system status: {e}"


def minimize_all_windows() -> str:
    """Toggles Show Desktop (Win+D) to minimize or restore all open windows."""
    try:
        VK_LWIN = 0x5B
        VK_D = 0x44
        ctypes.windll.user32.keybd_event(VK_LWIN, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_D, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_D, 0, 2, 0)
        ctypes.windll.user32.keybd_event(VK_LWIN, 0, 2, 0)
        return "Toggled desktop view (Win+D)."
    except Exception as e:
        return f"Failed to minimize windows: {e}"


def lock_pc() -> str:
    """Locks the Windows workstation."""
    try:
        ctypes.windll.user32.LockWorkStation()
        return "Workstation locked."
    except Exception as e:
        return f"Failed to lock PC: {e}"


# ---- Everyday Routines / Macros -----------------------------------------

DEFAULT_ROUTINES = [
    {
        "name": "coding mode",
        "description": "Open VS Code, open Desktop/CODING folder, and set volume to 30%.",
    },
    {
        "name": "morning briefing",
        "description": "Check current time, battery status, pending reminders, and recent notes.",
    },
    {
        "name": "focus mode",
        "description": "Mute audio, set a 25 minute Pomodoro timer labeled 'Focus Session', and close Discord.",
    },
    {
        "name": "goodnight / standby",
        "description": "Set screen brightness to 15%, mute audio, minimize all windows, and lock PC.",
    },
]


def _load_routines() -> list:
    routines = _load_json(ROUTINES_FILE, None)
    if routines is None:
        _save_json(ROUTINES_FILE, DEFAULT_ROUTINES)
        return list(DEFAULT_ROUTINES)
    return routines


def save_routine(name: str, description: str) -> str:
    """Saves or updates a custom multi-step everyday routine."""
    rname = (name or "").strip().lower()
    rdesc = (description or "").strip()
    if not rname or not rdesc:
        return "Provide both a routine name and description of steps."
    routines = _load_routines()
    for r in routines:
        if r.get("name", "").lower() == rname:
            r["description"] = rdesc
            _save_json(ROUTINES_FILE, routines)
            return f"Updated routine '{rname}': {rdesc}"
    routines.append({"name": rname, "description": rdesc})
    _save_json(ROUTINES_FILE, routines)
    return f"Saved routine '{rname}': {rdesc}"


def list_routines() -> str:
    """Lists all saved everyday routines and their steps."""
    routines = _load_routines()
    if not routines:
        return "No routines saved yet."
    return "\n".join(f"- '{r['name']}': {r['description']}" for r in routines)


def delete_routine(name: str) -> str:
    """Deletes a saved routine by name."""
    rname = (name or "").strip().lower()
    routines = _load_routines()
    kept = [r for r in routines if r.get("name", "").lower() != rname]
    if len(kept) == len(routines):
        return f"No routine found named '{name}'."
    _save_json(ROUTINES_FILE, kept)
    return f"Deleted routine '{name}'."


def get_routines_prompt_block() -> str:
    """Formats saved routines for injection into the agent's system prompt."""
    routines = _load_routines()
    if not routines:
        return ""
    return "\n".join(f"- '{r['name']}': {r['description']}" for r in routines)


# ---- Screenshots & Folders ----------------------------------------------

def take_screenshot() -> str:
    """Takes a screenshot of the display and saves it to the Pictures folder."""
    try:
        pic_dir = _get_user_folder("Pictures")
        filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath = pic_dir / filename
        img = _capture_screen_image()
        img.save(filepath)
        return f"Screenshot saved to {pic_dir.name} as {filename}."
    except Exception as e:
        return f"Failed to take screenshot: {e}"


def set_screen_brightness(level: int) -> str:
    """Sets screen brightness (0 to 100)."""
    try:
        level = max(0, min(100, int(level)))
        sbc.set_brightness(level)
        return f"Screen brightness set to {level}%."
    except Exception as e:
        return f"Could not set screen brightness: {e}"


def get_screen_brightness() -> str:
    """Gets current screen brightness percentage."""
    try:
        b = sbc.get_brightness()
        val = b[0] if isinstance(b, list) else b
        return f"Current screen brightness is {val}%."
    except Exception as e:
        return f"Could not determine screen brightness: {e}"


def open_folder(folder_name: str) -> str:
    """Opens standard user folders (Downloads, Documents, Desktop, Pictures, Music, Videos)."""
    target = _get_user_folder(folder_name)
    if not target or target == Path.home() and folder_name.lower().strip() not in ("home", "user"):
        target = Path(folder_name)
    try:
        if target.exists():
            os.startfile(target)
            return f"Opened {folder_name} folder in File Explorer."
        return f"Folder '{folder_name}' not found."
    except Exception as e:
        return f"Failed to open folder {folder_name}: {e}"


def clear_memory() -> str:
    """Clears short-term conversation context (keeps permanent learned facts)."""
    if HISTORY_FILE.exists():
        try:
            HISTORY_FILE.unlink()
        except Exception:
            pass
    return "Short-term conversation context cleared (your learned long-term memories are still saved)."


# ---- Long-Term Memory & Persistent Chat Archive ------------------------

def remember_fact(fact: str, category: str = "preference") -> str:
    """Saves a permanent fact, user preference, correction, or habit to long-term memory."""
    fact_clean = (fact or "").strip()
    if not fact_clean:
        return "Nothing to remember."
    cat_clean = (category or "general").strip().lower()
    memories = _load_json(MEMORIES_FILE, [])
    now_iso = datetime.now().isoformat(timespec="seconds")

    # Avoid exact duplicates (update timestamp/category if already stored)
    for m in memories:
        if m.get("fact", "").strip().lower() == fact_clean.lower():
            m["category"] = cat_clean
            m["updated_at"] = now_iso
            _save_json(MEMORIES_FILE, memories)
            return f"Updated memory [{cat_clean}]: {fact_clean}"

    next_id = max((m.get("id", 0) for m in memories), default=0) + 1
    memories.append({
        "id": next_id,
        "category": cat_clean,
        "fact": fact_clean,
        "created_at": now_iso,
        "updated_at": now_iso,
    })
    _save_json(MEMORIES_FILE, memories)
    return f"Learned and saved to memory [{cat_clean}]: {fact_clean}"


def list_memories(category: str = "") -> str:
    """Lists all facts, preferences, and corrections Jarvis has learned about the user."""
    memories = _load_json(MEMORIES_FILE, [])
    if not memories:
        return "I haven't saved any long-term memories yet."
    cat_filter = (category or "").strip().lower()
    if cat_filter:
        memories = [m for m in memories if m.get("category", "").lower() == cat_filter]
        if not memories:
            return f"No saved memories under category '{cat_filter}'."
    lines = [
        f"- [#{m.get('id', '?')} | {m.get('category', 'general')}] {m.get('fact', '')}"
        for m in memories
    ]
    return "\n".join(lines)


def forget_memory(query: str) -> str:
    """Removes a learned memory by ID (e.g. '3') or matching text."""
    q = (query or "").strip().lower().lstrip("#")
    if not q:
        return "Specify which memory to forget."
    memories = _load_json(MEMORIES_FILE, [])
    if not memories:
        return "No long-term memories to remove."
    kept = []
    removed = []
    for m in memories:
        mid = str(m.get("id", ""))
        fact_txt = m.get("fact", "")
        if q == mid or q in fact_txt.lower():
            removed.append(fact_txt)
        else:
            kept.append(m)
    if not removed:
        return f"Couldn't find a saved memory matching '{query}'."
    _save_json(MEMORIES_FILE, kept)
    return f"Forgot {len(removed)} memory item(s): " + "; ".join(removed)


def get_memories_prompt_block() -> str:
    """Formats saved long-term memories for injection into the agent's system prompt."""
    memories = _load_json(MEMORIES_FILE, [])
    if not memories:
        return ""
    lines = [f"- [{m.get('category', 'general')}] {m.get('fact', '')}" for m in memories if m.get("fact")]
    return "\n".join(lines)


def load_chat_log() -> list:
    """Loads the permanent chat log, bootstrapping from history.json if needed."""
    log_entries = _load_json(CHAT_LOG_FILE, [])
    if log_entries:
        return log_entries

    # Bootstrap from existing history.json if present so no prior chats are lost
    raw_history = _load_json(HISTORY_FILE, [])
    if isinstance(raw_history, list) and raw_history:
        bootstrapped = []
        pending_user = None
        pending_tools = []
        now_iso = datetime.now().isoformat(timespec="seconds")
        for msg in raw_history:
            role = msg.get("role")
            if role == "user" and msg.get("content"):
                pending_user = str(msg["content"])
                pending_tools = []
            elif role == "tool" and msg.get("name"):
                pending_tools.append(msg["name"])
            elif role == "assistant" and msg.get("content") and not msg.get("tool_calls"):
                if pending_user is not None:
                    bootstrapped.append({
                        "timestamp": now_iso,
                        "user": pending_user,
                        "assistant": str(msg["content"]).strip(),
                        "tools": pending_tools,
                    })
                    pending_user = None
                    pending_tools = []
        if bootstrapped:
            _save_json(CHAT_LOG_FILE, bootstrapped)
            return bootstrapped
    return []


def append_chat_log(user_text: str, assistant_text: str, tools_used: list | None = None):
    """Appends a completed conversation turn to the permanent chat log."""
    entries = load_chat_log()
    entries.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "user": user_text,
        "assistant": assistant_text,
        "tools": list(tools_used or []),
    })
    _save_json(CHAT_LOG_FILE, entries)


def search_chat_history(query: str = "", limit: int = 10) -> str:
    """Searches the permanent chat archive for past conversations matching query."""
    entries = load_chat_log()
    if not entries:
        return "No saved chat history found."
    q = (query or "").strip().lower()
    if q:
        matches = [
            e for e in entries
            if q in e.get("user", "").lower() or q in e.get("assistant", "").lower()
        ]
    else:
        matches = entries
    if not matches:
        return f"No past chats found matching '{query}'."
    recent = matches[-max(1, min(30, int(limit))):]
    formatted = []
    for e in recent:
        ts = e.get("timestamp", "")[:16].replace("T", " ")
        formatted.append(f"[{ts}] You: {e.get('user', '')}\nJarvis: {e.get('assistant', '')}")
    return "\n\n".join(formatted)


# ---- Schemas the model sees ---------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "open_app",
        "description": "Open a desktop application on the Windows PC by name (e.g. 'notepad', 'spotify', 'chrome', 'calc').",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "e.g. 'notepad', 'spotify', 'chrome'"}
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "close_app",
        "description": "Close/quit a running desktop application by name.",
        "input_schema": {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
            "required": ["app_name"],
        },
    },
    {
        "name": "search_web",
        "description": "Search the live web using DuckDuckGo to find real-time information, answers, news, and facts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query to look up on the web"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_clipboard_text",
        "description": "Read the text currently copied to the Windows clipboard.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_clipboard_text",
        "description": "Copy new text onto the Windows clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to copy to clipboard"}
            },
            "required": ["text"],
        },
    },
    {
        "name": "analyze_screen",
        "description": "Capture the active computer screen and use AI vision to analyze, explain errors, summarize open documents, or describe visual contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Specific question or request about what is on screen"}
            },
            "required": [],
        },
    },
    {
        "name": "set_volume",
        "description": "Set the master audio volume level percentage (0 to 100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Volume percentage from 0 to 100"}
            },
            "required": ["level"],
        },
    },
    {
        "name": "get_volume",
        "description": "Get current audio volume percentage and mute status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "mute_audio",
        "description": "Mute master audio output.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "unmute_audio",
        "description": "Unmute master audio output.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "control_media",
        "description": "Control media playback like play/pause, next track, previous track.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["play_pause", "play", "pause", "next", "previous"]}
            },
            "required": ["action"],
        },
    },
    {
        "name": "get_battery_status",
        "description": "Get current battery level percentage and charging status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "lock_pc",
        "description": "Lock the Windows PC / workstation screen.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the PC screen and save it to the Pictures folder.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_screen_brightness",
        "description": "Set the screen brightness percentage (0 to 100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Brightness percentage from 0 to 100"}
            },
            "required": ["level"],
        },
    },
    {
        "name": "get_screen_brightness",
        "description": "Get the current screen brightness level.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "open_folder",
        "description": "Open standard Windows folders in File Explorer (e.g. 'downloads', 'documents', 'desktop', 'pictures').",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_name": {"type": "string", "description": "e.g. 'downloads', 'documents', 'desktop'"}
            },
            "required": ["folder_name"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a URL in the user's web browser (optionally specify a browser like 'opera', 'chrome', 'edge', 'brave').",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "browser": {"type": "string", "description": "Optional browser name, e.g. 'opera', 'chrome', 'edge'"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_webpage_text",
        "description": "Fetch a webpage and return its visible text, for reading or summarizing a page without opening a browser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "take_note",
        "description": "Save a short note or piece of text for later.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "list_running_apps",
        "description": "List currently running desktop applications and processes sorted by memory usage.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Max apps to list (default 12)"}},
            "required": [],
        },
    },
    {
        "name": "search_files",
        "description": "Search user folders (Desktop, Documents, Downloads, Pictures) for local files or folders by name, and optionally open the top match.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "File or folder name keyword to search for"},
                "folder": {"type": "string", "description": "Optional specific folder to search inside (e.g. 'Downloads', 'Desktop', 'Documents')"},
                "open_first": {"type": "boolean", "description": "Set true to immediately open the first matching file or folder"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_notes",
        "description": "List the most recent saved notes.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "How many recent notes to show"}},
            "required": [],
        },
    },
    {
        "name": "delete_note",
        "description": "Delete a saved note matching the given keyword.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Keyword matching the note to delete"}},
            "required": ["query"],
        },
    },
    {
        "name": "set_reminder",
        "description": "Save a reminder that fires a desktop notification when due.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "when": {"type": "string", "description": "e.g. 'in 20 minutes', '5pm today', 'tomorrow at 9am'"},
            },
            "required": ["text", "when"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List pending reminders that haven't fired yet.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "delete_reminder",
        "description": "Cancel or delete a pending reminder matching the given keyword.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Keyword matching the reminder to cancel"}},
            "required": ["query"],
        },
    },
    {
        "name": "set_timer",
        "description": "Start a countdown timer that rings and shows a notification when done (e.g. '10 minutes', '45 seconds', '25 minutes').",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {"type": "string", "description": "Duration such as '10 minutes', '30 seconds', '25 minutes'"},
                "label": {"type": "string", "description": "Optional timer label (e.g. 'Pomodoro', 'Tea')"},
            },
            "required": ["duration"],
        },
    },
    {
        "name": "list_timers",
        "description": "List active countdown timers and their remaining time.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_timer",
        "description": "Cancel an active countdown timer by label (or leave label empty to cancel all active timers).",
        "input_schema": {
            "type": "object",
            "properties": {"label": {"type": "string", "description": "Optional timer label to cancel"}},
            "required": [],
        },
    },
    {
        "name": "get_system_status",
        "description": "Get live PC system health diagnostics: CPU usage %, RAM usage %, free disk space (GB), and battery status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "minimize_all_windows",
        "description": "Minimize or restore all open desktop windows (Show Desktop / Win+D).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "save_routine",
        "description": "Save or update a custom multi-step everyday routine/macro by name (e.g. 'coding mode', 'focus mode', 'goodnight').",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the routine, e.g. 'coding mode'"},
                "description": {"type": "string", "description": "Natural language description of the steps to execute when this routine is triggered"},
            },
            "required": ["name", "description"],
        },
    },
    {
        "name": "list_routines",
        "description": "List all saved everyday routines and their configured steps.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "delete_routine",
        "description": "Delete a saved routine by name.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Name of the routine to delete"}},
            "required": ["name"],
        },
    },
    {
        "name": "get_current_time",
        "description": "Get the current date and time.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "remember_fact",
        "description": (
            "Save a permanent fact, user preference, habit, personal detail, or correction into long-term memory "
            "(e.g. 'User uses Opera browser', 'Bigg Boss streams on JioHotstar (hotstar.com)', 'User's name is Alex'). "
            "Call this proactively whenever the user tells you something about themselves, their preferences, or corrects you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "Concise, clear statement of the fact, preference, or correction to remember permanently"},
                "category": {
                    "type": "string",
                    "description": "Category such as 'preference', 'correction', 'personal', 'shortcut', or 'workflow'",
                },
            },
            "required": ["fact"],
        },
    },
    {
        "name": "list_memories",
        "description": "List all long-term facts, preferences, and corrections Jarvis has learned about the user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Optional category filter"}
            },
            "required": [],
        },
    },
    {
        "name": "forget_memory",
        "description": "Delete a specific learned memory by its ID or matching text when the user asks you to forget or remove it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Memory ID (e.g. '2') or keyword matching the memory to remove"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_chat_history",
        "description": "Search the permanent archive of past saved chats to recall older conversations, links, or questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword to search in past chats (leave empty for most recent chats)"},
                "limit": {"type": "integer", "description": "Max number of chat exchanges to return (default 10)"},
            },
            "required": [],
        },
    },
    {
        "name": "clear_memory",
        "description": "Clear short-term conversation history from active context (keeps learned long-term memories).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_FUNCTIONS = {
    "open_app": lambda **kw: open_app(**kw),
    "close_app": lambda **kw: close_app(**kw),
    "list_running_apps": lambda **kw: list_running_apps(**kw),
    "search_files": lambda **kw: search_files(**kw),
    "search_web": lambda **kw: search_web(**kw),
    "get_clipboard_text": lambda **kw: get_clipboard_text(**kw),
    "set_clipboard_text": lambda **kw: set_clipboard_text(**kw),
    "analyze_screen": lambda **kw: analyze_screen(**kw),
    "set_volume": lambda **kw: set_volume(**kw),
    "get_volume": lambda **kw: get_volume(**kw),
    "mute_audio": lambda **kw: mute_audio(**kw),
    "unmute_audio": lambda **kw: unmute_audio(**kw),
    "control_media": lambda **kw: control_media(**kw),
    "get_battery_status": lambda **kw: get_battery_status(**kw),
    "get_system_status": lambda **kw: get_system_status(**kw),
    "minimize_all_windows": lambda **kw: minimize_all_windows(**kw),
    "lock_pc": lambda **kw: lock_pc(**kw),
    "take_screenshot": lambda **kw: take_screenshot(**kw),
    "set_screen_brightness": lambda **kw: set_screen_brightness(**kw),
    "get_screen_brightness": lambda **kw: get_screen_brightness(**kw),
    "open_folder": lambda **kw: open_folder(**kw),
    "open_url": lambda **kw: open_url(**kw),
    "fetch_webpage_text": lambda **kw: fetch_webpage_text(**kw),
    "take_note": lambda **kw: take_note(**kw),
    "list_notes": lambda **kw: list_notes(**kw),
    "delete_note": lambda **kw: delete_note(**kw),
    "set_reminder": lambda **kw: set_reminder(**kw),
    "list_reminders": lambda **kw: list_reminders(**kw),
    "delete_reminder": lambda **kw: delete_reminder(**kw),
    "set_timer": lambda **kw: set_timer(**kw),
    "list_timers": lambda **kw: list_timers(**kw),
    "cancel_timer": lambda **kw: cancel_timer(**kw),
    "save_routine": lambda **kw: save_routine(**kw),
    "list_routines": lambda **kw: list_routines(**kw),
    "delete_routine": lambda **kw: delete_routine(**kw),
    "get_current_time": lambda **kw: get_current_time(**kw),
    "remember_fact": lambda **kw: remember_fact(**kw),
    "list_memories": lambda **kw: list_memories(**kw),
    "forget_memory": lambda **kw: forget_memory(**kw),
    "search_chat_history": lambda **kw: search_chat_history(**kw),
    "clear_memory": lambda **kw: clear_memory(**kw),
}
