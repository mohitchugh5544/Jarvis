"""
Jarvis HUD Overlay — Iron Man-style floating desktop widget.

A transparent, always-on-top floating HUD that shows system vitals
(time, battery, volume, brightness) and expands into a full chat
interface.  Communicates with the JarvisAgent directly (same process).

Run:
    python gui.py
"""

import sys
import ctypes
from ctypes import wintypes
from datetime import datetime

import psutil
import screen_brightness_control as sbc
from pycaw.pycaw import AudioUtilities

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QRectF
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QFont, QIcon, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFrame, QScrollArea, QSystemTrayIcon,
    QMenu,
)

from agent import JarvisAgent
from tools import take_screenshot, control_media, mute_audio, unmute_audio, lock_pc
import scheduler


# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════

# Palette
COL_BG         = QColor(10, 15, 25, 200)
COL_ACCENT     = QColor(0, 212, 255)
COL_ACCENT_DIM = QColor(0, 212, 255, 80)
COL_TEXT       = QColor(220, 235, 245)
COL_TEXT_DIM   = QColor(138, 188, 212)
COL_SEP        = "rgba(0, 212, 255, 40)"

# Chat bubbles (CSS)
COL_USER_BG    = "#192850DD"
COL_USER_BD    = "#1a4a8a"
COL_BOT_BG     = "#0A323CDD"
COL_BOT_BD     = "#0d6070"
COL_SYS_BG     = "#1a1a2eCC"
COL_SYS_BD     = "#333355"

# Layout
HUD_W, HUD_H   = 170, 360
CHAT_W, CHAT_H  = 420, 620
RADIUS           = 18
GLOW             = 4
FONT             = "Segoe UI"


# ══════════════════════════════════════════════════════════════════════
#  SYSTEM STATS HELPERS
# ══════════════════════════════════════════════════════════════════════

def _read_battery():
    """Returns (percent | None, charging: bool)."""
    try:
        b = psutil.sensors_battery()
        return (b.percent, b.power_plugged) if b else (None, False)
    except Exception:
        return (None, False)


def _read_volume():
    """Returns (percent | None, muted: bool)."""
    try:
        vol = AudioUtilities.GetSpeakers().EndpointVolume
        return (int(round(vol.GetMasterVolumeLevelScalar() * 100)),
                bool(vol.GetMute()))
    except Exception:
        return (None, False)


def _read_brightness():
    """Returns percent | None."""
    try:
        b = sbc.get_brightness()
        return b[0] if isinstance(b, list) else b
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
#  BASE PANEL — translucent rounded rectangle with cyan glow border
# ══════════════════════════════════════════════════════════════════════

