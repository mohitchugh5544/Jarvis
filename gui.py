"""
Jarvis Desktop Application — Liquid Spatial UI (Google Stitch Edition).

Provides two seamless desktop modes powered by PyQt6-WebEngine and the local
Stitch Bridge server:
  1. Main Jarvis Hub (`MainHubWindow`) — 5-view spatial workspace:
       - Chat & Voice Workspace
       - Long-Term Memory & Learning
       - System & Hardware Controls
       - Productivity Hub (Reminders, Timers, Notes, Routines)
       - Settings & iOS/Siri Shortcut Bridge
  2. Compact Floating Desktop Widget (`CompactWidgetWindow`) — frameless,
     translucent, always-on-top mini HUD with interactive Acoustic Orb,
     live telemetry sliders, and 2x2 quick action tiles.

Run:
    python gui.py
"""

import collections
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import queue
import sys
import threading
import time

# When launched windowless via pythonw.exe, sys.stdout/sys.stderr are None.
# Redirect them BEFORE importing any third-party packages (tqdm/onnx/whisper check sys.stdout).
if sys.stdout is None or sys.stderr is None:
    try:
        _early_data_dir = Path(__file__).resolve().parent / "data"
        _early_data_dir.mkdir(exist_ok=True)
        _log_file = open(_early_data_dir / "jarvis.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _log_file
        if sys.stderr is None:
            sys.stderr = _log_file
    except Exception:
        pass

# Enable hardware GPU acceleration for smooth WebEngine rendering on Windows 11
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-features=CalculateNativeWinOcclusion --enable-gpu-rasterization --enable-zero-copy",
)

import numpy as np

try:
    import sounddevice as sd
    from faster_whisper import WhisperModel
    import openwakeword
    from openwakeword.model import Model as OwwModel
    VOICE_DEPS_AVAILABLE = True
except Exception:
    VOICE_DEPS_AVAILABLE = False

try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    import winsound
except Exception:
    winsound = None

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QObject, pyqtSlot
from PyQt6.QtGui import QColor, QPainter, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSystemTrayIcon,
    QMenu,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from agent import JarvisAgent
from config import (
    BASE_DIR,
    DATA_DIR,
    JARVIS_SERVER_PORT,
    JARVIS_TTS_RATE,
    JARVIS_TTS_VOICE_INDEX,
    JARVIS_WAKE_THRESHOLD,
    JARVIS_WAKE_WORD,
    JARVIS_WHISPER_MODEL,
)
import scheduler
from stitch_bridge import StitchBridgeController, start_bridge_http_server


STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "JarvisAssistant"


def _get_startup_command() -> str:
    py_exe = os.path.abspath(sys.executable)
    pyw_exe = os.path.join(os.path.dirname(py_exe), "pythonw.exe")
    runner = pyw_exe if os.path.exists(pyw_exe) else py_exe
    script = os.path.abspath(__file__)
    return f'"{runner}" "{script}"'


def _is_startup_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, STARTUP_REG_NAME)
            return bool(val)
    except Exception:
        return False


def _set_startup_enabled(enabled: bool) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, _get_startup_command())
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"[Startup Registry Error] {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  AUDIO & BACKGROUND WORKERS (TTS, VOICE, GLOBAL HOTKEY)
# ══════════════════════════════════════════════════════════════════════

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280
SILENCE_TIMEOUT_SECONDS = 0.7
MAX_RECORDING_SECONDS = 12.0
MIN_RECORDING_SECONDS = 0.35
PRE_ROLL_CHUNKS = 5

WHISPER_INITIAL_PROMPT = (
    "Hey Jarvis. Open Notepad, Calculator, Spotify, Chrome, Edge, Opera, VS Code, "
    "close app, what time is it, set a reminder, set a timer, take a note, "
    "run my coding mode routine, check system status, Bigg Boss, JioHotstar."
)


def _play_chime(kind: str = "activate"):
    if winsound is None:
        return

    def _beep():
        try:
            if kind == "activate":
                winsound.Beep(900, 70)
                winsound.Beep(1300, 80)
            elif kind == "done":
                winsound.Beep(750, 65)
        except Exception:
            pass

    threading.Thread(target=_beep, daemon=True).start()


