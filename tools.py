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
HISTORY_FILE = DATA_DIR / "history.json"

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


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
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


# ---- Apps ------------------------------------------------------------

def open_app(app_name: str) -> str:
    if platform.system() != "Windows":
        return "open_app is Windows-only right now."
    key = app_name.strip().lower()
    exe = APP_MAP.get(key)
    try:
        if exe:
            subprocess.Popen(exe, shell=True)
            return f"Opened {app_name}."
        subprocess.Popen(f'start "" "{app_name}"', shell=True)
        return (
            f"Tried to open '{app_name}' — it's not in my known app list, "
            f"so this is best-effort. If it didn't work, add it to APP_MAP "
            f"in tools.py with its .exe name."
        )
    except Exception as e:
        return f"Failed to open {app_name}: {e}"


def close_app(app_name: str) -> str:
    if platform.system() != "Windows":
        return "close_app is Windows-only right now."
    key = app_name.strip().lower()
    exe = APP_MAP.get(key)
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


# ---- Web & Search ------------------------------------------------------

def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
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


def lock_pc() -> str:
    """Locks the Windows workstation."""
    try:
        ctypes.windll.user32.LockWorkStation()
        return "Workstation locked."
    except Exception as e:
        return f"Failed to lock PC: {e}"


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
    """Clears persistent conversation memory."""
    if HISTORY_FILE.exists():
        try:
            HISTORY_FILE.unlink()
        except Exception:
            pass
    return "Conversation memory cleared."


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
        "description": "Open a URL in the default web browser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
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
        "name": "list_notes",
        "description": "List the most recent saved notes.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "How many recent notes to show"}},
            "required": [],
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
        "name": "get_current_time",
        "description": "Get the current date and time.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "clear_memory",
        "description": "Clear recent conversation history from memory.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_FUNCTIONS = {
    "open_app": lambda **kw: open_app(**kw),
    "close_app": lambda **kw: close_app(**kw),
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
    "lock_pc": lambda **kw: lock_pc(**kw),
    "take_screenshot": lambda **kw: take_screenshot(**kw),
    "set_screen_brightness": lambda **kw: set_screen_brightness(**kw),
    "get_screen_brightness": lambda **kw: get_screen_brightness(**kw),
    "open_folder": lambda **kw: open_folder(**kw),
    "open_url": lambda **kw: open_url(**kw),
    "fetch_webpage_text": lambda **kw: fetch_webpage_text(**kw),
    "take_note": lambda **kw: take_note(**kw),
    "list_notes": lambda **kw: list_notes(**kw),
    "set_reminder": lambda **kw: set_reminder(**kw),
    "list_reminders": lambda **kw: list_reminders(**kw),
    "get_current_time": lambda **kw: get_current_time(**kw),
    "clear_memory": lambda **kw: clear_memory(**kw),
}