class GlowPanel(QWidget):
    """Frameless, translucent, always-on-top widget with a painted glow."""

    def __init__(self, w, h, radius=RADIUS, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self._radius = radius
        self._drag_start = None
        self._drag_offset = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool          # stays off the taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    # ── painting ──────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = GLOW + 2
        body = self.rect().adjusted(inset, inset, -inset, -inset)

        # outer glow rings
        for i in range(GLOW):
            pen = QPen(QColor(0, 212, 255, 35 - i * 8))
            pen.setWidthF(2.5 - i * 0.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = body.adjusted(-i - 1, -i - 1, i + 1, i + 1)
            p.drawRoundedRect(QRectF(r), self._radius + i, self._radius + i)

        # dark background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COL_BG)
        p.drawRoundedRect(QRectF(body), self._radius, self._radius)

        # crisp border
        pen = QPen(COL_ACCENT_DIM)
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(body), self._radius, self._radius)

    # ── dragging ──────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._drag_offset = self._drag_start - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_offset and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self._drag_offset = None

    # ── prevent accidental destruction ────────────────────────────────

    def closeEvent(self, event):
        event.ignore()
        self.hide()


# ══════════════════════════════════════════════════════════════════════
#  STAT PROGRESS BAR  (thin 3 px horizontal bar)
# ══════════════════════════════════════════════════════════════════════

class StatBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._value = 0

    def setValue(self, v):
        self._value = max(0, min(100, int(v)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # track
        p.setBrush(QColor(255, 255, 255, 15))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 1.5, 1.5)
        # fill
        fw = int(self.width() * self._value / 100)
        if fw > 0:
            p.setBrush(COL_ACCENT)
            p.drawRoundedRect(QRectF(0, 0, fw, self.height()), 1.5, 1.5)


# ══════════════════════════════════════════════════════════════════════
#  CHAT MESSAGE BUBBLE
# ══════════════════════════════════════════════════════════════════════

class MessageBubble(QWidget):
    """Single chat bubble — user (right-aligned, blue) or Jarvis (left, teal)."""

    def __init__(self, text, is_user=True, is_system=False, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(300)
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        if is_system:
            bg, bd = COL_SYS_BG, COL_SYS_BD
            self._label.setMaximumWidth(350)
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addStretch()
            layout.addWidget(self._label)
            layout.addStretch()
        elif is_user:
            bg, bd = COL_USER_BG, COL_USER_BD
            layout.addStretch()
            layout.addWidget(self._label)
        else:
            bg, bd = COL_BOT_BG, COL_BOT_BD
            layout.addWidget(self._label)
            layout.addStretch()

        self._label.setStyleSheet(f"""
            QLabel {{
                color: #DCE9F5;
                background: {bg};
                border: 1px solid {bd};
                border-radius: 12px;
                padding: 8px 14px;
                font-family: '{FONT}';
                font-size: 13px;
            }}
        """)

    def set_text(self, text):
        self._label.setText(text)


# ══════════════════════════════════════════════════════════════════════
#  BACKGROUND WORKERS
# ══════════════════════════════════════════════════════════════════════

class AgentWorker(QThread):
    """Runs JarvisAgent.run() off the GUI thread so the UI never freezes."""
    result_ready = pyqtSignal(str)

    def __init__(self, agent, text, parent=None):
        super().__init__(parent)
        self._agent = agent
        self._text = text

    def run(self):
        try:
            reply = self._agent.run(self._text)
        except Exception as e:
            reply = f"Error: {e}"
        self.result_ready.emit(reply)


class HotkeyThread(QThread):
    """Listens for a global Ctrl+Shift+J hotkey via the Windows API."""
    triggered = pyqtSignal()
    _HOTKEY_ID = 9001

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tid = None

    def run(self):
        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        MOD = 0x0002 | 0x0004           # CTRL + SHIFT
        if not user32.RegisterHotKey(None, self._HOTKEY_ID, MOD, ord("J")):
            return                       # silently skip on failure
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:    # WM_HOTKEY
                self.triggered.emit()
        user32.UnregisterHotKey(None, self._HOTKEY_ID)

    def stop(self):
        """Post WM_QUIT to break out of the GetMessageW loop."""
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)


# ══════════════════════════════════════════════════════════════════════
#  COMPACT HUD WIDGET  (time, battery, volume, brightness)
# ══════════════════════════════════════════════════════════════════════

class HudWidget(GlowPanel):
    """Small floating pill showing time + system vitals."""
    expand_requested = pyqtSignal()

    def __init__(self):
        super().__init__(HUD_W, HUD_H)
        self._build_ui()
        self._start_timers()
        self._position_default()

    # ── build ─────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        m = GLOW + 8
        root.setContentsMargins(m + 6, m + 4, m + 6, m + 4)
        root.setSpacing(0)

        # header
        hdr = QLabel("JARVIS")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setStyleSheet(f"""
            color: {COL_ACCENT.name()};  font-family: '{FONT}';
            font-size: 13px; font-weight: bold;
            letter-spacing: 4px; background: transparent;
        """)
        root.addWidget(hdr)
        root.addSpacing(4)
        root.addWidget(self._sep())
        root.addSpacing(6)

        # time
        self._time_lbl = QLabel("--:--")
        self._time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_lbl.setStyleSheet(f"""
            color: white; font-family: '{FONT}';
            font-size: 26px; font-weight: 300; background: transparent;
        """)
        root.addWidget(self._time_lbl)

        self._date_lbl = QLabel("---")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._date_lbl.setStyleSheet(f"""
            color: {COL_TEXT_DIM.name()}; font-family: '{FONT}';
            font-size: 10px; background: transparent;
        """)
        root.addWidget(self._date_lbl)
        root.addSpacing(6)
        root.addWidget(self._sep())
        root.addSpacing(8)

        # stat rows
        self._bat_lbl, self._bat_bar = self._make_stat("BAT")
        self._vol_lbl, self._vol_bar = self._make_stat("VOL")
        self._bri_lbl, self._bri_bar = self._make_stat("BRI")
        for lbl, bar in [
            (self._bat_lbl, self._bat_bar),
            (self._vol_lbl, self._vol_bar),
            (self._bri_lbl, self._bri_bar),
        ]:
            root.addWidget(lbl)
            root.addWidget(bar)
            root.addSpacing(8)

        root.addWidget(self._sep())
        root.addSpacing(8)

        # expand button
        btn = QPushButton("💬")
        btn.setFixedSize(38, 38)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0,212,255,25);
                border: 2px solid {COL_ACCENT.name()};
                border-radius: 19px; color: {COL_ACCENT.name()};
                font-size: 16px;
            }}
            QPushButton:hover {{ background: rgba(0,212,255,55); }}
        """)
        btn.clicked.connect(self.expand_requested.emit)
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addStretch(); wl.addWidget(btn); wl.addStretch()
        root.addWidget(wrap)
        root.addStretch()

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _sep():
        s = QFrame()
        s.setFixedHeight(1)
        s.setStyleSheet(f"background: {COL_SEP};")
        return s

    def _make_stat(self, tag):
        lbl = QLabel(f"{tag}   ---")
        lbl.setStyleSheet(f"""
            color: {COL_TEXT_DIM.name()}; font-family: '{FONT}';
            font-size: 11px; letter-spacing: 2px; background: transparent;
        """)
        return lbl, StatBar()

    # ── timers ────────────────────────────────────────────────────────

    def _start_timers(self):
        t1 = QTimer(self)
        t1.timeout.connect(self._tick_clock)
        t1.start(1000)

        t2 = QTimer(self)
        t2.timeout.connect(self._tick_stats)
        t2.start(5000)

        self._tick_clock()
        self._tick_stats()

    def _tick_clock(self):
        now = datetime.now()
        self._time_lbl.setText(now.strftime("%I:%M %p").lstrip("0"))
        self._date_lbl.setText(now.strftime("%a, %b %d"))

    def _tick_stats(self):
        # battery
        pct, plugged = _read_battery()
        if pct is not None:
            icon = " ⚡" if plugged else ""
            self._bat_lbl.setText(f"BAT   {pct}%{icon}")
            self._bat_bar.setValue(pct)
        else:
            self._bat_lbl.setText("BAT   N/A")
            self._bat_bar.setValue(0)

        # volume
        vol, muted = _read_volume()
        if vol is not None:
            icon = " 🔇" if muted else ""
            self._vol_lbl.setText(f"VOL   {vol}%{icon}")
            self._vol_bar.setValue(vol)
        else:
            self._vol_lbl.setText("VOL   N/A")
            self._vol_bar.setValue(0)

        # brightness
        bri = _read_brightness()
        if bri is not None:
            self._bri_lbl.setText(f"BRI   {bri}%")
            self._bri_bar.setValue(bri)
        else:
            self._bri_lbl.setText("BRI   N/A")
            self._bri_bar.setValue(0)

    # ── positioning ───────────────────────────────────────────────────

    def _position_default(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.width() - self.width() - 20,
                  (scr.height() - self.height()) // 2)

    # ── click-to-expand (only if the user didn't drag) ───────────────

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start:
            delta = event.globalPosition().toPoint() - self._drag_start
            if abs(delta.x()) + abs(delta.y()) < 6:
                self.expand_requested.emit()
        super().mouseReleaseEvent(event)


# ══════════════════════════════════════════════════════════════════════
#  EXPANDED CHAT PANEL
# ══════════════════════════════════════════════════════════════════════

class ChatPanel(GlowPanel):
    """Full chat interface — messages, text input, quick-action bar."""
    minimize_requested = pyqtSignal()

    def __init__(self, agent):
        super().__init__(CHAT_W, CHAT_H)
        self._agent = agent
        self._worker = None
        self._thinking_bubble = None
        self._thinking_timer = None
        self._thinking_dots = 0
        self._build_ui()
        self._position_center()
        # welcome message
        self._add_message("Systems online. How can I help?", is_user=False)

    # ── ui build ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        m = GLOW + 6
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(0)

        # ── header ────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header.setFixedHeight(44)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 0, 4, 0)

        title = QLabel("⬡  JARVIS")
        title.setStyleSheet(f"""
            color: {COL_ACCENT.name()}; font-family: '{FONT}';
            font-size: 16px; font-weight: bold;
            letter-spacing: 3px; background: transparent;
        """)
        hl.addWidget(title)
        hl.addStretch()

        min_btn = QPushButton("─")
        min_btn.setFixedSize(30, 30)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.setStyleSheet(f"""
            QPushButton {{
                color: {COL_ACCENT.name()}; background: transparent;
                border: 1px solid {COL_ACCENT_DIM.name()};
                border-radius: 15px; font-size: 14px;
            }}
            QPushButton:hover {{ background: rgba(0,212,255,40); }}
        """)
        min_btn.clicked.connect(self.minimize_requested.emit)
        hl.addWidget(min_btn)
        root.addWidget(header)
        root.addWidget(self._sep())

        # ── chat area ─────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: rgba(255,255,255,8); width: 6px; border: none;
            }
            QScrollBar::handle:vertical {
                background: rgba(0,212,255,60); border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._chat_layout = QVBoxLayout(container)
        self._chat_layout.setContentsMargins(4, 8, 4, 8)
        self._chat_layout.setSpacing(4)
        self._chat_layout.addStretch()          # keeps bubbles pinned to top
        self._scroll.setWidget(container)
        root.addWidget(self._scroll, 1)         # stretch factor = 1

        # ── input row ─────────────────────────────────────────────────
        root.addWidget(self._sep())
        inp_row = QWidget()
        inp_row.setStyleSheet("background: transparent;")
        inp_row.setFixedHeight(46)
        il = QHBoxLayout(inp_row)
        il.setContentsMargins(6, 6, 6, 4)
        il.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Message JARVIS...")
        self._input.setStyleSheet(f"""
            QLineEdit {{
                color: #DCE9F5; background: rgba(255,255,255,5);
                border: 1px solid {COL_ACCENT_DIM.name()};
                border-radius: 14px; padding: 6px 14px;
                font-family: '{FONT}'; font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1.5px solid {COL_ACCENT.name()}; }}
        """)
        self._input.returnPressed.connect(self._send)
        il.addWidget(self._input)

        send = QPushButton("➤")
        send.setFixedSize(34, 34)
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {COL_ACCENT.name()};  border: none;
                border-radius: 17px; color: #0A0F19;
                font-size: 16px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #33E0FF; }}
        """)
        send.clicked.connect(self._send)
        il.addWidget(send)
        root.addWidget(inp_row)

        # ── quick-action bar ──────────────────────────────────────────
        acts = QWidget()
        acts.setStyleSheet("background: transparent;")
        acts.setFixedHeight(40)
        al = QHBoxLayout(acts)
        al.setContentsMargins(6, 2, 6, 6)
        al.setSpacing(6)

        for icon, tip, fn in [
            ("📷",  "Screenshot",      lambda: self._quick(take_screenshot)),
            ("⏮",  "Previous track",  lambda: self._quick(lambda: control_media(action="previous"))),
            ("⏯",  "Play / Pause",    lambda: self._quick(lambda: control_media(action="play_pause"))),
            ("⏭",  "Next track",      lambda: self._quick(lambda: control_media(action="next"))),
        ]:
            b = QPushButton(icon)
            b.setFixedSize(34, 30)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background: rgba(0,212,255,15);
                    border: 1px solid rgba(0,212,255,40);
                    border-radius: 8px; font-size: 14px;
                }
                QPushButton:hover { background: rgba(0,212,255,40); }
            """)
            b.clicked.connect(fn)
            al.addWidget(b)
        al.addStretch()
        root.addWidget(acts)

    @staticmethod
    def _sep():
        s = QFrame()
        s.setFixedHeight(1)
        s.setStyleSheet(f"background: {COL_SEP};")
        return s

    # ── messaging ─────────────────────────────────────────────────────

    def _add_message(self, text, is_user=True, is_system=False):
        bubble = MessageBubble(text, is_user=is_user, is_system=is_system)
        # insert before the trailing stretch
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        QTimer.singleShot(20, self._scroll_bottom)

    def _scroll_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _send(self):
        text = self._input.text().strip()
        if not text or self._worker is not None:
            return
        self._input.clear()
        self._add_message(text, is_user=True)
        self._show_thinking()

        self._worker = AgentWorker(self._agent, text, parent=self)
        self._worker.result_ready.connect(self._on_reply)
        self._worker.finished.connect(self._on_done)
        self._worker.start()
        self._input.setEnabled(False)

    def _on_reply(self, reply):
        self._hide_thinking()
        self._add_message(reply, is_user=False)

    def _on_done(self):
        self._worker = None
        self._input.setEnabled(True)
        self._input.setFocus()

    # ── thinking animation ────────────────────────────────────────────

    def _show_thinking(self):
        self._thinking_dots = 0
        self._thinking_bubble = MessageBubble("Thinking.", is_user=False)
        self._chat_layout.insertWidget(
            self._chat_layout.count() - 1, self._thinking_bubble
        )
        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._animate_dots)
        self._thinking_timer.start(400)
        QTimer.singleShot(20, self._scroll_bottom)

    def _animate_dots(self):
        self._thinking_dots = (self._thinking_dots + 1) % 4
        if self._thinking_bubble:
            self._thinking_bubble.set_text("Thinking" + "." * (self._thinking_dots + 1))

    def _hide_thinking(self):
        if self._thinking_timer:
            self._thinking_timer.stop()
            self._thinking_timer = None
        if self._thinking_bubble:
            self._thinking_bubble.setParent(None)
            self._thinking_bubble.deleteLater()
            self._thinking_bubble = None

    # ── quick actions (direct tool calls, no LLM round-trip) ─────────

    def _quick(self, fn):
        try:
            result = str(fn())
        except Exception as e:
            result = f"Error: {e}"
        self._add_message(result, is_user=False, is_system=True)

    # ── positioning & keyboard ────────────────────────────────────────

    def _position_center(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move((scr.width() - self.width()) // 2,
                  (scr.height() - self.height()) // 2)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.minimize_requested.emit()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.minimize_requested.emit()


# ══════════════════════════════════════════════════════════════════════
#  SYSTEM TRAY ICON
# ══════════════════════════════════════════════════════════════════════

def _create_tray_icon() -> QIcon:
    """Programmatically draw a cyan 'J' on a dark circle."""
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(COL_ACCENT)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(QColor(10, 15, 25))
    p.setFont(QFont(FONT, 30, QFont.Weight.Bold))
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "J")
    p.end()
    return QIcon(px)


# ══════════════════════════════════════════════════════════════════════
#  APPLICATION CONTROLLER — ties HUD, chat, tray, hotkey together
# ══════════════════════════════════════════════════════════════════════

class JarvisOverlay:

    def __init__(self):
        scheduler.start()
        self._agent = JarvisAgent()

        # widgets
        self.hud = HudWidget()
        self.chat = ChatPanel(self._agent)
        self.chat.hide()

        # signals: HUD <-> Chat
        self.hud.expand_requested.connect(self._show_chat)
        self.chat.minimize_requested.connect(self._show_hud)

        # tray icon
        self._tray = QSystemTrayIcon(_create_tray_icon())
        self._tray.setToolTip("Jarvis")
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #0A0F19; color: #DCE9F5;
                border: 1px solid rgba(0,212,255,80); padding: 4px;
            }
            QMenu::item:selected { background: rgba(0,212,255,40); }
            QMenu::separator {
                height: 1px; background: rgba(0,212,255,30);
                margin: 4px 8px;
            }
        """)
        menu.addAction("Show HUD",      self._show_hud)
        menu.addAction("Open Chat",     self._show_chat)
        menu.addSeparator()
        menu.addAction("Screenshot",    lambda: self._tray_action(take_screenshot))
        menu.addAction("Mute / Unmute", lambda: self._tray_action(self._toggle_mute))
        menu.addAction("Lock PC",       lambda: self._tray_action(lock_pc))
        menu.addSeparator()
        menu.addAction("Quit",          QApplication.instance().quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_click)
        self._tray.show()

        # global hotkey
        self._hotkey = HotkeyThread()
        self._hotkey.triggered.connect(self._toggle)
        self._hotkey.start()

        # show compact HUD on launch
        self.hud.show()

    # ── state transitions ─────────────────────────────────────────────

    def _show_hud(self):
        self.chat.hide()
        self.hud.show()
        self.hud.raise_()
        self.hud.activateWindow()

    def _show_chat(self):
        self.hud.hide()
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat._input.setFocus()

    def _toggle(self):
        """Ctrl+Shift+J cycle: hidden -> HUD -> chat -> hidden."""
        if self.chat.isVisible():
            self.chat.hide()
            self.hud.hide()
        elif self.hud.isVisible():
            self._show_chat()
        else:
            self._show_hud()

    # ── tray ──────────────────────────────────────────────────────────

    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle()

    def _tray_action(self, fn):
        try:
            result = str(fn())
        except Exception as e:
            result = f"Error: {e}"
        self._tray.showMessage(
            "Jarvis", result,
            QSystemTrayIcon.MessageIcon.Information, 2500,
        )

    @staticmethod
    def _toggle_mute():
        _, muted = _read_volume()
        return unmute_audio() if muted else mute_audio()

    # ── cleanup ───────────────────────────────────────────────────────

    def cleanup(self):
        self._hotkey.stop()
        self._hotkey.wait(2000)


# ══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 50)
    print("  JARVIS HUD OVERLAY")
    print("=" * 50)
    print("  • HUD          : floating on desktop")
    print("  • Chat panel   : click HUD or Ctrl+Shift+J")
    print("  • System tray  : right-click for quick actions")
    print("  • Hotkey       : Ctrl+Shift+J (toggle)")
    print("=" * 50 + "\n")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = JarvisOverlay()
    app.aboutToQuit.connect(overlay.cleanup)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