class TtsWorker(QThread):
    """Dedicated TTS thread so SAPI5 speech never blocks the Qt GUI or mic loop."""
    speaking_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._running = True
        self.enabled = True

    def speak(self, text: str):
        if self.enabled and text and text.strip():
            self._queue.put(text.strip())

    def stop(self):
        self._running = False
        self._queue.put(None)

    @staticmethod
    def _create_engine():
        engine = pyttsx3.init()
        engine.setProperty("rate", JARVIS_TTS_RATE)
        voices = engine.getProperty("voices")
        if voices and 0 <= JARVIS_TTS_VOICE_INDEX < len(voices):
            engine.setProperty("voice", voices[JARVIS_TTS_VOICE_INDEX].id)
        return engine

    def run(self):
        try:
            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass

        engine = None
        if pyttsx3 is not None:
            try:
                engine = self._create_engine()
            except Exception:
                engine = None

        while self._running:
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None or not self._running:
                break
            if not self.enabled or pyttsx3 is None:
                continue

            self.speaking_changed.emit(True)
            try:
                if engine is None:
                    engine = self._create_engine()
                engine.say(item)
                engine.runAndWait()
            except Exception as e:
                print(f"[GUI TTS Error] {e}")
                engine = None
            finally:
                self.speaking_changed.emit(False)


class GuiVoiceThread(QThread):
    """
    Background wake-word ('Hey Jarvis') + push-to-talk + faster-whisper STT worker.
    """
    state_changed = pyqtSignal(str)       # 'loading', 'ready', 'wake_off', 'listening', 'transcribing', 'error'
    command_recognized = pyqtSignal(str)  # transcribed user text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self.wake_enabled = False  # OFF by default to prevent false triggers
        self.busy = False
        self._manual_trigger = False
        self._calibrated = False
        self._audio_queue = queue.Queue()
        self._pre_roll = collections.deque(maxlen=PRE_ROLL_CHUNKS)
        self._speech_threshold = 320.0
        self._whisper = None
        self._oww = None

    def trigger_push_to_talk(self):
        self._manual_trigger = True

    def set_wake_enabled(self, enabled: bool):
        self.wake_enabled = bool(enabled)
        if not self.busy:
            self.state_changed.emit("ready" if self.wake_enabled else "wake_off")

    def stop(self):
        self._running = False

    def _audio_cb(self, indata, frames, time_info, status):
        self._audio_queue.put(indata.copy())

    def _calibrate(self, duration: float = 0.8):
        samples = []
        t0 = time.time()
        while time.time() - t0 < duration and self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.15)
                rms = float(np.sqrt(np.mean(chunk.flatten().astype(np.float32) ** 2)))
                samples.append(rms)
            except queue.Empty:
                continue
        if samples:
            ambient = float(np.median(samples))
            self._speech_threshold = max(200.0, ambient * 2.0)

    def _transcribe(self, audio_float32: np.ndarray) -> str:
        peak = np.max(np.abs(audio_float32))
        if peak > 1e-4:
            audio_float32 = (audio_float32 / peak) * 0.95
        segments, _ = self._whisper.transcribe(
            audio_float32,
            language="en",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=WHISPER_INITIAL_PROMPT,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300),
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def _record_and_transcribe(self):
        self.state_changed.emit("listening")
        _play_chime("activate")

        recorded = list(self._pre_roll)
        speech_started = False
        silence_start = None
        t0 = time.time()

        for c in recorded:
            if np.sqrt(np.mean(c.astype(np.float32) ** 2)) >= self._speech_threshold:
                speech_started = True
                break

        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.3)
            except queue.Empty:
                chunk = None

            if chunk is None:
                continue

            flat = chunk.flatten()
            recorded.append(flat)
            rms = float(np.sqrt(np.mean(flat.astype(np.float32) ** 2)))
            elapsed = time.time() - t0

            if rms >= self._speech_threshold:
                speech_started = True
                silence_start = None
            else:
                if speech_started:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start >= SILENCE_TIMEOUT_SECONDS:
                        break

            if not speech_started and elapsed >= 4.5:
                self.state_changed.emit("ready" if self.wake_enabled else "wake_off")
                return

            if elapsed >= MAX_RECORDING_SECONDS:
                break

        _play_chime("done")
        if not recorded:
            self.state_changed.emit("ready" if self.wake_enabled else "wake_off")
            return

        full_int16 = np.concatenate(recorded)
        if len(full_int16) / SAMPLE_RATE < MIN_RECORDING_SECONDS:
            self.state_changed.emit("ready" if self.wake_enabled else "wake_off")
            return

        self.state_changed.emit("transcribing")
        audio_f32 = full_int16.astype(np.float32) / 32768.0
        try:
            text = self._transcribe(audio_f32)
        except Exception as e:
            print(f"[GUI Whisper Error] {e}")
            text = ""

        self.state_changed.emit("ready" if self.wake_enabled else "wake_off")
        if not text:
            return

        cleaned = text
        for prefix in ("hey jarvis,", "hey jarvis", "jarvis,", "jarvis"):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix):].strip(" ,:.-")
                break
        if not cleaned:
            cleaned = text

        self.command_recognized.emit(cleaned)

    def _ensure_models_loaded(self) -> bool:
        if self._whisper is not None and self._oww is not None:
            return True
        self.state_changed.emit("loading")
        try:
            if self._whisper is None:
                self._whisper = WhisperModel(
                    JARVIS_WHISPER_MODEL,
                    device="cpu",
                    compute_type="int8",
                )
            if self._oww is None:
                try:
                    self._oww = OwwModel(
                        wakeword_models=[JARVIS_WAKE_WORD],
                        inference_framework="onnx",
                    )
                except Exception:
                    openwakeword.utils.download_models()
                    self._oww = OwwModel(
                        wakeword_models=[JARVIS_WAKE_WORD],
                        inference_framework="onnx",
                    )
            return True
        except Exception as e:
            print(f"[GUI Voice Init Error] {e}")
            self.state_changed.emit("error")
            return False

    def run(self):
        if not VOICE_DEPS_AVAILABLE:
            self.state_changed.emit("error")
            return

        self.state_changed.emit("ready" if self.wake_enabled else "wake_off")

        while self._running:
            # Stay completely idle (mic closed) while Voice is OFF and no manual trigger is requested
            if not self.wake_enabled and not self._manual_trigger:
                time.sleep(0.08)
                continue

            if not self._ensure_models_loaded():
                self._manual_trigger = False
                self.wake_enabled = False
                time.sleep(0.5)
                continue

            try:
                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=CHUNK_SAMPLES,
                    callback=self._audio_cb,
                ):
                    if not self._calibrated:
                        self._calibrate(0.6)
                        self._calibrated = True
                    self.state_changed.emit("ready" if self.wake_enabled else "wake_off")

                    while self._running and (self.wake_enabled or self._manual_trigger):
                        try:
                            chunk = self._audio_queue.get(timeout=0.08)
                        except queue.Empty:
                            chunk = None

                        if self.busy:
                            if self._oww:
                                self._oww.reset()
                            self._pre_roll.clear()
                            continue

                        if self._manual_trigger:
                            self._manual_trigger = False
                            if self._oww:
                                self._oww.reset()
                            self._record_and_transcribe()
                            if self._oww:
                                self._oww.reset()
                            self._pre_roll.clear()
                            continue

                        if chunk is None:
                            continue

                        flat = chunk.flatten()
                        self._pre_roll.append(flat)

                        if not self.wake_enabled:
                            continue

                        prediction = self._oww.predict(flat)
                        score = float(prediction.get(JARVIS_WAKE_WORD, 0.0))
                        if score >= JARVIS_WAKE_THRESHOLD:
                            self._oww.reset()
                            self._record_and_transcribe()
                            self._oww.reset()
                            self._pre_roll.clear()

                # Clear stale audio queue after closing input stream
                while not self._audio_queue.empty():
                    try:
                        self._audio_queue.get_nowait()
                    except queue.Empty:
                        break
                self._pre_roll.clear()
                self.state_changed.emit("ready" if self.wake_enabled else "wake_off")
            except Exception as e:
                print(f"[GUI Audio Stream Error] {e}")
                self.state_changed.emit("error")
                time.sleep(0.5)


class HotkeyThread(QThread):
    """Listens for global Ctrl+Alt+J (toggle) and Ctrl+Alt+Space (push-to-talk) via Win32 RegisterHotKey."""
    pressed = pyqtSignal()
    ptt_pressed = pyqtSignal()

    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    VK_J = 0x4A
    VK_SPACE = 0x20
    WM_HOTKEY = 0x0312

    def run(self):
        user32 = ctypes.windll.user32
        user32.RegisterHotKey(None, 1, self.MOD_CONTROL | self.MOD_ALT, self.VK_J)
        user32.RegisterHotKey(None, 2, self.MOD_CONTROL | self.MOD_ALT, self.VK_SPACE)
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == self.WM_HOTKEY:
                    if msg.wParam == 1:
                        self.pressed.emit()
                    elif msg.wParam == 2:
                        self.ptt_pressed.emit()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, 1)
            user32.UnregisterHotKey(None, 2)


# ══════════════════════════════════════════════════════════════════════
#  STITCH WEBENGINE WINDOWS
# ══════════════════════════════════════════════════════════════════════

def _apply_win11_rounded_corners(widget):
    """Applies native Windows 11 DWM rounded corners without WA_TranslucentBackground flicker."""
    try:
        hwnd = int(widget.winId())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        val = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(val), ctypes.sizeof(val)
        )
    except Exception:
        pass


class CompactWidgetWindow(QMainWindow):
    """Frameless floating desktop widget (`/widget`)."""

    def __init__(self, url: str):
        super().__init__()
        self.setWindowTitle("Jarvis Floating Widget")
        self.pinned = True
        self.setFixedSize(356, 652)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setStyleSheet("background-color: #0d0e11;")

        self.view = QWebEngineView(self)
        self.view.page().setBackgroundColor(QColor("#0d0e11"))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setUrl(QUrl(url))
        self.setCentralWidget(self.view)
        _apply_win11_rounded_corners(self)

    def showEvent(self, event):
        super().showEvent(event)
        _apply_win11_rounded_corners(self)

    def set_pinned(self, pinned: bool):
        self.pinned = bool(pinned)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        _apply_win11_rounded_corners(self)


class MainHubWindow(QMainWindow):
    """Full 5-Tab Jarvis Spatial Workspace (`/hub`) — lazily loaded on demand."""

    def __init__(self, url: str):
        super().__init__()
        self.setWindowTitle("JARVIS — Spatial Neural Core")
        self.pinned = False
        self._target_url = url
        self._loaded = False
        self.resize(1360, 860)
        self.setMinimumSize(960, 640)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setStyleSheet("background-color: #121316;")

        self.view = QWebEngineView(self)
        self.view.page().setBackgroundColor(QColor("#121316"))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCentralWidget(self.view)
        _apply_win11_rounded_corners(self)

    def ensure_loaded(self):
        if not self._loaded:
            self._loaded = True
            self.view.setUrl(QUrl(self._target_url))

    def showEvent(self, event):
        self.ensure_loaded()
        super().showEvent(event)
        _apply_win11_rounded_corners(self)

    def set_pinned(self, pinned: bool):
        self.pinned = bool(pinned)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if self.pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        _apply_win11_rounded_corners(self)


# ══════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════

class _GuiActionDispatcher(QObject):
    """Marshals window actions from the HTTP server thread onto the Qt GUI thread."""
    request_received = pyqtSignal(dict)


class JarvisStitchApp(QObject):
    """Coordinates the Stitch WebEngine windows, voice/TTS threads, and system tray."""

    def __init__(self, app: QApplication, start_in_widget_mode: bool = True):
        super().__init__()
        self._app = app
        self._agent = JarvisAgent()
        self._notified_hide_shortcut = False

        # Background TTS & Voice workers
        self._tts = TtsWorker()
        self._tts.start()

        self._voice = GuiVoiceThread()
        self._dispatcher = _GuiActionDispatcher()
        self._dispatcher.request_received.connect(self._execute_window_action_on_main_thread)

        # Bridge Controller + local HTTP server
        self._controller = StitchBridgeController(
            agent=self._agent,
            tts_worker=self._tts,
            voice_thread=self._voice,
            window_callback=self._on_bridge_window_request,
        )
        self._port = start_bridge_http_server(self._controller)

        # Connect Voice & TTS signals to Bridge Controller
        self._voice.state_changed.connect(self._on_voice_state_changed)
        self._voice.command_recognized.connect(self._on_voice_command)
        self._tts.speaking_changed.connect(self._on_tts_speaking_changed)
        self._voice.start()

        # Create Windows (MainHubWindow is lazy-loaded only when opened)
        self._widget_win = CompactWidgetWindow(f"http://127.0.0.1:{self._port}/widget")
        self._hub_win = MainHubWindow(f"http://127.0.0.1:{self._port}/hub")

        self._position_windows()
        self._setup_tray()

        self._hotkey = HotkeyThread()
        self._hotkey.pressed.connect(self._toggle_visibility)
        self._hotkey.ptt_pressed.connect(self._on_ptt_pressed)
        self._hotkey.start()

        if start_in_widget_mode:
            self._widget_win.show()
            self._widget_win.raise_()
            self._widget_win.activateWindow()
        else:
            self._hub_win.ensure_loaded()
            self._hub_win.show()
            self._hub_win.raise_()
            self._hub_win.activateWindow()

    def _position_windows(self):
        screen = self._app.primaryScreen().availableGeometry()
        # Center Main Hub Window
        hx = screen.x() + max(0, (screen.width() - self._hub_win.width()) // 2)
        hy = screen.y() + max(0, (screen.height() - self._hub_win.height()) // 2)
        self._hub_win.move(hx, hy)

        # Position Compact Floating Widget on top-right
        wx = screen.right() - self._widget_win.width() - 20
        wy = screen.top() + 30
        self._widget_win.move(wx, wy)

    def _on_voice_state_changed(self, state: str):
        if state in ("listening", "transcribing", "loading", "error"):
            self._controller.assistant_state = state
        elif not self._controller.thinking:
            self._controller.assistant_state = "ready"

    def _on_voice_command(self, text: str):
        self._controller.dispatch_command(text, is_voice=True)

    def _on_tts_speaking_changed(self, is_speaking: bool):
        if is_speaking:
            self._controller.assistant_state = "speaking"
            self._voice.busy = True
        else:
            if not self._controller.thinking:
                self._controller.assistant_state = "ready"
                self._voice.busy = False

    def _on_bridge_window_request(self, payload: dict) -> dict:
        self._dispatcher.request_received.emit(payload)
        return {"ok": True}

    @pyqtSlot(dict)
    def _execute_window_action_on_main_thread(self, payload: dict):
        target = payload.get("target", "hub")
        action = payload.get("action", "")
        win = self._widget_win if target == "widget" else self._hub_win

        if action == "start_drag":
            wh = win.windowHandle()
            if wh is not None and wh.startSystemMove():
                return
            try:
                hwnd = int(win.winId())
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.SendMessageW(hwnd, 0x00A1, 2, 0)
            except Exception:
                pass
        elif action == "move_by":
            dx = int(payload.get("dx", 0))
            dy = int(payload.get("dy", 0))
            win.move(win.x() + dx, win.y() + dy)
        elif action == "resize":
            w = max(280, int(payload.get("width", 356)))
            h = max(100, int(payload.get("height", 652)))
            self._widget_win.setFixedSize(w, h)
            _apply_win11_rounded_corners(self._widget_win)
        elif action == "toggle_pin":
            win.set_pinned(not win.pinned)
        elif action == "collapse":
            self._widget_win.setFixedSize(356, 148)
            _apply_win11_rounded_corners(self._widget_win)
        elif action == "expand_pill":
            self._widget_win.setFixedSize(356, 652)
            _apply_win11_rounded_corners(self._widget_win)
        elif action == "hide_widget":
            self._widget_win.hide()
            if not self._notified_hide_shortcut and hasattr(self, "_tray"):
                self._notified_hide_shortcut = True
                self._tray.showMessage(
                    "Jarvis Popup Hidden",
                    "Press Ctrl+Alt+J anytime to bring the popup back.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2500,
                )
        elif action == "show":
            self._widget_win.hide()
            self._hub_win.ensure_loaded()
            self._hub_win.showNormal()
            self._hub_win.raise_()
            self._hub_win.activateWindow()
            view_name = payload.get("view")
            if view_name:
                self._hub_win.view.page().runJavaScript(
                    f"if(window.switchJarvisView) window.switchJarvisView('{view_name}');"
                )
        elif action == "focus_widget_mode":
            self._hub_win.hide()
            self._widget_win.show()
            self._widget_win.raise_()
            self._widget_win.activateWindow()
        elif action == "minimize":
            win.showMinimized()
        elif action == "toggle_maximize":
            if win.isMaximized():
                win.showNormal()
            else:
                win.showMaximized()
        elif action == "hide":
            # When closing Main Hub, drop into the Compact Floating Widget
            self._hub_win.hide()
            self._widget_win.show()
            self._widget_win.raise_()

    def _toggle_visibility(self):
        """Global hotkey (Ctrl+Alt+J) or tray click: toggles popup visibility or returns from Hub to popup."""
        if self._hub_win.isVisible():
            self._hub_win.hide()
            self._widget_win.show()
            self._widget_win.raise_()
            self._widget_win.activateWindow()
        elif self._widget_win.isVisible():
            self._widget_win.hide()
        else:
            self._widget_win.show()
            self._widget_win.raise_()
            self._widget_win.activateWindow()

    def _on_ptt_pressed(self):
        """Global push-to-talk hotkey (Ctrl+Alt+Space): triggers one-shot listening from anywhere."""
        if not self._widget_win.isVisible() and not self._hub_win.isVisible():
            self._widget_win.show()
            self._widget_win.raise_()
        self._voice.trigger_push_to_talk()

    def _setup_tray(self):
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#4b8eff"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.setBrush(QColor("#121316"))
        p.drawEllipse(10, 10, 12, 12)
        p.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self._app)
        self._tray.setToolTip("JARVIS — Spatial Neural Core (Ctrl+Alt+J)")

        menu = QMenu()
        act_hub = menu.addAction("Open Jarvis Hub")
        act_hub.triggered.connect(lambda: self._execute_window_action_on_main_thread({"target": "hub", "action": "show"}))

        act_widget = menu.addAction("Switch to Floating Widget")
        act_widget.triggered.connect(lambda: self._execute_window_action_on_main_thread({"target": "widget", "action": "focus_widget_mode"}))

        menu.addSeparator()
        self._act_startup = menu.addAction("Launch on Windows Startup")
        self._act_startup.setCheckable(True)
        self._act_startup.setChecked(_is_startup_enabled())
        self._act_startup.triggered.connect(self._toggle_startup)

        menu.addSeparator()
        act_quit = menu.addAction("Quit Jarvis")
        act_quit.triggered.connect(self._quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_click)
        self._tray.show()

    def _toggle_startup(self, checked: bool):
        _set_startup_enabled(bool(checked))
        self._act_startup.setChecked(_is_startup_enabled())

    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visibility()

    def _quit(self):
        self._voice.stop()
        self._tts.stop()
        self._tray.hide()
        self._app.quit()


# ══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

def main():
    if "--install-startup" in sys.argv:
        ok = _set_startup_enabled(True)
        print("Registered Jarvis in Windows Startup." if ok else "Failed to register startup.")
        return
    if "--uninstall-startup" in sys.argv:
        ok = _set_startup_enabled(False)
        print("Removed Jarvis from Windows Startup." if ok else "Failed to remove startup.")
        return

    # Single-instance check: if Jarvis is already running, wake its popup and exit
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JarvisAssistantSingleton")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://127.0.0.1:{JARVIS_SERVER_PORT}/api/window",
                data=b'{"target":"widget","action":"focus_widget_mode"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=1.5)
        except Exception:
            pass
        return

    scheduler.start()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    start_in_widget = "--hub" not in sys.argv
    _jarvis = JarvisStitchApp(app, start_in_widget_mode=start_in_widget)
    print(f"Jarvis Liquid Spatial UI active on port {_jarvis._port} (0.0.0.0:{_jarvis._port}). Press Ctrl+Alt+J to show/hide popup.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
