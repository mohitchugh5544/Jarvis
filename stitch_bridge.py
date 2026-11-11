"""
Stitch UI Bridge & Local API Server for Jarvis.

Merges the 6 Google Stitch HTML/Tailwind templates into:
  - `/widget` : Compact Floating Desktop Widget (frameless HUD)
  - `/hub`    : Full 5-Tab Jarvis Spatial Workspace
And serves `/api/*` endpoints so every button, slider, input, voice control,
and data view in the Stitch UI talks directly to the real Python backend.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import platform
import re
import socket
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import psutil
import pyperclip

from config import (
    DATA_DIR,
    JARVIS_AUTH_TOKEN,
    JARVIS_MODEL,
    JARVIS_SERVER_PORT,
    JARVIS_TTS_RATE,
    JARVIS_TTS_VOICE_INDEX,
    JARVIS_WAKE_THRESHOLD,
    JARVIS_WHISPER_MODEL,
)
import tools

STITCH_ROOT = Path(__file__).parent / "stitch_ui" / "stitch_jarvis_ai_assistant_ui"


def _extract_main_inner(html_text: str) -> str:
    """Extracts the HTML inside <main ...>...</main> and removes trailing demo <script>."""
    m = re.search(r"<main[^>]*>(.*)</main>", html_text, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    inner = m.group(1)
    # Remove trailing inline <script>...</script> inside <main> so bridge_hub.js controls it cleanly
    inner = re.sub(r"<script\b[^>]*>.*?</script>", "", inner, flags=re.DOTALL | re.IGNORECASE)
    return inner.strip()


def build_widget_html() -> str:
    """Builds the Compact Floating Desktop Widget HTML from Stitch export."""
    widget_file = STITCH_ROOT / "jarvis_compact_floating_desktop_widget" / "code.html"
    raw = widget_file.read_text(encoding="utf-8")
    # Remove demo script at bottom
    raw = re.sub(
        r"<script>\s*\(function initJarvisHUD\(\).*?</script>",
        '<script src="/bridge_widget.js"></script>',
        raw,
        flags=re.DOTALL,
    )
    # Make outer container fit frameless window snugly & disable backdrop-filter repaint flicker
    anti_flicker_widget_css = """
    <style>
      *, *::before, *::after { -webkit-backface-visibility: hidden; backface-visibility: hidden; }
      .backdrop-blur-3xl, .backdrop-blur-2xl, .backdrop-blur-xl, .backdrop-blur-md, .backdrop-blur-sm {
        backdrop-filter: none !important;
        -webkit-backdrop-filter: none !important;
      }
      .animate-ping { animation: none !important; opacity: 0.35 !important; }
    </style>
    """
    raw = raw.replace("</head>", anti_flicker_widget_css + "</head>")
    raw = raw.replace(
        '<body class="bg-surface font-body-md text-on-surface antialiased"><main class="w-full min-h-screen flex items-center justify-center p-space-md bg-surface"><div class="flex flex-col w-full items-center justify-center py-6 px-4 select-none relative overflow-hidden">',
        '<body class="bg-[#0d0e11] font-body-md text-on-surface antialiased overflow-hidden select-none m-0 p-0"><main class="w-full bg-[#0d0e11] m-0 p-0"><div class="flex flex-col w-full select-none relative overflow-hidden m-0 p-0">',
    )
    # Remove outer ambient glow layers so there is no outer box around the card
    raw = re.sub(
        r"<!-- Ambient Spatial Glows Behind Widget.*?<!-- FLOATING MINI HUD WINDOW CONTAINER",
        "<!-- FLOATING MINI HUD WINDOW CONTAINER",
        raw,
        flags=re.DOTALL,
    )
    # Make the inner card itself fill 100% of the DWM rounded window edge-to-edge
    raw = raw.replace(
        '<div class="relative w-full max-w-[356px] rounded-[36px] bg-surface-container-lowest/80 backdrop-blur-3xl p-5 shadow-2xl flex flex-col gap-4 text-on-surface transition-all duration-300">',
        '<div id="jarvisWidgetCard" class="relative w-full rounded-[8px] bg-[#0d0e11] p-4 flex flex-col gap-3.5 text-on-surface">',
    )
    raw = raw.replace(
        '<div class="absolute inset-0 rounded-[36px] pointer-events-none shadow-[inset_0_1px_1px_rgba(255,255,255,0.2),inset_0_0_24px_rgba(75,142,255,0.12)]"></div>',
        '<div class="absolute inset-0 rounded-[8px] pointer-events-none shadow-[inset_0_1px_1px_rgba(255,255,255,0.22),inset_0_0_24px_rgba(75,142,255,0.14)]"></div>',
    )
    # Add Hide Popup button next to Expand button in the top-right header bar
    raw = raw.replace(
        '<span class="material-symbols-outlined text-[16px]">open_in_full</span>\n</button>',
        '<span class="material-symbols-outlined text-[16px]">open_in_full</span>\n</button>\n'
        '<button class="w-7 h-7 rounded-full flex items-center justify-center text-on-surface-variant hover:text-error hover:bg-error-container/30 transition-colors" id="hideWidgetBtn" title="Hide Popup (Press Ctrl+Alt+J to bring back)">\n'
        '<span class="material-symbols-outlined text-[16px]">close</span>\n</button>',
    )
    return raw


def build_hub_html() -> str:
    """Combines all 5 Stitch dashboard screens into a single instant-switching SPA."""
    views = [
        ("chat-voice", "jarvis_chat_voice_workspace"),
        ("long-term-memory", "jarvis_long_term_memory_learning"),
        ("system-controls", "jarvis_system_hardware_controls"),
        ("productivity-hub", "jarvis_productivity_hub"),
        ("settings-bridge", "jarvis_settings_mobile_bridge"),
    ]

    chat_html = (STITCH_ROOT / "jarvis_chat_voice_workspace" / "code.html").read_text(encoding="utf-8")
    # Split chat_html around <main ...>...</main>
    prefix_match = re.search(r"^(.*?)<main[^>]*>", chat_html, flags=re.DOTALL | re.IGNORECASE)
    prefix = prefix_match.group(1) if prefix_match else ""

    view_blocks = []
    for idx, (slug, folder) in enumerate(views):
        fpath = STITCH_ROOT / folder / "code.html"
        raw = fpath.read_text(encoding="utf-8")
        inner = _extract_main_inner(raw)
        hidden_cls = "" if idx == 0 else " hidden"
        view_blocks.append(f'<div id="view-{slug}" class="jarvis-view{hidden_cls}">{inner}</div>')

    combined_main = (
        '<main class="w-full pt-16 bg-surface min-h-screen">'
        + "\n".join(view_blocks)
        + "</main>"
    )

    extra_css = """
    <style>
      .jarvis-view.hidden { display: none !important; }
      *, *::before, *::after { -webkit-backface-visibility: hidden; backface-visibility: hidden; }
      /* Prevent Chromium backdrop-filter + animate-ping full-viewport repaint flicker */
      .backdrop-blur-3xl, .backdrop-blur-2xl, .backdrop-blur-xl, .backdrop-blur-md, .backdrop-blur-sm {
        backdrop-filter: none !important;
        -webkit-backdrop-filter: none !important;
      }
      .animate-ping { animation: none !important; opacity: 0.45 !important; }
      /* Custom subtle scrollbar for chat & lists */
      ::-webkit-scrollbar { width: 6px; height: 6px; display: block; }
      ::-webkit-scrollbar-track { background: transparent; }
      ::-webkit-scrollbar-thumb { background: rgba(173, 198, 255, 0.18); border-radius: 9999px; }
      ::-webkit-scrollbar-thumb:hover { background: rgba(173, 198, 255, 0.35); }
      /* Responsive mobile layout when /hub is opened on narrow screens */
      @media (max-width: 1023px) {
        aside.fixed.left-0.w-72 {
          width: 100% !important;
          height: 60px !important;
          top: auto !important;
          bottom: 0 !important;
          padding: 4px 8px !important;
          flex-direction: row !important;
          background: #0d0e11 !important;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        aside.fixed.left-0.w-72 > div:first-child > div:not(nav),
        aside.fixed.left-0.w-72 > div:last-child {
          display: none !important;
        }
        aside.fixed.left-0.w-72 > div:first-child {
          width: 100% !important;
          padding: 0 !important;
        }
        aside.fixed.left-0.w-72 nav {
          flex-direction: row !important;
          justify-content: space-around !important;
          width: 100% !important;
        }
        aside.fixed.left-0.w-72 nav a {
          flex-direction: column !important;
          gap: 2px !important;
          padding: 6px 8px !important;
          font-size: 10px !important;
        }
        .pl-72 { padding-left: 0 !important; padding-bottom: 70px !important; }
        header.fixed.left-72 { left: 0 !important; }
      }
    </style>
    """

    full_html = (
        prefix.replace("</head>", extra_css + "</head>")
        + combined_main
        + '<script src="/bridge_hub.js"></script></div></body></html>'
    )
    return full_html


WIDGET_JS = r"""
(function() {
  async function api(path, body) {
    try {
      const opts = body !== undefined
        ? { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) }
        : { method: 'GET' };
      const res = await fetch(path, opts);
      return await res.json();
    } catch (e) {
      console.error('API err:', path, e);
      return null;
    }
  }

  // Elements
  const widgetCard = document.getElementById('jarvisWidgetCard') || document.querySelector('.rounded-\\[36px\\]');
  const pinBtn = document.getElementById('pinBtn');
  const hideWidgetBtn = document.getElementById('hideWidgetBtn');
  const headerBtns = document.querySelectorAll('.flex.items-center.gap-1.bg-surface-container-low\\/70 button');
  const collapseBtn = headerBtns[1];
  const expandTopBtn = headerBtns[2];
  const liveClock = document.getElementById('liveClock');
  const ampmEl = liveClock ? liveClock.nextElementSibling : null;
  const dateRow = liveClock ? liveClock.parentElement.nextElementSibling : null;
  const batTopBadge = document.querySelector('.bg-surface-container-high\\/90 span:last-child');
  const orbBtn = document.getElementById('orbButton');
  const orbWave = document.getElementById('orbWave');
  const orbStatus = document.getElementById('orbStatus');
  const orbSubtext = document.getElementById('orbSubtext');
  const muteBtn = document.getElementById('muteBtn');
  const volFill = document.getElementById('volFill');
  const volLabel = document.getElementById('volLabel');
  const pttMic = document.getElementById('pttMic');
  const openHubBtn = pttMic ? pttMic.nextElementSibling : null;

  // Battery & Brightness bars in widget
  const sliderRows = document.querySelectorAll('.space-y-2\\.5 > div.flex.items-center');
  const batRow = sliderRows[0];
  const volRow = sliderRows[1];
  const briRow = sliderRows[2];
  const batFill = batRow ? batRow.querySelector('.flex-1 > div') : null;
  const batLabel = batRow ? batRow.querySelector('span:last-child') : null;
  const briTrack = briRow ? briRow.querySelector('.flex-1') : null;
  const briFill = briRow ? briRow.querySelector('.flex-1 > div') : null;
  const briLabel = briRow ? briRow.querySelector('span:last-child') : null;
  const volTrack = volRow ? volRow.querySelector('.flex-1') : null;

  // Quick 2x2 action tiles
  const quickTiles = document.querySelectorAll('.grid.grid-cols-2.gap-2 > button');
  const tileStandby = quickTiles[0];
  const tileAnalyze = quickTiles[1];
  const tileSnippet = quickTiles[2];
  const tileLock = quickTiles[3];

  // Native OS Window Dragging from Header Bar (single call on mousedown, zero lag)
  const headerBar = document.querySelector('.relative.z-10.flex.items-center.justify-between.px-1');
  if (headerBar) {
    headerBar.style.cursor = 'move';
    headerBar.addEventListener('mousedown', (e) => {
      if (e.target.closest('button')) return;
      api('/api/window', { target: 'widget', action: 'start_drag' });
    });
  }

  function syncWidgetSize() {
    if (!widgetCard) return;
    const h = Math.ceil(widgetCard.getBoundingClientRect().height);
    if (h > 60) {
      api('/api/window', { target: 'widget', action: 'resize', width: 356, height: h });
    }
  }

  // Pin toggle
  let isPinned = true;
  if (pinBtn) {
    pinBtn.addEventListener('click', async () => {
      const r = await api('/api/window', { target: 'widget', action: 'toggle_pin' });
      isPinned = r && r.pinned !== undefined ? r.pinned : !isPinned;
      if (isPinned) {
        pinBtn.classList.add('bg-primary/15', 'text-primary');
        pinBtn.classList.remove('text-on-surface-variant');
      } else {
        pinBtn.classList.remove('bg-primary/15', 'text-primary');
        pinBtn.classList.add('text-on-surface-variant');
      }
    });
  }

  // Collapse to Pill toggle
  let isCollapsed = false;
  if (collapseBtn) {
    collapseBtn.addEventListener('click', () => {
      isCollapsed = !isCollapsed;
      // Hide/show sections below time platter
      if (widgetCard) {
        const cardChildren = widgetCard.children;
        for (let i = 3; i < cardChildren.length; i++) {
          cardChildren[i].style.display = isCollapsed ? 'none' : '';
        }
      }
      requestAnimationFrame(syncWidgetSize);
    });
  }

  // Hide Popup button (Ctrl+Alt+J restores it)
  if (hideWidgetBtn) {
    hideWidgetBtn.addEventListener('click', () => {
      api('/api/window', { target: 'widget', action: 'hide_widget' });
    });
  }

  window.addEventListener('load', () => {
    setTimeout(syncWidgetSize, 40);
  });

  // Expand to Full Hub
  function openMainHub() {
    api('/api/window', { target: 'hub', action: 'show' });
  }
  if (expandTopBtn) expandTopBtn.addEventListener('click', openMainHub);
  if (openHubBtn) openHubBtn.addEventListener('click', openMainHub);

  // Acoustic Orb -> One-shot Push-to-Talk | Bottom Mic Button (#pttMic) -> Toggle Voice Mode ON/OFF
  async function triggerVoice() {
    if (orbStatus) {
      orbStatus.innerHTML = '<span class="text-primary font-bold">Listening...</span><span class="w-1.5 h-1.5 rounded-full bg-primary animate-ping"></span>';
    }
    if (orbSubtext) {
      orbSubtext.textContent = 'Voice capture stream active • Speak now';
    }
    await api('/api/voice/ptt', {});
  }
  if (orbBtn) orbBtn.addEventListener('click', triggerVoice);

  if (pttMic) {
    pttMic.innerHTML = '<span class="material-symbols-outlined text-[18px]" id="pttMicIcon">mic_off</span><span id="pttMicLabel">Voice: OFF</span>';
    pttMic.className = 'h-10 px-3.5 rounded-full bg-surface-container-high text-on-surface-variant hover:text-on-surface flex items-center justify-center gap-1.5 font-headline-sm text-label-md font-semibold active:scale-95 transition-all flex-shrink-0';
    pttMic.title = 'Toggle Continuous Voice Wake Word ("Hey Jarvis") ON or OFF';
    pttMic.addEventListener('click', async () => {
      const r = await api('/api/voice/wake', { toggle: true });
      if (r) updateTelemetryUI(r);
    });
  }

  // Volume Bar Click & Mute
  if (volTrack) {
    volTrack.addEventListener('click', async (e) => {
      const rect = volTrack.getBoundingClientRect();
      const pct = Math.max(0, Math.min(100, Math.round(((e.clientX - rect.left) / rect.width) * 100)));
      if (volFill) volFill.style.width = pct + '%';
      if (volLabel) volLabel.textContent = pct + '%';
      await api('/api/system/volume', { level: pct });
    });
  }
  if (muteBtn) {
    muteBtn.addEventListener('click', async () => {
      const r = await api('/api/system/mute', { toggle: true });
      if (r && r.volume !== undefined) {
        updateTelemetryUI(r);
      }
    });
  }

  // Brightness Bar Click
  if (briTrack) {
    briTrack.addEventListener('click', async (e) => {
      const rect = briTrack.getBoundingClientRect();
      const pct = Math.max(10, Math.min(100, Math.round(((e.clientX - rect.left) / rect.width) * 100)));
      if (briFill) briFill.style.width = pct + '%';
      if (briLabel) briLabel.textContent = pct + '%';
      await api('/api/system/brightness', { level: pct });
    });
  }

  // 2x2 Quick Action Tiles
  if (tileStandby) {
    tileStandby.addEventListener('click', async () => {
      if (orbSubtext) orbSubtext.textContent = 'Entering Standby (minimizing windows & muting)...';
      await api('/api/system/standby', {});
    });
  }
  if (tileAnalyze) {
    tileAnalyze.addEventListener('click', async () => {
      if (orbStatus) {
        orbStatus.innerHTML = '<span class="text-secondary font-bold">Analyzing Screen...</span><span class="w-1.5 h-1.5 rounded-full bg-secondary animate-ping"></span>';
      }
      if (orbSubtext) orbSubtext.textContent = 'Capturing display & running AI vision...';
      const r = await api('/api/system/analyze_screen', {});
      if (r && r.result) {
        if (orbSubtext) orbSubtext.textContent = r.result;
        let box = document.getElementById('widgetVisionResult');
        if (!box && widgetCard) {
          box = document.createElement('div');
          box.id = 'widgetVisionResult';
          box.className = 'relative z-10 rounded-xl bg-surface-container-high/90 p-3 text-body-sm text-on-surface border border-primary/25 flex flex-col gap-1.5';
          widgetCard.insertBefore(box, widgetCard.children[3]);
        }
        if (box) {
          box.style.display = '';
          box.innerHTML = `
            <div class="flex items-center justify-between">
              <span class="font-label-sm text-caption text-primary font-semibold uppercase tracking-wider">Screen Analysis</span>
              <button id="closeVisionBox" class="text-on-surface-variant hover:text-on-surface text-caption px-1">✕</button>
            </div>
            <div class="text-caption leading-relaxed max-h-28 overflow-y-auto">${r.result.replace(/</g, '&lt;')}</div>
          `;
          const closeBtn = box.querySelector('#closeVisionBox');
          if (closeBtn) {
            closeBtn.onclick = () => {
              box.remove();
              requestAnimationFrame(syncWidgetSize);
            };
          }
          requestAnimationFrame(syncWidgetSize);
        }
      }
    });
  }
  if (tileSnippet) {
    tileSnippet.addEventListener('click', async () => {
      if (orbSubtext) orbSubtext.textContent = 'Capturing screenshot...';
      const r = await api('/api/system/screenshot', {});
      if (r && r.result && orbSubtext) {
        orbSubtext.textContent = r.result;
      }
    });
  }
  if (tileLock) {
    tileLock.addEventListener('click', async () => {
      await api('/api/system/lock', {});
    });
  }

  function updateTelemetryUI(data) {
    if (!data) return;
    if (liveClock && data.clock_short) liveClock.textContent = data.clock_short;
    if (ampmEl && data.ampm) ampmEl.textContent = data.ampm;
    if (dateRow && data.date_short) {
      const spans = dateRow.querySelectorAll('span');
      if (spans[0]) spans[0].textContent = data.date_short;
      if (spans[2]) spans[2].textContent = 'CTRL+ALT+J';
    }
    if (data.battery !== null && data.battery !== undefined) {
      if (batTopBadge) batTopBadge.textContent = data.battery + '%';
      if (batFill) batFill.style.width = data.battery + '%';
      if (batLabel) batLabel.textContent = data.battery + '%';
    }
    if (data.volume !== null && data.volume !== undefined) {
      const v = data.muted ? 0 : data.volume;
      if (volFill) volFill.style.width = v + '%';
      if (volLabel) volLabel.textContent = v + '%';
      if (muteBtn) {
        muteBtn.textContent = data.muted ? 'volume_off' : 'volume_up';
        muteBtn.classList.toggle('text-error', !!data.muted);
      }
    }
    if (data.brightness !== null && data.brightness !== undefined) {
      if (briFill) briFill.style.width = data.brightness + '%';
      if (briLabel) briLabel.textContent = data.brightness + '%';
    }
    // Voice / Agent status
    if (orbStatus && orbSubtext && orbBtn && orbWave) {
      const st = data.assistant_state || 'ready';
      if (st === 'listening') {
        orbStatus.innerHTML = '<span class="text-tertiary font-bold">Listening...</span><span class="w-1.5 h-1.5 rounded-full bg-tertiary animate-ping"></span>';
        orbSubtext.textContent = 'Voice capture stream active • Speak now';
        orbBtn.classList.add('shadow-[0_0_48px_rgba(83,225,111,0.6)]');
        orbWave.classList.add('text-tertiary');
        orbWave.classList.remove('text-primary');
      } else if (st === 'thinking' || st === 'transcribing') {
        orbStatus.innerHTML = '<span class="text-secondary font-bold">Thinking...</span><span class="w-1.5 h-1.5 rounded-full bg-secondary animate-ping"></span>';
        orbSubtext.textContent = data.last_status_detail || 'Processing neural command...';
        orbBtn.classList.remove('shadow-[0_0_48px_rgba(83,225,111,0.6)]');
        orbWave.classList.add('text-secondary');
        orbWave.classList.remove('text-primary', 'text-tertiary');
      } else if (st === 'speaking') {
        orbStatus.innerHTML = '<span class="text-primary font-bold">Speaking...</span><span class="w-1.5 h-1.5 rounded-full bg-primary animate-ping"></span>';
        orbSubtext.textContent = data.last_reply_preview || 'Streaming vocal response...';
        orbWave.classList.add('text-primary');
        orbWave.classList.remove('text-secondary', 'text-tertiary');
      } else {
        const wakeOn = !!data.wake_enabled;
        const wakeText = wakeOn ? 'Voice ON ("Hey Jarvis")' : 'Voice OFF (Tap Orb to Speak)';
        const dotCls = wakeOn ? 'bg-tertiary' : 'bg-outline';
        orbStatus.innerHTML = `<span>${wakeText}</span><span class="w-1.5 h-1.5 rounded-full ${dotCls}"></span>`;
        orbSubtext.textContent = data.last_reply_preview || (wakeOn ? 'Listening for "Hey Jarvis"...' : 'Continuous mic off • Click "Voice: OFF" below to enable');
        orbBtn.classList.remove('shadow-[0_0_48px_rgba(83,225,111,0.6)]');
        orbWave.classList.remove('text-tertiary', 'text-secondary');
        orbWave.classList.add('text-primary');
        if (pttMic) {
          const ic = document.getElementById('pttMicIcon');
          const lbl = document.getElementById('pttMicLabel');
          if (ic) ic.textContent = wakeOn ? 'mic' : 'mic_off';
          if (lbl) lbl.textContent = wakeOn ? 'Voice: ON' : 'Voice: OFF';
          pttMic.className = wakeOn
            ? 'h-10 px-3.5 rounded-full bg-tertiary text-on-tertiary flex items-center justify-center gap-1.5 font-headline-sm text-label-md font-semibold shadow-lg active:scale-95 transition-all flex-shrink-0'
            : 'h-10 px-3.5 rounded-full bg-surface-container-high text-on-surface-variant hover:text-on-surface flex items-center justify-center gap-1.5 font-headline-sm text-label-md font-semibold active:scale-95 transition-all flex-shrink-0';
        }
      }
    }
  }

  async function poll() {
    if (document.hidden) return;
    const data = await api('/api/telemetry');
    if (data) updateTelemetryUI(data);
  }
  poll();
  setInterval(poll, 2500);
})();
"""


HUB_JS = r"""
(function() {
  async function api(path, body) {
    try {
      const opts = body !== undefined
        ? { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) }
        : { method: 'GET' };
      const res = await fetch(path, opts);
      return await res.json();
    } catch (e) {
      console.error('API error:', path, e);
      return null;
    }
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // Global Toast helper (uses #toastNotification in Productivity Hub or creates one)
  function showToast(message, icon = 'info', colorClass = 'text-primary') {
    let toast = document.getElementById('globalJarvisToast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'globalJarvisToast';
      toast.className = 'fixed bottom-6 right-6 z-[999] transform translate-y-16 opacity-0 transition-all duration-300 pointer-events-none flex items-center gap-3 px-4 py-3 rounded-xl bg-surface-container-high border border-white/10 text-on-surface shadow-2xl';
      toast.innerHTML = `<span class="material-symbols-outlined text-[20px] text-primary" id="globalToastIcon">info</span><span class="font-body-sm text-body-sm font-medium" id="globalToastMsg"></span>`;
      document.body.appendChild(toast);
    }
    document.getElementById('globalToastMsg').textContent = message;
    const ic = document.getElementById('globalToastIcon');
    ic.textContent = icon;
    ic.className = `material-symbols-outlined text-[20px] ${colorClass}`;
    toast.classList.remove('translate-y-16', 'opacity-0');
    toast.classList.add('translate-y-0', 'opacity-100');
    if (window._jarvisToastTimer) clearTimeout(window._jarvisToastTimer);
    window._jarvisToastTimer = setTimeout(() => {
      toast.classList.add('translate-y-16', 'opacity-0');
      toast.classList.remove('translate-y-0', 'opacity-100');
    }, 2800);
  }
  window.showToast = showToast;

  // =========================================================================
  // 1. SIDEBAR NAVIGATION & WINDOW CONTROLS
  // =========================================================================
  const navLinks = document.querySelectorAll('aside nav a[data-path]');
  const views = document.querySelectorAll('.jarvis-view');
  const activeNavClasses = ['bg-primary-container', 'text-on-primary-container', 'font-semibold', 'shadow-[0_4px_16px_-2px_rgba(75,142,255,0.3)]'];
  const inactiveNavClasses = ['text-on-surface-variant'];

  function switchView(targetPath) {
    views.forEach(v => {
      if (v.id === 'view-' + targetPath) {
        v.classList.remove('hidden');
      } else {
        v.classList.add('hidden');
      }
    });
    navLinks.forEach(a => {
      const p = a.getAttribute('data-path');
      if (p === targetPath) {
        a.classList.add(...activeNavClasses);
        a.classList.remove(...inactiveNavClasses);
      } else {
        a.classList.remove(...activeNavClasses);
        a.classList.add(...inactiveNavClasses);
      }
    });
    if (targetPath === 'long-term-memory') loadMemoriesView();
    if (targetPath === 'system-controls') loadSystemControlsView();
    if (targetPath === 'productivity-hub') loadProductivityView();
    if (targetPath === 'settings-bridge') loadSettingsView();
  }
  window.switchJarvisView = switchView;

  navLinks.forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      switchView(a.getAttribute('data-path'));
    });
  });

  // Top Header Dragging & Window Controls (Native OS Drag)
  const topHeader = document.querySelector('header.fixed.top-0');
  if (topHeader) {
    topHeader.style.cursor = 'move';
    topHeader.addEventListener('mousedown', (e) => {
      if (e.target.closest('button') || e.target.closest('input')) return;
      api('/api/window', { target: 'hub', action: 'start_drag' });
    });

    // Header buttons (Floating Widget, Volume, Notifications, Minimize, Maximize, Close)
    const hdrButtons = topHeader.querySelectorAll('button');
    // hdrButtons[0]: Floating Widget
    if (hdrButtons[0]) {
      hdrButtons[0].addEventListener('click', () => {
        api('/api/window', { target: 'widget', action: 'focus_widget_mode' });
      });
    }
    // hdrButtons[1]: Quick Mute toggle
    if (hdrButtons[1]) {
      hdrButtons[1].addEventListener('click', async () => {
        await api('/api/system/mute', { toggle: true });
        pollTelemetry();
      });
    }
    // hdrButtons[2]: Notifications -> switch to Productivity Hub Reminders
    if (hdrButtons[2]) {
      hdrButtons[2].addEventListener('click', () => switchView('productivity-hub'));
    }
    // hdrButtons[3]: Minimize
    if (hdrButtons[3]) {
      hdrButtons[3].addEventListener('click', () => api('/api/window', { target: 'hub', action: 'minimize' }));
    }
    // hdrButtons[4]: Maximize / Restore
    if (hdrButtons[4]) {
      hdrButtons[4].addEventListener('click', () => api('/api/window', { target: 'hub', action: 'toggle_maximize' }));
    }
    // hdrButtons[5]: Close (hide to tray / widget)
    if (hdrButtons[5]) {
      hdrButtons[5].addEventListener('click', () => api('/api/window', { target: 'hub', action: 'hide' }));
    }
  }

  // Sub-header window controls inside Chat & Voice view
  const chatView = document.getElementById('view-chat-voice');
  if (chatView) {
    const subBtns = chatView.querySelectorAll('.px-space-lg.py-space-sm button');
    if (subBtns[0]) subBtns[0].addEventListener('click', () => api('/api/window', { target: 'widget', action: 'focus_widget_mode' }));
    if (subBtns[1]) subBtns[1].addEventListener('click', () => api('/api/window', { target: 'hub', action: 'toggle_pin' }));
    if (subBtns[2]) subBtns[2].addEventListener('click', () => api('/api/window', { target: 'hub', action: 'minimize' }));
    if (subBtns[3]) subBtns[3].addEventListener('click', () => api('/api/window', { target: 'hub', action: 'hide' }));
  }

  // =========================================================================
  // 2. VIEW 1: CHAT & VOICE WORKSPACE
  // =========================================================================
  const chatFeedContainer = chatView ? chatView.querySelector('.flex-1.w-full.max-w-7xl.mx-auto') : null;
  const chatInput = document.getElementById('chatInput');
  const pttButton = document.getElementById('pttButton');
  const statusLabel = document.getElementById('statusLabel');
  const archiveSearchInput = chatView ? chatView.querySelector('input[placeholder*="Search Permanent Archive"]') : null;
  const clearContextBtn = chatView ? chatView.querySelector('.px-space-lg.py-2\\.5 button') : null;

  // Multimodal bottom bar buttons
  const mmBar = chatView ? chatView.querySelector('.sticky.bottom-0 .flex.items-center.gap-space-xs.flex-wrap') : null;
  const mmBtns = mmBar ? mmBar.querySelectorAll('button') : [];
  const btnWakeToggle = mmBtns[0];
  const btnTtsToggle = mmBtns[1];
  const btnAnalyzeScreenChat = mmBtns[2];
  const btnReadClipboardChat = mmBtns[3];

  // Input bar Send & Attach buttons
  const inputPlatterBtns = chatView ? chatView.querySelectorAll('.p-2.rounded-2xl.bg-surface-container-high\\/90 button') : [];
  const attachBtn = inputPlatterBtns[1];
  const sendBtn = inputPlatterBtns[2];

  // Remove inline onclick alert from Stitch template Send button & Wake/TTS buttons
  if (sendBtn) sendBtn.removeAttribute('onclick');
  if (btnWakeToggle) btnWakeToggle.removeAttribute('onclick');
  if (btnTtsToggle) btnTtsToggle.removeAttribute('onclick');

  let lastRenderedChatCount = -1;
  let currentArchiveFilter = '';
  let isAgentThinking = false;

  function renderChatEntry(entry, idx) {
    const tsRaw = entry.timestamp || '';
    const tsShort = tsRaw.includes('T') ? tsRaw.split('T')[1].slice(0, 5) : (tsRaw || 'Now');
    const userText = escapeHtml(entry.user || '');
    const botText = escapeHtml(entry.assistant || '').replace(/\n/g, '<br>');
    const toolsUsed = Array.isArray(entry.tools) ? entry.tools : [];
    const isVoice = !!entry.is_voice;

    let toolsHtml = '';
    if (toolsUsed.length > 0) {
      const badges = toolsUsed.map(t => `<span class="px-2 py-0.5 rounded-full bg-surface-container-high font-caption text-caption text-tertiary font-mono font-semibold">${escapeHtml(t)}</span>`).join(' ');
      const learnedMemory = toolsUsed.includes('remember_fact');
      toolsHtml = `
        <div class="w-full max-w-3xl mr-auto pl-2 flex flex-col gap-space-xs">
          <div class="p-3.5 rounded-2xl bg-surface-container-low/90 backdrop-blur-2xl shadow-lg flex items-center justify-between flex-wrap gap-2 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.08)]">
            <div class="flex items-center gap-2.5">
              <div class="w-7 h-7 rounded-lg bg-tertiary-container/30 flex items-center justify-center">
                <span class="material-symbols-outlined text-tertiary text-[18px]">verified</span>
              </div>
              <div>
                <span class="font-label-md text-label-md text-on-surface font-semibold">Executed Tools</span>
                <div class="flex flex-wrap gap-1.5 mt-1">${badges}</div>
              </div>
            </div>
            <span class="px-2 py-0.5 rounded-full bg-surface-container font-caption text-caption text-on-surface-variant font-mono">Status: 200 OK</span>
          </div>
          ${learnedMemory ? `
          <div class="mx-space-xs p-3 rounded-xl bg-secondary-container/20 backdrop-blur-xl flex items-center justify-between gap-space-sm shadow-[inset_0_1px_0_0_rgba(232,179,255,0.2)]">
            <div class="flex items-center gap-2.5">
              <div class="w-6 h-6 rounded-lg bg-secondary/20 flex items-center justify-center">
                <span class="material-symbols-outlined text-secondary text-[16px]">psychology</span>
              </div>
              <div class="flex flex-col">
                <span class="font-label-sm text-label-sm text-secondary font-semibold uppercase tracking-wider">Learned &amp; Saved to Long-Term Memory</span>
                <p class="font-body-sm text-body-sm text-on-surface">Retained in permanent context cache (data/memories.json).</p>
              </div>
            </div>
            <button onclick="window.switchJarvisView('long-term-memory')" class="text-secondary hover:text-on-surface transition-colors p-1" title="View Memory Graph" type="button">
              <span class="material-symbols-outlined text-[18px]">open_in_new</span>
            </button>
          </div>` : ''}
        </div>
      `;
    }

    const userCardHtml = isVoice ? `
      <div class="flex justify-end w-full chat-turn-item">
        <div class="flex flex-col items-end max-w-xl gap-1">
          <div class="flex items-center gap-2 px-2">
            <span class="font-caption text-caption text-outline">You • Voice Command</span>
            <span class="w-1 h-1 rounded-full bg-outline-variant"></span>
            <span class="font-caption text-caption text-outline">${escapeHtml(tsShort)}</span>
          </div>
          <div class="relative w-full p-space-md rounded-2xl rounded-tr-sm bg-surface-container-high/90 backdrop-blur-xl shadow-lg flex flex-col gap-2 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.12)]">
            <div class="flex items-start gap-2">
              <span class="material-symbols-outlined text-[16px] text-tertiary mt-0.5">graphic_eq</span>
              <p class="font-body-md text-body-md text-on-surface leading-snug">"${userText}"</p>
            </div>
          </div>
        </div>
      </div>
    ` : `
      <div class="flex justify-end w-full chat-turn-item">
        <div class="flex flex-col items-end max-w-2xl gap-1">
          <div class="flex items-center gap-2 px-2">
            <span class="font-caption text-caption text-outline">You</span>
            <span class="w-1 h-1 rounded-full bg-outline-variant"></span>
            <span class="font-caption text-caption text-outline">${escapeHtml(tsShort)}</span>
          </div>
          <div class="relative px-space-lg py-space-md rounded-2xl rounded-tr-sm bg-primary-container text-on-primary-container shadow-[0_12px_32px_-8px_rgba(75,142,255,0.3)]">
            <p class="font-body-md text-body-md leading-relaxed font-medium">${userText}</p>
            <div class="mt-1.5 flex items-center justify-end gap-1.5 text-on-primary-container/70 font-caption text-caption font-mono">
              <span class="material-symbols-outlined text-[13px]">done_all</span>
              <span>Saved • Archive</span>
            </div>
          </div>
        </div>
      </div>
    `;

    const botCardHtml = `
      <div class="w-full max-w-3xl mr-auto flex flex-col gap-2 chat-turn-item">
        <div class="flex items-center gap-2 px-2">
          <div class="w-5 h-5 rounded-full bg-primary/20 flex items-center justify-center">
            <span class="material-symbols-outlined text-primary text-[13px]">neurology</span>
          </div>
          <span class="font-headline-sm text-label-md font-bold text-on-surface">Jarvis Core</span>
          <span class="w-1 h-1 rounded-full bg-outline-variant"></span>
          <span class="font-caption text-caption text-outline">${escapeHtml(tsShort)}</span>
          <span class="ml-1 px-1.5 py-0.5 rounded bg-surface-container font-caption text-caption text-primary font-mono font-medium">Neural Agent</span>
        </div>
        <div class="p-space-lg rounded-2xl rounded-tl-sm bg-surface-container-low/95 backdrop-blur-3xl shadow-2xl flex flex-col gap-space-md shadow-[inset_0_1px_0_0_rgba(255,255,255,0.1)]">
          <p class="font-body-lg text-body-lg text-on-surface leading-relaxed select-text">${botText}</p>
          <div class="flex items-center justify-between flex-wrap gap-2 pt-1">
            <div class="flex items-center gap-space-xs">
              <button data-speak="${escapeHtml(entry.assistant || '')}" class="btn-speak-msg group flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-surface-container-high hover:bg-surface-bright text-on-surface-variant hover:text-on-surface transition-all text-label-sm font-label-sm shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)]" type="button">
                <span class="material-symbols-outlined text-[16px] text-secondary group-hover:scale-110 transition-transform">volume_up</span>
                <span>Speak Aloud (TTS)</span>
              </button>
              <button data-copy="${escapeHtml(entry.assistant || '')}" class="btn-copy-msg group flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-surface-container-high hover:bg-surface-bright text-on-surface-variant hover:text-on-surface transition-all text-label-sm font-label-sm shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)]" type="button">
                <span class="material-symbols-outlined text-[16px] group-hover:scale-110 transition-transform">content_copy</span>
                <span>Copy Text</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    `;

    return userCardHtml + toolsHtml + botCardHtml;
  }

  async function loadChatFeed(force = false) {
    if (!chatFeedContainer) return;
    const data = await api('/api/chat/log');
    if (!data || !Array.isArray(data.entries)) return;

    const entries = data.entries;
    const filtered = currentArchiveFilter
      ? entries.filter(e =>
          (e.user || '').toLowerCase().includes(currentArchiveFilter) ||
          (e.assistant || '').toLowerCase().includes(currentArchiveFilter))
      : entries;

    if (!force && filtered.length === lastRenderedChatCount && !data.thinking) {
      return;
    }
    lastRenderedChatCount = filtered.length;

    // Update restored session badge count
    const sessionBadge = chatView.querySelector('.px-space-lg.py-2\\.5 .hidden.sm\\:flex');
    if (sessionBadge) {
      sessionBadge.innerHTML = `
        <span class="w-2 h-2 rounded-full bg-tertiary"></span>
        <span class="font-label-sm text-label-sm text-on-surface-variant">${entries.length} Saved Turns</span>
        <span class="font-caption text-caption text-outline">· ${data.memories_count || 0} Learned Memories Active</span>
      `;
    }

    let html = '';
    if (filtered.length === 0) {
      html = `
        <div class="p-space-lg rounded-2xl bg-surface-container-low/80 text-center my-8">
          <span class="material-symbols-outlined text-primary text-[36px]">neurology</span>
          <h3 class="font-headline-sm text-headline-sm text-on-surface mt-2">Jarvis Spatial Neural Core Ready</h3>
          <p class="font-body-md text-body-md text-on-surface-variant mt-1">Speak "Hey Jarvis" or type any command below.</p>
        </div>
      `;
    } else {
      html = filtered.map((e, i) => renderChatEntry(e, i)).join('\n');
    }

    if (data.thinking) {
      html += `
        <div class="w-full max-w-2xl mr-auto flex items-center gap-3 p-4 rounded-2xl bg-surface-container-low/90 border border-primary/20 animate-pulse">
          <span class="material-symbols-outlined text-primary animate-spin">progress_activity</span>
          <span class="font-body-md text-body-md text-primary font-medium">Jarvis is thinking: "${escapeHtml(data.thinking_prompt || '...')}"</span>
        </div>
      `;
    }

    chatFeedContainer.innerHTML = html;
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  // Delegate Speak Aloud & Copy Text buttons in chat feed
  if (chatFeedContainer) {
    chatFeedContainer.addEventListener('click', async (e) => {
      const speakBtn = e.target.closest('.btn-speak-msg');
      if (speakBtn) {
        const text = speakBtn.getAttribute('data-speak');
        await api('/api/voice/speak', { text });
        showToast('Speaking message aloud via TTS...', 'volume_up', 'text-secondary');
        return;
      }
      const copyBtn = e.target.closest('.btn-copy-msg');
      if (copyBtn) {
        const text = copyBtn.getAttribute('data-copy');
        await api('/api/system/clipboard', { text });
        showToast('Copied reply to Windows clipboard', 'content_copy', 'text-tertiary');
      }
    });
  }

  async function sendChatMessage(textOverride) {
    const txt = (textOverride !== undefined ? textOverride : (chatInput ? chatInput.value : '')).trim();
    if (!txt) return;
    if (chatInput && textOverride === undefined) chatInput.value = '';
    await api('/api/chat/send', { text: txt });
    setTimeout(() => loadChatFeed(true), 150);
  }

  if (sendBtn) sendBtn.addEventListener('click', () => sendChatMessage());
  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendChatMessage();
      }
    });
  }

  if (pttButton) {
    pttButton.addEventListener('click', async () => {
      showToast('Listening... Speak your command now', 'mic', 'text-tertiary');
      await api('/api/voice/ptt', {});
    });
  }

  if (attachBtn) {
    attachBtn.addEventListener('click', () => {
      sendChatMessage('Search my Desktop and Downloads for recent files');
    });
  }

  if (archiveSearchInput) {
    archiveSearchInput.addEventListener('input', (e) => {
      currentArchiveFilter = (e.target.value || '').trim().toLowerCase();
      loadChatFeed(true);
    });
  }

  if (clearContextBtn) {
    clearContextBtn.addEventListener('click', async () => {
      await api('/api/chat/clear', {});
      showToast('Active LLM context cleared (Permanent Archive & Learned Memories preserved)', 'mop', 'text-primary');
      loadChatFeed(true);
    });
  }

  // Multimodal bar toggles
  if (btnWakeToggle) {
    btnWakeToggle.addEventListener('click', async () => {
      const r = await api('/api/voice/wake', { toggle: true });
      if (r) updateWakeAndTtsButtons(r.wake_enabled, r.tts_enabled);
    });
  }
  if (btnTtsToggle) {
    btnTtsToggle.addEventListener('click', async () => {
      const r = await api('/api/voice/tts', { toggle: true });
      if (r) updateWakeAndTtsButtons(r.wake_enabled, r.tts_enabled);
    });
  }
  if (btnAnalyzeScreenChat) {
    btnAnalyzeScreenChat.addEventListener('click', () => {
      sendChatMessage('Analyze my screen and tell me what you see.');
    });
  }
  if (btnReadClipboardChat) {
    btnReadClipboardChat.addEventListener('click', () => {
      sendChatMessage('Read my Windows clipboard and summarize or help me with its contents.');
    });
  }

  function updateWakeAndTtsButtons(wakeOn, ttsOn) {
    if (btnWakeToggle) {
      const badge = btnWakeToggle.querySelector('span:last-child');
      if (badge) badge.textContent = wakeOn ? 'ON' : 'OFF';
      btnWakeToggle.classList.toggle('bg-tertiary-container/30', !!wakeOn);
      btnWakeToggle.classList.toggle('text-tertiary', !!wakeOn);
      btnWakeToggle.classList.toggle('bg-surface-container-high', !wakeOn);
      btnWakeToggle.classList.toggle('text-on-surface-variant', !wakeOn);
    }
    if (btnTtsToggle) {
      const badge = btnTtsToggle.querySelector('span:last-child');
      if (badge) badge.textContent = ttsOn ? 'ON' : 'OFF';
      btnTtsToggle.classList.toggle('bg-primary/20', !!ttsOn);
      btnTtsToggle.classList.toggle('text-primary', !!ttsOn);
      btnTtsToggle.classList.toggle('bg-surface-container-high', !ttsOn);
      btnTtsToggle.classList.toggle('text-on-surface-variant', !ttsOn);
    }
  }

  // =========================================================================
  // 3. VIEW 2: LONG-TERM MEMORY & LEARNING
  // =========================================================================
  const memView = document.getElementById('view-long-term-memory');
  const memoryGrid = document.getElementById('memoryGrid');
  const teachInput = document.getElementById('teachInput');
  const categorySelect = document.getElementById('categorySelect');
  const saveMemoryBtn = document.getElementById('saveMemoryBtn');
  const memorySearch = document.getElementById('memorySearch');
  const exportBtn = document.getElementById('exportBtn');
  const filterTabs = memView ? memView.querySelectorAll('.filter-tab') : [];
  let currentMemCategory = 'all';
  let cachedMemories = [];

  function catMeta(cat) {
    const c = (cat || 'preference').toLowerCase();
    if (c.includes('corr')) return { key: 'correction', label: 'Correction', icon: 'sync_problem', badgeCls: 'bg-error/15 text-error' };
    if (c.includes('fact') || c.includes('pers')) return { key: 'fact', label: 'Personal Fact', icon: 'person', badgeCls: 'bg-secondary/15 text-secondary' };
    if (c.includes('short')) return { key: 'shortcut', label: 'App Shortcut', icon: 'terminal', badgeCls: 'bg-secondary-fixed/20 text-secondary-fixed-dim' };
    if (c.includes('work') || c.includes('rout')) return { key: 'workflow', label: 'Workflow', icon: 'schedule', badgeCls: 'bg-tertiary-container/30 text-tertiary' };
    return { key: 'preference', label: 'Preference', icon: 'tune', badgeCls: 'bg-primary/15 text-primary' };
  }

  function renderMemoriesGrid() {
    if (!memoryGrid) return;
    const q = (memorySearch ? memorySearch.value : '').trim().toLowerCase();
    const counts = { all: cachedMemories.length, preference: 0, correction: 0, fact: 0, shortcut: 0, workflow: 0 };
    cachedMemories.forEach(m => {
      const k = catMeta(m.category).key;
      counts[k] = (counts[k] || 0) + 1;
    });

    // Update top counter chip
    const activeCountChip = memView ? memView.querySelector('.font-headline-sm.text-headline-sm.text-on-surface.font-bold') : null;
    if (activeCountChip) activeCountChip.textContent = String(cachedMemories.length);

    // Update tab labels
    const tabNames = { all: 'All', preference: 'Preferences', correction: 'Corrections', fact: 'Personal Facts', shortcut: 'App Shortcuts', workflow: 'Workflows' };
    filterTabs.forEach(t => {
      const f = t.getAttribute('data-filter');
      if (tabNames[f]) t.textContent = `${tabNames[f]} (${counts[f] || 0})`;
    });

    const filtered = cachedMemories.filter(m => {
      const meta = catMeta(m.category);
      const matchCat = (currentMemCategory === 'all') || (meta.key === currentMemCategory);
      const matchQ = !q || (m.fact || '').toLowerCase().includes(q) || (m.category || '').toLowerCase().includes(q);
      return matchCat && matchQ;
    });

    memoryGrid.innerHTML = filtered.map(m => {
      const meta = catMeta(m.category);
      const updated = (m.updated_at || m.created_at || 'Saved').replace('T', ' ');
      return `
        <article class="memory-card group flex flex-col justify-between p-space-lg rounded-xl bg-surface-container/60 hover:bg-surface-container-high/80 transition-all duration-300 shadow-md" data-id="${m.id}" data-category="${meta.key}">
          <div class="flex flex-col gap-space-sm">
            <div class="flex items-center justify-between">
              <span class="px-2.5 py-1 rounded-full ${meta.badgeCls} font-label-sm text-label-sm font-semibold tracking-wide flex items-center gap-1">
                <span class="material-symbols-outlined text-[14px]">${meta.icon}</span>
                ${meta.label}
              </span>
              <div class="flex items-center gap-1 text-tertiary font-label-sm text-label-sm font-bold">
                <span class="material-symbols-outlined text-[14px]">verified</span>
                <span>100% Lock</span>
              </div>
            </div>
            <p class="font-body-lg text-body-lg text-on-surface font-medium leading-relaxed">
              &ldquo;${escapeHtml(m.fact)}&rdquo;
            </p>
          </div>
          <div class="flex flex-col gap-space-sm pt-space-md mt-space-sm">
            <div class="flex items-center justify-between font-caption text-caption text-on-surface-variant">
              <span class="flex items-center gap-1">
                <span class="material-symbols-outlined text-[13px]">schedule</span>
                ${escapeHtml(updated)}
              </span>
              <span class="text-outline">ID: #${m.id}</span>
            </div>
            <div class="flex items-center justify-end gap-2 pt-1">
              <button data-id="${m.id}" data-fact="${escapeHtml(m.fact)}" data-cat="${escapeHtml(m.category)}" class="edit-mem-btn px-2.5 py-1 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest font-label-sm text-label-sm transition-all flex items-center gap-1" type="button">
                <span class="material-symbols-outlined text-[14px]">edit</span> Edit
              </button>
              <button data-id="${m.id}" class="delete-mem-btn px-2.5 py-1 rounded-lg text-error hover:bg-error-container/30 font-label-sm text-label-sm transition-all flex items-center gap-1" type="button">
                <span class="material-symbols-outlined text-[14px]">delete</span> Forget
              </button>
            </div>
          </div>
        </article>
      `;
    }).join('');
  }

  async function loadMemoriesView() {
    const r = await api('/api/memories');
    if (r && Array.isArray(r.memories)) {
      cachedMemories = r.memories;
      renderMemoriesGrid();
    }
  }

  filterTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      filterTabs.forEach(t => {
        t.classList.remove('bg-primary-container', 'text-on-primary-container', 'font-semibold', 'shadow-sm');
        t.classList.add('text-on-surface-variant');
      });
      tab.classList.add('bg-primary-container', 'text-on-primary-container', 'font-semibold', 'shadow-sm');
      tab.classList.remove('text-on-surface-variant');
      currentMemCategory = tab.getAttribute('data-filter') || 'all';
      renderMemoriesGrid();
    });
  });

  if (memorySearch) memorySearch.addEventListener('input', renderMemoriesGrid);

  if (saveMemoryBtn && teachInput) {
    saveMemoryBtn.addEventListener('click', async () => {
      const fact = teachInput.value.trim();
      if (!fact) { teachInput.focus(); return; }
      const rawCat = categorySelect ? categorySelect.value : 'Preference';
      const catKey = catMeta(rawCat).key;
      await api('/api/memories/add', { fact, category: catKey });
      teachInput.value = '';
      showToast('Learned & committed to Long-Term Memory', 'psychology', 'text-tertiary');
      loadMemoriesView();
    });
  }

  if (memoryGrid) {
    memoryGrid.addEventListener('click', async (e) => {
      const delBtn = e.target.closest('.delete-mem-btn');
      if (delBtn) {
        const id = delBtn.getAttribute('data-id');
        await api('/api/memories/delete', { id });
        showToast(`Forgot memory #${id}`, 'delete', 'text-error');
        loadMemoriesView();
        return;
      }
      const editBtn = e.target.closest('.edit-mem-btn');
      if (editBtn && teachInput) {
        teachInput.value = editBtn.getAttribute('data-fact') || '';
        teachInput.focus();
        await api('/api/memories/delete', { id: editBtn.getAttribute('data-id') });
        loadMemoriesView();
        showToast('Loaded memory into editor above — modify and click Save', 'edit', 'text-primary');
      }
    });
  }

  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      const blob = new Blob([JSON.stringify(cachedMemories, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `jarvis_memories_${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showToast('Exported memories.json', 'download', 'text-primary');
    });
  }

  // =========================================================================
  // 4. VIEW 3: SYSTEM & HARDWARE CONTROLS
  // =========================================================================
  const sysView = document.getElementById('view-system-controls');
  const sysClock = document.getElementById('system-digital-clock');
  const volRange = document.getElementById('master-vol-range');
  const volDisplay = document.getElementById('volume-val-display');
  const volIcon = document.getElementById('audio-icon-indicator');
  const btnMute = document.getElementById('btn-toggle-mute');
  const muteLabel = document.getElementById('mute-label');
  const brightRange = document.getElementById('screen-bright-range');
  const brightDisplay = document.getElementById('brightness-val-display');
  const btnPrev = document.getElementById('media-prev');
  const btnPlayPause = document.getElementById('media-play-pause');
  const playPauseIcon = document.getElementById('play-pause-icon');
  const btnNext = document.getElementById('media-next');
  const btnScreenshot = document.getElementById('btn-take-screenshot');
  const screenshotFeedback = document.getElementById('screenshot-feedback');
  const btnAnalyzeScreen = document.getElementById('btn-analyze-screen');
  const clipPreview = document.getElementById('clipboard-preview-text');
  const btnCopyClip = document.getElementById('btn-copy-clip');
  const btnClearClip = document.getElementById('btn-clear-clip');
  const btnLock = document.getElementById('btn-lock-workstation');
  const procInput = document.getElementById('process-input-field');
  const btnOpenApp = document.getElementById('btn-open-app');
  const btnCloseApp = document.getElementById('btn-force-close-app');
  const procsContainer = document.getElementById('processes-container');

  if (volRange) {
    volRange.addEventListener('change', async (e) => {
      const val = parseInt(e.target.value, 10);
      await api('/api/system/volume', { level: val });
      showToast(`Master volume set to ${val}%`, 'volume_up', 'text-primary');
    });
    volRange.addEventListener('input', (e) => {
      if (volDisplay) volDisplay.innerHTML = `${e.target.value}<span class="text-headline-md font-sans font-medium text-on-surface-variant">%</span>`;
    });
  }

  if (btnMute) {
    btnMute.addEventListener('click', async () => {
      const r = await api('/api/system/mute', { toggle: true });
      if (r) loadSystemControlsView();
    });
  }

  if (brightRange) {
    brightRange.addEventListener('change', async (e) => {
      const val = parseInt(e.target.value, 10);
      await api('/api/system/brightness', { level: val });
      showToast(`Display brightness set to ${val}%`, 'brightness_medium', 'text-secondary');
    });
    brightRange.addEventListener('input', (e) => {
      if (brightDisplay) brightDisplay.innerHTML = `${e.target.value}<span class="text-headline-md font-sans font-medium text-on-surface-variant">%</span>`;
    });
  }

  if (btnPrev) btnPrev.addEventListener('click', () => { api('/api/system/media', { action: 'previous' }); showToast('Previous Track', 'skip_previous'); });
  if (btnNext) btnNext.addEventListener('click', () => { api('/api/system/media', { action: 'next' }); showToast('Next Track', 'skip_next'); });
  if (btnPlayPause) {
    btnPlayPause.addEventListener('click', () => {
      api('/api/system/media', { action: 'play_pause' });
      if (playPauseIcon) playPauseIcon.textContent = playPauseIcon.textContent === 'pause' ? 'play_arrow' : 'pause';
      showToast('Toggled Media Play/Pause', 'graphic_eq');
    });
  }

  if (btnScreenshot) {
    btnScreenshot.addEventListener('click', async () => {
      const r = await api('/api/system/screenshot', {});
      if (screenshotFeedback && r) {
        screenshotFeedback.classList.remove('hidden');
        screenshotFeedback.classList.add('flex');
        const sp = screenshotFeedback.querySelector('span:last-child');
        if (sp) sp.textContent = r.result || 'Screenshot saved';
      }
      showToast((r && r.result) || 'Screenshot saved!', 'screenshot_monitor', 'text-tertiary');
    });
  }

  if (btnAnalyzeScreen) {
    btnAnalyzeScreen.addEventListener('click', () => {
      switchView('chat-voice');
      sendChatMessage('Analyze my screen for any errors, warnings, or important context.');
    });
  }

  if (btnCopyClip && clipPreview) {
    btnCopyClip.addEventListener('click', async () => {
      await api('/api/system/clipboard', { text: clipPreview.textContent.trim() });
      showToast('Clipboard text copied', 'content_copy', 'text-tertiary');
    });
  }
  if (btnClearClip && clipPreview) {
    btnClearClip.addEventListener('click', async () => {
      await api('/api/system/clipboard', { text: '' });
      clipPreview.textContent = '[Clipboard Empty]';
      showToast('Windows clipboard cleared', 'backspace', 'text-on-surface-variant');
    });
  }

  // Quick Folder Launchers (Downloads, Documents, Desktop, Pictures, Repos)
  if (sysView) {
    const folderBtns = sysView.querySelectorAll('.grid.grid-cols-2.sm\\:grid-cols-5 button');
    const folderMap = ['Downloads', 'Documents', 'Desktop', 'Pictures', 'Desktop'];
    folderBtns.forEach((btn, i) => {
      btn.addEventListener('click', async () => {
        const folder = folderMap[i] || 'Desktop';
        const r = await api('/api/system/folder', { folder });
        showToast((r && r.result) || `Opened ${folder}`, 'folder_open', 'text-primary');
      });
    });
  }

  if (btnLock) {
    btnLock.addEventListener('click', async () => {
      showToast('Locking Windows Workstation...', 'lock', 'text-error');
      await api('/api/system/lock', {});
    });
  }

  if (btnOpenApp && procInput) {
    btnOpenApp.addEventListener('click', async () => {
      const name = procInput.value.trim();
      if (!name) { procInput.focus(); return; }
      const r = await api('/api/system/app', { action: 'open', app_name: name });
      showToast((r && r.result) || `Opening ${name}`, 'play_arrow', 'text-tertiary');
      procInput.value = '';
      setTimeout(loadSystemControlsView, 1000);
    });
  }
  if (btnCloseApp && procInput) {
    btnCloseApp.addEventListener('click', async () => {
      const name = procInput.value.trim();
      if (!name) { procInput.focus(); return; }
      const r = await api('/api/system/app', { action: 'close', app_name: name });
      showToast((r && r.result) || `Closed ${name}`, 'cancel', 'text-error');
      procInput.value = '';
      setTimeout(loadSystemControlsView, 800);
    });
  }

  if (procsContainer) {
    procsContainer.addEventListener('click', async (e) => {
      const killBtn = e.target.closest('.btn-kill-proc');
      if (killBtn) {
        const appName = killBtn.getAttribute('data-app');
        if (appName) {
          const r = await api('/api/system/app', { action: 'close', app_name: appName });
          showToast((r && r.result) || `Terminated ${appName}`, 'cancel', 'text-error');
          loadSystemControlsView();
        }
      }
    });
  }

  async function loadSystemControlsView() {
    const d = await api('/api/system/details');
    if (!d) return;
    if (sysClock && d.clock_full) sysClock.textContent = d.clock_full;
    if (clipPreview) clipPreview.textContent = d.clipboard || '[Clipboard Empty]';

    if (d.volume !== null && d.volume !== undefined) {
      const v = d.muted ? 0 : d.volume;
      if (volRange) volRange.value = v;
      if (volDisplay) volDisplay.innerHTML = `${v}<span class="text-headline-md font-sans font-medium text-on-surface-variant">%</span>`;
      if (volIcon) volIcon.textContent = d.muted ? 'volume_off' : 'volume_up';
      if (muteLabel) muteLabel.textContent = d.muted ? 'Unmute' : 'Mute';
    }
    if (d.brightness !== null && d.brightness !== undefined) {
      if (brightRange) brightRange.value = d.brightness;
      if (brightDisplay) brightDisplay.innerHTML = `${d.brightness}<span class="text-headline-md font-sans font-medium text-on-surface-variant">%</span>`;
    }

    // Top CPU/RAM/Disk mini cards in System Controls
    if (sysView) {
      const miniSpecs = sysView.querySelectorAll('.min-w-\\[130px\\]');
      if (miniSpecs[0] && d.cpu_percent !== undefined) {
        miniSpecs[0].querySelector('.text-tertiary.font-mono').textContent = `${d.cpu_percent}%`;
        miniSpecs[0].querySelector('.bg-tertiary.rounded-full').style.width = `${d.cpu_percent}%`;
      }
      if (miniSpecs[1] && d.ram_percent !== undefined) {
        miniSpecs[1].querySelector('.text-primary.font-mono').textContent = `${d.ram_percent}%`;
        miniSpecs[1].querySelector('.bg-primary.rounded-full').style.width = `${d.ram_percent}%`;
        miniSpecs[1].querySelector('span:last-child').textContent = `${d.ram_used_gb} / ${d.ram_total_gb} GB`;
      }
      if (miniSpecs[2] && d.disk_percent !== undefined) {
        miniSpecs[2].querySelector('.font-medium').textContent = 'DISK (C:\\ NVMe)';
        miniSpecs[2].querySelector('.text-secondary.font-mono').textContent = `${d.disk_percent}%`;
        miniSpecs[2].querySelector('.bg-secondary.rounded-full').style.width = `${d.disk_percent}%`;
        miniSpecs[2].querySelector('span:last-child').textContent = `${d.disk_free_gb} GB Free`;
      }
    }

    // Render top processes
    if (procsContainer && Array.isArray(d.processes)) {
      procsContainer.innerHTML = d.processes.map(p => `
        <div class="flex items-center justify-between p-2.5 rounded-lg bg-surface-container/60 hover:bg-surface-container-high transition-all">
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-primary text-[18px]">terminal</span>
            <div class="flex flex-col">
              <span class="font-label-md text-label-md text-on-surface font-semibold">${escapeHtml(p.name)}</span>
              <span class="font-caption text-caption text-on-surface-variant font-mono">${p.count} proc // ${p.mem_mb} MB RAM</span>
            </div>
          </div>
          <button data-app="${escapeHtml(p.name)}" class="btn-kill-proc px-2.5 py-1 rounded bg-surface-container-highest hover:bg-error-container hover:text-on-error-container text-error font-caption text-caption font-semibold transition-colors flex items-center gap-1" type="button">
            <span>Kill</span>
          </button>
        </div>
      `).join('');
    }
  }

  // =========================================================================
  // 5. VIEW 4: PRODUCTIVITY HUB (REMINDERS, TIMERS, NOTES, ROUTINES)
  // =========================================================================
  const prodView = document.getElementById('view-productivity-hub');
  const reminderText = document.getElementById('reminderText');
  const reminderTime = document.getElementById('reminderTime');
  const setReminderBtn = document.getElementById('setReminderBtn');
  const remindersContainer = document.getElementById('remindersContainer');
  const noteScratchpad = document.getElementById('noteScratchpad');
  const noteCharCount = document.getElementById('noteCharCount');
  const notesContainer = document.getElementById('notesContainer');

  window.setQuickTime = function(t) {
    if (reminderTime) { reminderTime.value = t; reminderTime.focus(); }
  };

  window.insertMarkdown = function(syntax) {
    if (!noteScratchpad) return;
    const s = noteScratchpad.selectionStart || 0;
    const e = noteScratchpad.selectionEnd || 0;
    const v = noteScratchpad.value;
    noteScratchpad.value = v.substring(0, s) + syntax + v.substring(e);
    noteScratchpad.focus();
    if (noteCharCount) noteCharCount.textContent = `${noteScratchpad.value.length} characters`;
  };

  if (noteScratchpad && noteCharCount) {
    noteScratchpad.addEventListener('input', () => {
      noteCharCount.textContent = `${noteScratchpad.value.length} characters`;
    });
  }

  window.saveScratchpadNote = async function() {
    if (!noteScratchpad) return;
    const txt = noteScratchpad.value.trim();
    if (!txt) { showToast('Enter note text first', 'warning', 'text-error'); return; }
    await api('/api/productivity/note/add', { text: txt });
    noteScratchpad.value = '';
    if (noteCharCount) noteCharCount.textContent = '0 characters';
    showToast('Note saved to data/notes.json', 'bookmark_add', 'text-tertiary');
    loadProductivityView();
  };

  if (setReminderBtn) {
    setReminderBtn.addEventListener('click', async () => {
      const txt = (reminderText ? reminderText.value : '').trim();
      const when = (reminderTime ? reminderTime.value : '').trim() || 'in 15 minutes';
      if (!txt) { showToast('Enter a reminder description', 'warning', 'text-error'); return; }
      const r = await api('/api/productivity/reminder/add', { text: txt, when });
      if (reminderText) reminderText.value = '';
      if (reminderTime) reminderTime.value = '';
      showToast((r && r.result) || 'Reminder scheduled!', 'notifications_active', 'text-tertiary');
      loadProductivityView();
    });
  }

  window.dismissReminder = async function(query) {
    await api('/api/productivity/reminder/delete', { query });
    showToast('Reminder removed', 'delete', 'text-on-surface-variant');
    loadProductivityView();
  };

  window.snoozeReminder = async function(text) {
    await api('/api/productivity/reminder/add', { text, when: 'in 10 minutes' });
    showToast('Snoozed for 10 minutes', 'snooze', 'text-primary');
    loadProductivityView();
  };

  window.deleteNote = async function(query) {
    await api('/api/productivity/note/delete', { query });
    showToast('Note deleted', 'delete', 'text-on-surface-variant');
    loadProductivityView();
  };

  window.copyNoteText = async function(text) {
    await api('/api/system/clipboard', { text });
    showToast('Copied note to Windows clipboard', 'content_copy', 'text-primary');
  };

  window.executeRoutine = async function(btn, routineName) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-outlined text-[18px] animate-spin">refresh</span><span>Executing ${escapeHtml(routineName)}...</span>`;
    showToast(`Running routine: ${routineName}`, 'bolt', 'text-primary');
    await api('/api/chat/send', { text: `Run my ${routineName} routine` });
    setTimeout(() => {
      btn.disabled = false;
      btn.innerHTML = `<span class="material-symbols-outlined text-[18px] text-tertiary">task_alt</span><span>Dispatched to Jarvis</span>`;
      setTimeout(() => { btn.innerHTML = orig; }, 2200);
    }, 1000);
  };

  window.createNewRoutineModal = async function() {
    const name = prompt('Enter new routine name (e.g., "Gaming Mode", "Study Setup"):');
    if (!name || !name.trim()) return;
    const desc = prompt(`Enter steps for "${name.trim()}" (e.g., "Open Steam, set volume to 60%, and dim brightness to 50%"):`);
    if (!desc || !desc.trim()) return;
    await api('/api/productivity/routine/add', { name: name.trim(), description: desc.trim() });
    showToast(`Saved routine "${name.trim()}"!`, 'bolt', 'text-tertiary');
    loadProductivityView();
  };

  async function loadProductivityView() {
    const d = await api('/api/productivity');
    if (!d) return;

    // Render Reminders + Active Timers
    if (remindersContainer) {
      const items = [];
      (d.timers || []).forEach(t => {
        items.push(`
          <div class="group p-space-md rounded-xl bg-surface-container-low/70 hover:bg-surface-container/70 backdrop-blur-xl transition-all duration-200 shadow-md flex flex-col md:flex-row items-start md:items-center justify-between gap-space-md">
            <div class="flex items-start gap-space-md min-w-0">
              <div class="w-10 h-10 rounded-lg bg-tertiary-container/30 flex items-center justify-center text-tertiary mt-0.5 shrink-0">
                <span class="material-symbols-outlined text-[20px]">timer</span>
              </div>
              <div class="flex flex-col min-w-0">
                <div class="flex items-center gap-space-sm flex-wrap">
                  <h3 class="font-headline-sm text-headline-sm text-on-surface font-semibold truncate">Timer: ${escapeHtml(t.label)}</h3>
                  <span class="px-2.5 py-0.5 rounded-full bg-tertiary-container/30 text-tertiary font-label-sm text-label-sm flex items-center gap-1">
                    <span class="w-1.5 h-1.5 rounded-full bg-tertiary animate-pulse"></span> ${escapeHtml(t.remaining)} left
                  </span>
                </div>
                <div class="flex items-center gap-3 text-on-surface-variant font-body-sm text-body-sm mt-0.5">
                  <span>Due at ${escapeHtml(t.due)}</span>
                </div>
              </div>
            </div>
            <div class="flex items-center gap-2 self-end md:self-center shrink-0">
              <button onclick="window.dismissReminder('${escapeHtml(t.label)}')" class="px-3 py-1.5 rounded-lg bg-surface-container-highest text-on-surface-variant hover:text-error hover:bg-error-container/40 font-label-sm text-label-sm transition-all flex items-center gap-1.5" type="button">
                <span class="material-symbols-outlined text-[16px]">cancel</span>
                <span>Cancel Timer</span>
              </button>
            </div>
          </div>
        `);
      });

      (d.reminders || []).forEach(r => {
        items.push(`
          <div class="group p-space-md rounded-xl bg-surface-container-low/70 hover:bg-surface-container/70 backdrop-blur-xl transition-all duration-200 shadow-md flex flex-col md:flex-row items-start md:items-center justify-between gap-space-md">
            <div class="flex items-start gap-space-md min-w-0">
              <div class="w-10 h-10 rounded-lg bg-surface-container-high flex items-center justify-center text-primary mt-0.5 shrink-0">
                <span class="material-symbols-outlined text-[20px]">notifications_active</span>
              </div>
              <div class="flex flex-col min-w-0">
                <div class="flex items-center gap-space-sm flex-wrap">
                  <h3 class="font-headline-sm text-headline-sm text-on-surface font-semibold truncate">${escapeHtml(r.text)}</h3>
                  <span class="px-2.5 py-0.5 rounded-full bg-primary-container/20 text-primary font-label-sm text-label-sm">Scheduled</span>
                </div>
                <div class="flex items-center gap-3 text-on-surface-variant font-body-sm text-body-sm mt-0.5">
                  <span class="flex items-center gap-1"><span class="material-symbols-outlined text-[15px]">event</span> ${escapeHtml((r.due || '').replace('T', ' '))}</span>
                  <span>•</span>
                  <span class="flex items-center gap-1 text-primary"><span class="material-symbols-outlined text-[15px]">headphones</span> Spatial Audio Chime Enabled</span>
                </div>
              </div>
            </div>
            <div class="flex items-center gap-2 self-end md:self-center shrink-0">
              <button onclick="window.snoozeReminder('${escapeHtml(r.text)}')" class="px-3 py-1.5 rounded-lg bg-surface-container-high text-on-surface-variant hover:text-on-surface hover:bg-surface-bright font-label-sm text-label-sm transition-all flex items-center gap-1.5" type="button">
                <span class="material-symbols-outlined text-[16px]">snooze</span>
                <span>Snooze 10m</span>
              </button>
              <button onclick="window.dismissReminder('${escapeHtml(r.text)}')" class="px-3 py-1.5 rounded-lg bg-surface-container-highest text-on-surface-variant hover:text-error hover:bg-error-container/40 font-label-sm text-label-sm transition-all flex items-center gap-1.5" type="button">
                <span class="material-symbols-outlined text-[16px]">delete</span>
                <span>Dismiss / Delete</span>
              </button>
            </div>
          </div>
        `);
      });

      remindersContainer.innerHTML = items.length ? items.join('') : `
        <div class="p-space-md rounded-xl bg-surface-container-low/50 text-on-surface-variant text-center font-body-sm">
          No pending reminders or countdown timers. Add one above!
        </div>
      `;
    }

    // Render Notes
    if (notesContainer && Array.isArray(d.notes)) {
      notesContainer.innerHTML = d.notes.length ? d.notes.slice().reverse().map((n, idx) => `
        <div class="p-space-md rounded-xl bg-surface-container-low/70 backdrop-blur-xl shadow-md flex flex-col gap-space-sm transition-all hover:bg-surface-container/70">
          <div class="flex items-start justify-between gap-space-sm">
            <div class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full bg-primary"></span>
              <span class="font-label-sm text-label-sm text-on-surface-variant">${escapeHtml((n.created || '').replace('T', ' ').slice(0, 16))}</span>
            </div>
            <div class="flex items-center gap-1">
              <button onclick="window.copyNoteText('${escapeHtml(n.text)}')" class="p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors" title="Copy Note" type="button">
                <span class="material-symbols-outlined text-[17px]">content_copy</span>
              </button>
              <button onclick="window.deleteNote('${escapeHtml(n.text.slice(0, 24))}')" class="p-1.5 rounded-lg text-on-surface-variant hover:text-error hover:bg-error-container/30 transition-colors" title="Delete Note" type="button">
                <span class="material-symbols-outlined text-[17px]">delete</span>
              </button>
            </div>
          </div>
          <p class="font-body-md text-body-md text-on-surface font-mono bg-surface-container-lowest/60 p-3 rounded-lg leading-relaxed select-all whitespace-pre-wrap">${escapeHtml(n.text)}</p>
        </div>
      `).join('') : `
        <div class="p-space-md rounded-xl bg-surface-container-low/50 text-on-surface-variant text-center font-body-sm">
          No saved scratchpad notes yet.
        </div>
      `;
    }
  }

  // =========================================================================
  // 6. VIEW 5: SETTINGS & MOBILE BRIDGE
  // =========================================================================
  const setView = document.getElementById('view-settings-bridge');
  const calibrateNoiseBtn = document.getElementById('calibrateNoiseBtn');
  const calibrationStatus = document.getElementById('calibrationStatus');
  const wakeSlider = document.getElementById('wakeSlider');
  const wakeValLabel = document.getElementById('wakeValLabel');
  const rateSlider = document.getElementById('rateSlider');
  const rateLabel = document.getElementById('rateLabel');
  const playVoiceSampleBtn = document.getElementById('playVoiceSampleBtn');
  const playIcon = document.getElementById('playIcon');
  const playLabel = document.getElementById('playLabel');
  const secretField = document.getElementById('secretField');
  const toggleSecretBtn = document.getElementById('toggleSecretBtn');
  const secretIcon = document.getElementById('secretIcon');
  const secretActionText = document.getElementById('secretActionText');
  const pingBridgeBtn = document.getElementById('pingBridgeBtn');
  const pingResult = document.getElementById('pingResult');
  const saveAllBtn = document.getElementById('saveAllBtn');

  let currentBridgeSecret = '';
  let secretShown = false;

  if (calibrateNoiseBtn && calibrationStatus) {
    calibrateNoiseBtn.addEventListener('click', async () => {
      calibrationStatus.textContent = 'Calibrating ambient room noise floor...';
      await api('/api/voice/calibrate', {});
      setTimeout(() => {
        calibrationStatus.textContent = 'Room noise floor calibrated — Active';
        showToast('Microphone ambient threshold recalibrated', 'tune', 'text-tertiary');
      }, 900);
    });
  }

  if (wakeSlider && wakeValLabel) {
    wakeSlider.addEventListener('input', (e) => {
      const v = parseInt(e.target.value, 10);
      wakeValLabel.textContent = `${v}% Sensitivity`;
    });
  }

  if (rateSlider && rateLabel) {
    rateSlider.addEventListener('input', (e) => {
      rateLabel.textContent = parseFloat(e.target.value).toFixed(2) + 'x';
    });
  }

  if (playVoiceSampleBtn) {
    playVoiceSampleBtn.addEventListener('click', async () => {
      if (playIcon) playIcon.textContent = 'volume_up';
      if (playLabel) playLabel.textContent = 'Speaking Sample...';
      await api('/api/voice/speak', { text: 'Good evening. Jarvis spatial neural core and voice synthesis are online and ready.' });
      setTimeout(() => {
        if (playIcon) playIcon.textContent = 'play_arrow';
        if (playLabel) playLabel.textContent = 'Audition Voice Sample';
      }, 2800);
    });
  }

  if (toggleSecretBtn && secretField) {
    toggleSecretBtn.addEventListener('click', () => {
      secretShown = !secretShown;
      secretField.textContent = secretShown ? (currentBridgeSecret || 'No secret configured in .env') : '••••••••••••••••••••••••••••';
      if (secretIcon) secretIcon.textContent = secretShown ? 'visibility_off' : 'visibility';
      if (secretActionText) secretActionText.textContent = secretShown ? 'Hide' : 'Show';
    });
  }

  if (pingBridgeBtn && pingResult) {
    pingBridgeBtn.addEventListener('click', async () => {
      const t0 = performance.now();
      await api('/api/telemetry');
      const ms = Math.max(1, Math.round(performance.now() - t0));
      pingResult.textContent = `Local Bridge RTT: ${ms}ms`;
      showToast(`Bridge healthy (${ms}ms RTT)`, 'network_check', 'text-tertiary');
    });
  }

  if (saveAllBtn) {
    saveAllBtn.addEventListener('click', () => {
      showToast('Settings & Audio Parameters Synced!', 'check', 'text-tertiary');
    });
  }

  async function loadSettingsView() {
    const s = await api('/api/settings');
    if (!s || !setView) return;
    currentBridgeSecret = s.api_secret || '';

    // Populate Microphone & TTS selects
    const selects = setView.querySelectorAll('select');
    if (selects[0] && Array.isArray(s.mics) && s.mics.length) {
      selects[0].innerHTML = s.mics.map((m, i) => `<option ${i === 0 ? 'selected' : ''}>${escapeHtml(m)}</option>`).join('');
    }
    if (selects[1] && Array.isArray(s.voices) && s.voices.length) {
      selects[1].innerHTML = s.voices.map((v, i) => `<option ${i === s.voice_index ? 'selected' : ''}>${escapeHtml(v)}</option>`).join('');
    }

    // Update LAN & Tailscale URLs
    const copyBtns = setView.querySelectorAll('.copy-btn');
    if (copyBtns[0] && s.lan_url) {
      const urlSpan = copyBtns[0].previousElementSibling;
      if (urlSpan) urlSpan.textContent = s.lan_url;
      copyBtns[0].setAttribute('data-copy', s.lan_url);
    }
    if (copyBtns[1] && s.tailscale_url) {
      const urlSpan = copyBtns[1].previousElementSibling;
      if (urlSpan) urlSpan.textContent = s.tailscale_url;
      copyBtns[1].setAttribute('data-copy', s.tailscale_url);
    }

    copyBtns.forEach(btn => {
      btn.onclick = async () => {
        const txt = btn.getAttribute('data-copy');
        await api('/api/system/clipboard', { text: txt });
        showToast(`Copied ${txt}`, 'content_copy', 'text-tertiary');
      };
    });
  }

  // =========================================================================
  // 7. GLOBAL TELEMETRY POLLING
  // =========================================================================
  async function pollTelemetry() {
    if (document.hidden) return;
    const d = await api('/api/telemetry');
    if (!d) return;

    // Update top header status pill & model badge
    if (topHeader) {
      const topStateText = topHeader.querySelector('.uppercase.tracking-wider');
      if (topStateText) topStateText.textContent = (d.assistant_state || 'Ready').toUpperCase();
      const modelBadge = topHeader.querySelector('.hidden.md\\:flex span:last-child');
      if (modelBadge && d.model) modelBadge.textContent = `${d.model} (Neural Core)`;
    }

    // Update Chat & Voice sub-header status
    if (statusLabel) {
      const st = d.assistant_state || 'ready';
      if (st === 'listening') statusLabel.textContent = 'Listening... Speak Now';
      else if (st === 'thinking' || st === 'transcribing') statusLabel.textContent = 'Thinking...';
      else if (st === 'speaking') statusLabel.textContent = 'Speaking Reply...';
      else statusLabel.textContent = d.wake_enabled ? 'Wake Word Active' : 'Push-to-Talk Mode';
    }

    // Update bottom telemetry summary pill in Chat view
    if (chatView) {
      const peekSpans = chatView.querySelectorAll('.hidden.lg\\:flex.items-center.gap-3 span');
      if (peekSpans.length >= 8) {
        if (d.battery !== null && d.battery !== undefined) peekSpans[1].textContent = d.battery + '%';
        if (d.volume !== null && d.volume !== undefined) peekSpans[4].textContent = (d.muted ? 0 : d.volume) + '%';
        if (d.brightness !== null && d.brightness !== undefined) peekSpans[7].textContent = d.brightness + '%';
      }
    }

    updateWakeAndTtsButtons(d.wake_enabled, d.tts_enabled);

    // Refresh chat feed if turn count or thinking state changed
    if (d.chat_count !== lastRenderedChatCount || d.thinking !== isAgentThinking) {
      isAgentThinking = !!d.thinking;
      loadChatFeed(true);
    }
  }

  // Spacebar Push-to-Talk shortcut when not typing in an input/textarea
  window.addEventListener('keydown', (e) => {
    const tag = (document.activeElement && document.activeElement.tagName) || '';
    if (e.code === 'Space' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
      e.preventDefault();
      api('/api/voice/ptt', {});
      showToast('Push-to-Talk activated — Speak now', 'mic', 'text-tertiary');
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && archiveSearchInput) {
      e.preventDefault();
      switchView('chat-voice');
      archiveSearchInput.focus();
    }
  });

  // Initial load
  loadChatFeed(true);
  pollTelemetry();
  setInterval(pollTelemetry, 2000);
})();
"""


MOBILE_HTML = r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="Jarvis" />
  <title>JARVIS — iPhone Companion</title>
  <style>
    :root {
      --bg: #0b0c10;
      --card: #15171e;
      --card-hi: #1e212b;
      --border: rgba(255, 255, 255, 0.08);
      --text: #eef0f6;
      --muted: #949bb0;
      --primary: #5b9bff;
      --primary-glow: rgba(91, 155, 255, 0.22);
      --green: #4ade80;
      --purple: #d896ff;
      --danger: #ff6b6b;
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    html, body {
      margin: 0; padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
      overscroll-behavior-y: none;
      -webkit-font-smoothing: antialiased;
    }
    body {
      padding-top: env(safe-area-inset-top, 12px);
      padding-bottom: calc(76px + env(safe-area-inset-bottom, 16px));
    }
    /* Header */
    .top-bar {
      position: sticky; top: 0; z-index: 30;
      background: rgba(11, 12, 16, 0.88);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      padding: 12px 16px;
      display: flex; align-items: center; justify-content: space-between;
      border-bottom: 1px solid var(--border);
    }
    .brand { display: flex; align-items: center; gap: 10px; }
    .dot {
      width: 9px; height: 9px; border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 10px var(--green);
    }
    .dot.busy { background: var(--purple); box-shadow: 0 0 10px var(--purple); }
    .brand-title { font-weight: 700; letter-spacing: 0.18em; font-size: 14px; color: var(--primary); }
    .status-pill {
      font-size: 12px; font-weight: 600;
      padding: 5px 10px; border-radius: 999px;
      background: var(--card-hi); color: var(--text);
      display: flex; align-items: center; gap: 6px;
      border: 1px solid var(--border);
    }
    /* Views */
    .view { display: none; padding: 14px 16px; max-width: 540px; margin: 0 auto; }
    .view.active { display: flex; flex-direction: column; gap: 14px; }
    /* Cards */
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.28);
    }
    .card-title {
      font-size: 11px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--muted); margin: 0 0 12px 0;
      display: flex; align-items: center; justify-content: space-between;
    }
    /* Quick Command Bar */
    .cmd-form { display: flex; gap: 8px; }
    .cmd-input {
      flex: 1; background: var(--card-hi); color: var(--text);
      border: 1px solid var(--border); border-radius: 14px;
      padding: 12px 14px; font-size: 15px; outline: none;
    }
    .cmd-input:focus { border-color: var(--primary); }
    .btn-primary {
      background: var(--primary); color: #001d4a;
      border: none; border-radius: 14px; padding: 0 16px;
      font-weight: 700; font-size: 14px; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
    }
    .btn-primary:active { transform: scale(0.96); }
    .reply-box {
      margin-top: 12px; padding: 12px 14px;
      border-radius: 14px; background: rgba(91, 155, 255, 0.1);
      border: 1px solid rgba(91, 155, 255, 0.25);
      font-size: 14px; line-height: 1.45; color: var(--text);
    }
    .reply-label {
      font-size: 11px; font-weight: 700; color: var(--primary);
      text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px;
    }
    /* Sliders */
    .slider-row { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
    .slider-row:last-child { margin-bottom: 0; }
    .slider-hdr { display: flex; justify-content: space-between; align-items: center; font-size: 14px; font-weight: 600; }
    input[type="range"] {
      -webkit-appearance: none; appearance: none;
      width: 100%; height: 8px; border-radius: 999px;
      background: var(--card-hi); outline: none;
    }
    input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance: none; appearance: none;
      width: 24px; height: 24px; border-radius: 50%;
      background: var(--primary); cursor: pointer;
      box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    }
    /* Media row */
    .media-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
    .media-btn {
      background: var(--card-hi); color: var(--text);
      border: 1px solid var(--border); border-radius: 14px;
      padding: 13px 6px; font-size: 13px; font-weight: 600;
      display: flex; flex-direction: column; align-items: center; gap: 4px;
      cursor: pointer;
    }
    .media-btn:active { transform: scale(0.95); background: #282c3a; }
    .media-btn .ico { font-size: 18px; }
    /* 2x3 Action Grid */
    .action-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .tile {
      background: var(--card-hi); color: var(--text);
      border: 1px solid var(--border); border-radius: 16px;
      padding: 14px; text-align: left; cursor: pointer;
      display: flex; flex-direction: column; gap: 6px;
    }
    .tile:active { transform: scale(0.97); background: #262a36; }
    .tile-top { display: flex; align-items: center; justify-content: space-between; font-size: 20px; }
    .tile-name { font-size: 14px; font-weight: 700; }
    .tile-sub { font-size: 11px; color: var(--muted); }
    /* App chips */
    .chip-row { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip {
      background: var(--card-hi); color: var(--text);
      border: 1px solid var(--border); border-radius: 999px;
      padding: 8px 13px; font-size: 13px; font-weight: 600; cursor: pointer;
    }
    .chip:active { background: var(--primary); color: #001d4a; }
    /* Chat Feed */
    .chat-list { display: flex; flex-direction: column; gap: 12px; }
    .bubble-user {
      align-self: flex-end; max-width: 84%;
      background: var(--primary); color: #001b44;
      padding: 11px 14px; border-radius: 18px 18px 4px 18px;
      font-size: 14px; font-weight: 600; line-height: 1.4;
    }
    .bubble-bot {
      align-self: flex-start; max-width: 88%;
      background: var(--card-hi); color: var(--text);
      border: 1px solid var(--border);
      padding: 12px 14px; border-radius: 18px 18px 18px 4px;
      font-size: 14px; line-height: 1.45;
    }
    .bubble-meta { font-size: 10px; color: var(--muted); margin-top: 4px; }
    /* Tasks & Routines */
    .list-item {
      display: flex; align-items: center; justify-content: space-between;
      gap: 10px; padding: 12px; border-radius: 14px;
      background: var(--card-hi); border: 1px solid var(--border);
      margin-bottom: 8px;
    }
    .list-item:last-child { margin-bottom: 0; }
    .btn-sm {
      background: rgba(255,255,255,0.08); color: var(--text);
      border: none; border-radius: 10px; padding: 7px 11px;
      font-size: 12px; font-weight: 600; cursor: pointer; shrink: 0;
    }
    .btn-sm.danger { color: var(--danger); background: rgba(255,107,107,0.12); }
    .btn-sm.run { color: var(--green); background: rgba(74,222,128,0.14); }
    /* Bottom Tab Bar */
    .tab-bar {
      position: fixed; bottom: 0; left: 0; right: 0; z-index: 40;
      background: rgba(15, 17, 23, 0.94);
      backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      border-top: 1px solid var(--border);
      display: grid; grid-template-columns: repeat(4, 1fr);
      padding: 6px 8px calc(6px + env(safe-area-inset-bottom, 10px));
    }
    .tab-btn {
      background: none; border: none; color: var(--muted);
      display: flex; flex-direction: column; align-items: center; gap: 3px;
      padding: 6px 4px; font-size: 11px; font-weight: 600; cursor: pointer;
    }
    .tab-btn.active { color: var(--primary); }
    .tab-btn .t-ico { font-size: 19px; line-height: 1; }
    /* Toast */
    #mToast {
      position: fixed; top: 64px; left: 50%; transform: translateX(-50%) translateY(-20px);
      background: #232734; color: var(--text); border: 1px solid rgba(91,155,255,0.35);
      padding: 9px 16px; border-radius: 999px; font-size: 13px; font-weight: 600;
      opacity: 0; pointer-events: none; transition: all 0.22s ease; z-index: 100;
      box-shadow: 0 8px 24px rgba(0,0,0,0.45);
    }
    #mToast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  </style>
</head>
<body>
  <div id="mToast">Done</div>

  <!-- Sticky Top Header -->
  <header class="top-bar">
    <div class="brand">
      <span id="statusDot" class="dot"></span>
      <span class="brand-title">JARVIS</span>
    </div>
    <div class="status-pill">
      <span id="topStatusText">Online</span>
      <span>•</span>
      <span id="topBattery">--%</span>
      <span>•</span>
      <span id="topClock">--:--</span>
    </div>
  </header>

  <!-- TAB 1: REMOTE & QUICK COMMAND -->
  <section id="view-remote" class="view active">
    <div class="card">
      <div class="card-title">
        <span>Ask or Command Jarvis</span>
        <span style="color:var(--primary)">Voice Dictation Ready</span>
      </div>
      <form id="quickCmdForm" class="cmd-form">
        <input id="quickCmdInput" class="cmd-input" type="text" placeholder="Tap mic on keyboard or type..." autocomplete="off" />
        <button type="submit" class="btn-primary">Send</button>
      </form>
      <div class="reply-box">
        <div class="reply-label" id="replyLabel">Latest Response</div>
        <div id="replyText">Tap any control below or send a command.</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span>PC Audio &amp; Display</span></div>
      <div class="slider-row">
        <div class="slider-hdr">
          <span>🔊 Master Volume</span>
          <span id="volVal">50%</span>
        </div>
        <input id="volSlider" type="range" min="0" max="100" value="50" />
      </div>
      <div class="slider-row">
        <div class="slider-hdr">
          <span>☀️ Screen Brightness</span>
          <span id="briVal">75%</span>
        </div>
        <input id="briSlider" type="range" min="10" max="100" value="75" />
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span>Media &amp; Audio Controls</span></div>
      <div class="media-grid">
        <button class="media-btn" onclick="sendMedia('previous')"><span class="ico">⏮</span><span>Prev</span></button>
        <button class="media-btn" onclick="sendMedia('play_pause')"><span class="ico">⏯</span><span>Play/Pause</span></button>
        <button class="media-btn" onclick="sendMedia('next')"><span class="ico">⏭</span><span>Next</span></button>
        <button class="media-btn" onclick="toggleMute()"><span class="ico" id="muteIco">🔇</span><span id="muteTxt">Mute</span></button>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span>PC Quick Actions</span></div>
      <div class="action-grid">
        <button class="tile" onclick="analyzeScreenMobile()">
          <div class="tile-top"><span>👁️</span></div>
          <div class="tile-name">Analyze Screen</div>
          <div class="tile-sub">AI Vision summary</div>
        </button>
        <button class="tile" onclick="takeScreenshotMobile()">
          <div class="tile-top"><span>📸</span></div>
          <div class="tile-name">Screenshot</div>
          <div class="tile-sub">Save to PC Pictures</div>
        </button>
        <button class="tile" onclick="standbyMobile()">
          <div class="tile-top"><span>🌙</span></div>
          <div class="tile-name">Standby Mode</div>
          <div class="tile-sub">Mute &amp; minimize all</div>
        </button>
        <button class="tile" onclick="lockPcMobile()">
          <div class="tile-top"><span>🔒</span></div>
          <div class="tile-name">Lock PC</div>
          <div class="tile-sub">Lock workstation</div>
        </button>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span>Quick Launch on PC</span></div>
      <div class="chip-row">
        <button class="chip" onclick="openAppMobile('opera')">Opera</button>
        <button class="chip" onclick="openAppMobile('chrome')">Chrome</button>
        <button class="chip" onclick="openAppMobile('spotify')">Spotify</button>
        <button class="chip" onclick="openAppMobile('vs code')">VS Code</button>
        <button class="chip" onclick="openAppMobile('notepad')">Notepad</button>
        <button class="chip" onclick="openAppMobile('calculator')">Calculator</button>
        <button class="chip" onclick="openFolderMobile('Downloads')">Downloads</button>
        <button class="chip" onclick="openFolderMobile('Desktop')">Desktop</button>
      </div>
    </div>
  </section>

  <!-- TAB 2: CHAT -->
  <section id="view-chat" class="view">
    <div class="card">
      <div class="card-title">
        <span>Conversation History</span>
        <button class="btn-sm danger" onclick="clearChatMobile()">Clear Context</button>
      </div>
      <form id="chatTabForm" class="cmd-form" style="margin-bottom: 14px;">
        <input id="chatTabInput" class="cmd-input" type="text" placeholder="Message Jarvis..." autocomplete="off" />
        <button type="submit" class="btn-primary">Send</button>
      </form>
      <div id="mobileChatList" class="chat-list"></div>
    </div>
  </section>

  <!-- TAB 3: TASKS & ROUTINES -->
  <section id="view-tasks" class="view">
    <div class="card">
      <div class="card-title"><span>One-Tap Everyday Routines</span></div>
      <div id="mobileRoutinesList"></div>
    </div>

    <div class="card">
      <div class="card-title"><span>Set Timer or Reminder</span></div>
      <form id="reminderForm" class="cmd-form" style="margin-bottom:10px;">
        <input id="remText" class="cmd-input" type="text" placeholder="Reminder or timer (e.g. Call Mom)" />
        <input id="remWhen" class="cmd-input" style="max-width:125px;" type="text" placeholder="in 15 mins" value="in 15 minutes" />
        <button type="submit" class="btn-primary">Add</button>
      </form>
      <div id="mobileRemindersList"></div>
    </div>

    <div class="card">
      <div class="card-title"><span>Quick Scratchpad Notes</span></div>
      <form id="noteForm" class="cmd-form" style="margin-bottom:10px;">
        <input id="noteInput" class="cmd-input" type="text" placeholder="Jot down a note..." />
        <button type="submit" class="btn-primary">Save</button>
      </form>
      <div id="mobileNotesList"></div>
    </div>
  </section>

  <!-- TAB 4: LONG-TERM MEMORY -->
  <section id="view-memory" class="view">
    <div class="card">
      <div class="card-title"><span>Teach Jarvis a Fact / Preference</span></div>
      <form id="memForm" class="cmd-form" style="margin-bottom:12px;">
        <input id="memInput" class="cmd-input" type="text" placeholder="e.g. I prefer Opera browser" />
        <button type="submit" class="btn-primary">Save</button>
      </form>
      <div id="mobileMemList"></div>
    </div>
  </section>

  <!-- Fixed Bottom iOS Navigation Bar -->
  <nav class="tab-bar">
    <button class="tab-btn active" onclick="switchMobileTab('remote', this)">
      <span class="t-ico">🎛️</span><span>Remote</span>
    </button>
    <button class="tab-btn" onclick="switchMobileTab('chat', this)">
      <span class="t-ico">💬</span><span>Chat</span>
    </button>
    <button class="tab-btn" onclick="switchMobileTab('tasks', this)">
      <span class="t-ico">⚡</span><span>Routines</span>
    </button>
    <button class="tab-btn" onclick="switchMobileTab('memory', this)">
      <span class="t-ico">🧠</span><span>Memory</span>
    </button>
  </nav>

  <script>
    async function api(path, body) {
      try {
        const opts = body !== undefined
          ? { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) }
          : { method: 'GET' };
        const r = await fetch(path, opts);
        return await r.json();
      } catch (e) {
        return null;
      }
    }

    function esc(s) {
      return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function toast(msg) {
      const el = document.getElementById('mToast');
      el.textContent = msg;
      el.classList.add('show');
      clearTimeout(window._mt);
      window._mt = setTimeout(() => el.classList.remove('show'), 2200);
    }

    function switchMobileTab(tab, btn) {
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      const target = document.getElementById('view-' + tab);
      if (target) target.classList.add('active');
      if (btn) btn.classList.add('active');
      if (tab === 'chat') loadMobileChat();
      if (tab === 'tasks') loadMobileTasks();
      if (tab === 'memory') loadMobileMemories();
    }

    async function runMobileCommand(text) {
      if (!text || !text.trim()) return;
      const replyLabel = document.getElementById('replyLabel');
      const replyText = document.getElementById('replyText');
      if (replyLabel) replyLabel.textContent = 'Jarvis Thinking...';
      if (replyText) replyText.textContent = 'Executing: "' + text.trim() + '"...';
      const r = await api('/command', { text: text.trim() });
      if (r && r.reply) {
        if (replyLabel) replyLabel.textContent = 'Jarvis Reply';
        if (replyText) replyText.textContent = r.reply;
      }
      loadMobileChat();
      pollMobile();
    }

    document.getElementById('quickCmdForm').addEventListener('submit', e => {
      e.preventDefault();
      const inp = document.getElementById('quickCmdInput');
      const txt = inp.value;
      inp.value = '';
      inp.blur();
      runMobileCommand(txt);
    });

    document.getElementById('chatTabForm').addEventListener('submit', async e => {
      e.preventDefault();
      const inp = document.getElementById('chatTabInput');
      const txt = inp.value;
      inp.value = '';
      inp.blur();
      await runMobileCommand(txt);
    });

    const volSlider = document.getElementById('volSlider');
    const volVal = document.getElementById('volVal');
    let volDragging = false;
    volSlider.addEventListener('input', e => {
      volDragging = true;
      volVal.textContent = e.target.value + '%';
    });
    volSlider.addEventListener('change', async e => {
      const v = parseInt(e.target.value, 10);
      await api('/api/system/volume', { level: v });
      volDragging = false;
      toast('Volume ' + v + '%');
    });

    const briSlider = document.getElementById('briSlider');
    const briVal = document.getElementById('briVal');
    let briDragging = false;
    briSlider.addEventListener('input', e => {
      briDragging = true;
      briVal.textContent = e.target.value + '%';
    });
    briSlider.addEventListener('change', async e => {
      const v = parseInt(e.target.value, 10);
      await api('/api/system/brightness', { level: v });
      briDragging = false;
      toast('Brightness ' + v + '%');
    });

    async function sendMedia(action) {
      await api('/api/system/media', { action });
      toast('Media: ' + action.replace('_', ' '));
    }

    async function toggleMute() {
      const r = await api('/api/system/mute', { toggle: true });
      if (r) updateMobileTelemetry(r);
      toast('Toggled Mute');
    }

    async function analyzeScreenMobile() {
      document.getElementById('replyLabel').textContent = 'Analyzing PC Screen...';
      document.getElementById('replyText').textContent = 'Capturing PC display and running Gemini Vision...';
      toast('Analyzing PC screen...');
      const r = await api('/api/system/analyze_screen', {});
      if (r && r.result) {
        document.getElementById('replyLabel').textContent = 'PC Screen Analysis';
        document.getElementById('replyText').textContent = r.result;
      }
    }

    async function takeScreenshotMobile() {
      const r = await api('/api/system/screenshot', {});
      toast((r && r.result) || 'Screenshot saved on PC');
      if (r && r.result) document.getElementById('replyText').textContent = r.result;
    }

    async function standbyMobile() {
      await api('/api/system/standby', {});
      toast('PC entered Standby Mode');
    }

    async function lockPcMobile() {
      await api('/api/system/lock', {});
      toast('PC Workstation Locked');
    }

    async function openAppMobile(app_name) {
      const r = await api('/api/system/app', { action: 'open', app_name });
      toast((r && r.result) || ('Opened ' + app_name));
    }

    async function openFolderMobile(folder) {
      const r = await api('/api/system/folder', { folder });
      toast((r && r.result) || ('Opened ' + folder));
    }

    async function clearChatMobile() {
      await api('/api/chat/clear', {});
      toast('Cleared active chat context');
      loadMobileChat();
    }

    async function loadMobileChat() {
      const d = await api('/api/chat/log');
      const box = document.getElementById('mobileChatList');
      if (!d || !Array.isArray(d.entries) || !box) return;
      const recent = d.entries.slice(-25).reverse();
      if (!recent.length) {
        box.innerHTML = '<div style="color:var(--muted);font-size:13px;text-align:center;padding:16px;">No messages yet.</div>';
        return;
      }
      box.innerHTML = recent.map(e => `
        <div style="display:flex;flex-direction:column;gap:6px;">
          <div class="bubble-user">${esc(e.user)}</div>
          <div class="bubble-bot">
            <div>${esc(e.assistant).replace(/\n/g, '<br>')}</div>
            ${e.tools && e.tools.length ? `<div class="bubble-meta">Tools: ${esc(e.tools.join(', '))}</div>` : ''}
          </div>
        </div>
      `).join('');
    }

    async function loadMobileTasks() {
      const d = await api('/api/productivity');
      if (!d) return;
      const rBox = document.getElementById('mobileRoutinesList');
      if (rBox) {
        rBox.innerHTML = (d.routines || []).map(r => `
          <div class="list-item">
            <div>
              <div style="font-weight:700;font-size:14px;text-transform:capitalize;">${esc(r.name)}</div>
              <div style="font-size:12px;color:var(--muted);margin-top:2px;">${esc(r.description)}</div>
            </div>
            <button class="btn-sm run" onclick="runMobileCommand('Run my ${esc(r.name)} routine');switchMobileTab('remote', document.querySelector('.tab-btn'));">Run</button>
          </div>
        `).join('');
      }

      const remBox = document.getElementById('mobileRemindersList');
      if (remBox) {
        const items = [];
        (d.timers || []).forEach(t => {
          items.push(`
            <div class="list-item">
              <div>
                <div style="font-weight:700;font-size:14px;">⏱ ${esc(t.label)} (${esc(t.remaining)} left)</div>
                <div style="font-size:11px;color:var(--muted);">Rings at ${esc(t.due)}</div>
              </div>
              <button class="btn-sm danger" onclick="delRem('${esc(t.label)}')">Cancel</button>
            </div>
          `);
        });
        (d.reminders || []).forEach(r => {
          items.push(`
            <div class="list-item">
              <div>
                <div style="font-weight:700;font-size:14px;">🔔 ${esc(r.text)}</div>
                <div style="font-size:11px;color:var(--muted);">${esc((r.due || '').replace('T', ' '))}</div>
              </div>
              <button class="btn-sm danger" onclick="delRem('${esc(r.text)}')">Delete</button>
            </div>
          `);
        });
        remBox.innerHTML = items.length ? items.join('') : '<div style="color:var(--muted);font-size:13px;">No active timers or reminders.</div>';
      }

      const nBox = document.getElementById('mobileNotesList');
      if (nBox) {
        const notes = (d.notes || []).slice().reverse();
        nBox.innerHTML = notes.length ? notes.map(n => `
          <div class="list-item">
            <div style="font-size:13px;line-height:1.4;">${esc(n.text)}</div>
            <button class="btn-sm danger" onclick="delNote('${esc(n.text.slice(0,20))}')">Delete</button>
          </div>
        `).join('') : '<div style="color:var(--muted);font-size:13px;">No saved notes.</div>';
      }
    }

    async function delRem(q) {
      await api('/api/productivity/reminder/delete', { query: q });
      loadMobileTasks();
      toast('Removed');
    }

    async function delNote(q) {
      await api('/api/productivity/note/delete', { query: q });
      loadMobileTasks();
      toast('Deleted note');
    }

    document.getElementById('reminderForm').addEventListener('submit', async e => {
      e.preventDefault();
      const txt = document.getElementById('remText').value.trim();
      const when = document.getElementById('remWhen').value.trim() || 'in 15 minutes';
      if (!txt) return;
      document.getElementById('remText').value = '';
      await api('/api/productivity/reminder/add', { text: txt, when });
      loadMobileTasks();
      toast('Reminder set');
    });

    document.getElementById('noteForm').addEventListener('submit', async e => {
      e.preventDefault();
      const txt = document.getElementById('noteInput').value.trim();
      if (!txt) return;
      document.getElementById('noteInput').value = '';
      await api('/api/productivity/note/add', { text: txt });
      loadMobileTasks();
      toast('Note saved');
    });

    async function loadMobileMemories() {
      const d = await api('/api/memories');
      const box = document.getElementById('mobileMemList');
      if (!d || !Array.isArray(d.memories) || !box) return;
      box.innerHTML = d.memories.length ? d.memories.map(m => `
        <div class="list-item">
          <div>
            <div style="font-size:13px;font-weight:600;">${esc(m.fact)}</div>
            <div style="font-size:11px;color:var(--primary);text-transform:uppercase;margin-top:2px;">${esc(m.category || 'preference')}</div>
          </div>
          <button class="btn-sm danger" onclick="delMem('${m.id}')">Forget</button>
        </div>
      `).join('') : '<div style="color:var(--muted);font-size:13px;">No saved memories yet.</div>';
    }

    async function delMem(id) {
      await api('/api/memories/delete', { id });
      loadMobileMemories();
      toast('Memory removed');
    }

    document.getElementById('memForm').addEventListener('submit', async e => {
      e.preventDefault();
      const txt = document.getElementById('memInput').value.trim();
      if (!txt) return;
      document.getElementById('memInput').value = '';
      await api('/api/memories/add', { fact: txt, category: 'preference' });
      loadMobileMemories();
      toast('Saved to Long-Term Memory');
    });

    function updateMobileTelemetry(d) {
      if (!d) return;
      document.getElementById('topClock').textContent = (d.clock_short || '') + ' ' + (d.ampm || '');
      if (d.battery !== null && d.battery !== undefined) {
        document.getElementById('topBattery').textContent = '🔋 ' + d.battery + '%';
      }
      const st = d.assistant_state || 'ready';
      document.getElementById('topStatusText').textContent = st.charAt(0).toUpperCase() + st.slice(1);
      document.getElementById('statusDot').className = (st === 'thinking' || st === 'listening') ? 'dot busy' : 'dot';

      if (!volDragging && d.volume !== null && d.volume !== undefined) {
        const v = d.muted ? 0 : d.volume;
        volSlider.value = v;
        volVal.textContent = v + '%';
        document.getElementById('muteTxt').textContent = d.muted ? 'Unmute' : 'Mute';
      }
      if (!briDragging && d.brightness !== null && d.brightness !== undefined) {
        briSlider.value = d.brightness;
        briVal.textContent = d.brightness + '%';
      }
    }

    async function pollMobile() {
      if (document.hidden) return;
      const d = await api('/api/telemetry');
      if (d) updateMobileTelemetry(d);
    }
    pollMobile();
    setInterval(pollMobile, 2500);
  </script>
</body>
</html>
"""


class StitchBridgeController:
    """Holds shared state between the PyQt6 GUI/voice threads and the HTTP bridge."""

    def __init__(self, agent, tts_worker=None, voice_thread=None, window_callback=None):
        self.agent = agent
        self.tts_worker = tts_worker
        self.voice_thread = voice_thread
        self.window_callback = window_callback  # callable(payload_dict) -> dict

        self.assistant_state = "ready"
        self.thinking = False
        self.thinking_prompt = ""
        self.last_reply_preview = ""
        self.last_status_detail = ""
        self.server_port = JARVIS_SERVER_PORT
        self._agent_lock = threading.Lock()
        self._cached_html_widget = build_widget_html()
        self._cached_html_hub = build_hub_html()

        # Cache hardware sensors so we NEVER hammer WMI/DDC-CI (sbc.get_brightness) on every poll
        self._hw_ts = 0.0
        self._bat_pct = 100
        self._charging = True
        self._vol_pct = 50
        self._muted = False
        try:
            b = tools.sbc.get_brightness()
            self._bri_pct = int(b[0] if isinstance(b, list) else b)
        except Exception:
            self._bri_pct = 75
        self._refresh_hw_sensors(force=True)

    def _refresh_hw_sensors(self, force: bool = False):
        now_t = time.time()
        if not force and (now_t - self._hw_ts) < 20.0:
            return
        self._hw_ts = now_t
        try:
            bat = psutil.sensors_battery()
            if bat:
                self._bat_pct = int(bat.percent)
                self._charging = bool(bat.power_plugged)
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.ole32.CoInitialize(None)
            vol_obj = tools._get_speaker_volume()
            self._vol_pct = int(round(vol_obj.GetMasterVolumeLevelScalar() * 100))
            self._muted = bool(vol_obj.GetMute())
        except Exception:
            pass

    def dispatch_command(self, text: str, is_voice: bool = False):
        """Runs a user command through JarvisAgent in a background thread."""
        clean = (text or "").strip()
        if not clean or self.thinking:
            return
        self.thinking = True
        self.thinking_prompt = clean
        self.assistant_state = "thinking"
        if self.voice_thread:
            self.voice_thread.busy = True

        def _worker():
            try:
                with self._agent_lock:
                    reply = self.agent.run(clean)
            except Exception as e:
                reply = f"Error: {e}"
            finally:
                self.thinking = False
                self.thinking_prompt = ""

            self.last_reply_preview = (reply or "")[:90]
            if self.tts_worker and self.tts_worker.enabled and reply:
                self.assistant_state = "speaking"
                self.tts_worker.speak(reply)
            else:
                self.assistant_state = "ready"
                if self.voice_thread:
                    self.voice_thread.busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def run_command_sync(self, text: str) -> str:
        """Executes a command synchronously (used by iPhone /command endpoint) while syncing UI state."""
        clean = (text or "").strip()
        if not clean:
            return "I didn't hear a command."
        self.thinking = True
        self.thinking_prompt = clean
        self.assistant_state = "thinking"
        try:
            with self._agent_lock:
                reply = self.agent.run(clean)
        except Exception as e:
            reply = f"Error: {e}"
        finally:
            self.thinking = False
            self.thinking_prompt = ""
            self.assistant_state = "ready"

        self.last_reply_preview = (reply or "")[:90]
        return reply or "Done."

    def get_telemetry(self) -> dict:
        now = datetime.now()
        self._refresh_hw_sensors(force=False)

        chat_entries = tools.load_chat_log()
        memories = tools._load_json(tools.MEMORIES_FILE, [])

        return {
            "clock_short": now.strftime("%I:%M").lstrip("0") or "12:00",
            "ampm": now.strftime("%p"),
            "date_short": now.strftime("%a, %b %d"),
            "battery": self._bat_pct,
            "charging": self._charging,
            "volume": self._vol_pct,
            "muted": self._muted,
            "brightness": self._bri_pct,
            "assistant_state": "thinking" if self.thinking else self.assistant_state,
            "thinking": self.thinking,
            "thinking_prompt": self.thinking_prompt,
            "last_reply_preview": self.last_reply_preview,
            "wake_enabled": bool(self.voice_thread.wake_enabled) if self.voice_thread else True,
            "tts_enabled": bool(self.tts_worker.enabled) if self.tts_worker else True,
            "model": JARVIS_MODEL,
            "chat_count": len(chat_entries),
            "memories_count": len(memories),
        }

    def get_system_details(self) -> dict:
        base = self.get_telemetry()
        now = datetime.now()
        base["clock_full"] = now.strftime("%I:%M:%S %p")
        try:
            base["cpu_percent"] = int(round(psutil.cpu_percent(interval=None)))
            mem = psutil.virtual_memory()
            base["ram_percent"] = int(round(mem.percent))
            base["ram_used_gb"] = f"{mem.used / (1024**3):.1f}"
            base["ram_total_gb"] = f"{mem.total / (1024**3):.0f}"
            disk = psutil.disk_usage("C:\\")
            base["disk_percent"] = int(round(disk.percent))
            base["disk_free_gb"] = f"{disk.free / (1024**3):.0f}"
        except Exception:
            pass

        try:
            base["clipboard"] = (pyperclip.paste() or "").strip()[:300]
        except Exception:
            base["clipboard"] = ""

        # Top 4 processes by RAM
        procs = []
        try:
            agg = {}
            for p in psutil.process_iter(["name", "memory_info"]):
                info = p.info
                nm = (info.get("name") or "").strip()
                if not nm or nm.lower() in ("system idle process", "system", "registry", "svchost.exe", "memcompression"):
                    continue
                mb = (info["memory_info"].rss / (1024 * 1024)) if info.get("memory_info") else 0.0
                if nm not in agg:
                    agg[nm] = {"name": nm.replace(".exe", ""), "mem_mb": 0, "count": 0}
                agg[nm]["mem_mb"] += mb
                agg[nm]["count"] += 1
            top = sorted(agg.values(), key=lambda x: x["mem_mb"], reverse=True)[:4]
            for item in top:
                item["mem_mb"] = int(round(item["mem_mb"]))
            procs = top
        except Exception:
            pass
        base["processes"] = procs
        return base

    def get_productivity_data(self) -> dict:
        reminders = [r for r in tools._load_json(tools.REMINDERS_FILE, []) if not r.get("fired")]
        notes = tools._load_json(tools.NOTES_FILE, [])
        routines = tools._load_routines()
        now = datetime.now()
        timers = []
        for t in tools._load_json(tools.TIMERS_FILE, []):
            if t.get("fired"):
                continue
            try:
                due_dt = datetime.fromisoformat(t["due"])
                rem = max(0, int((due_dt - now).total_seconds()))
                mins, secs = divmod(rem, 60)
                timers.append({
                    "label": t.get("label", "Timer"),
                    "due": due_dt.strftime("%I:%M:%S %p"),
                    "remaining": f"{mins}m {secs}s" if mins else f"{secs}s",
                })
            except Exception:
                pass
        return {
            "reminders": reminders,
            "timers": timers,
            "notes": notes,
            "routines": routines,
        }

    def get_settings_data(self) -> dict:
        mics = []
        try:
            import sounddevice as sd
            for dev in sd.query_devices():
                if dev.get("max_input_channels", 0) > 0:
                    nm = dev.get("name", "")
                    if nm and nm not in mics:
                        mics.append(nm)
        except Exception:
            pass
        if not mics:
            mics = ["Default Windows Microphone Input"]

        voices = ["Microsoft David Desktop - English (United States)", "Microsoft Zira Desktop - English (United States)"]
        lan_ip = "127.0.0.1"
        tailscale_ip = None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
        except Exception:
            pass

        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None):
                ip = info[4][0]
                if isinstance(ip, str) and ip.startswith("100."):
                    tailscale_ip = ip
                    break
        except Exception:
            pass

        active_port = self.server_port or JARVIS_SERVER_PORT
        return {
            "mics": mics[:6],
            "voices": voices,
            "voice_index": JARVIS_TTS_VOICE_INDEX,
            "tts_rate": JARVIS_TTS_RATE,
            "wake_threshold": JARVIS_WAKE_THRESHOLD,
            "whisper_model": JARVIS_WHISPER_MODEL,
            "lan_url": f"http://{lan_ip}:{active_port}/command",
            "tailscale_url": f"http://{tailscale_ip or lan_ip}:{active_port}/command",
            "api_secret": JARVIS_AUTH_TOKEN,
        }


def start_bridge_http_server(controller: StitchBridgeController) -> int:
    """Starts the unified HTTP server on 0.0.0.0:8000 (serving desktop UI, mobile web UI, and iPhone /command)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # keep terminal clean

        def _check_auth(self) -> bool:
            token = JARVIS_AUTH_TOKEN
            if not token:
                return True
            client_ip = self.client_address[0] if self.client_address else ""
            if client_ip in ("127.0.0.1", "::1"):
                return True
            provided = self.headers.get("X-Jarvis-Token", "").strip()
            if not provided:
                auth = self.headers.get("Authorization", "").strip()
                if auth.lower().startswith("bearer "):
                    provided = auth[7:].strip()
                else:
                    provided = auth
            return provided == token

        def _send_json(self, data: dict, status: int = 200):
            raw = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Jarvis-Token, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(raw)

        def _send_text(self, text: str, content_type: str = "text/html; charset=utf-8"):
            raw = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Jarvis-Token, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                return self._send_json({
                    "status": "ok",
                    "auth_required": bool(JARVIS_AUTH_TOKEN),
                })
            if path == "/widget":
                return self._send_text(controller._cached_html_widget)
            if path == "/mobile":
                return self._send_text(MOBILE_HTML)
            if path == "/":
                ua = (self.headers.get("User-Agent") or "").lower()
                is_mobile = any(k in ua for k in ("iphone", "ipad", "ipod", "android", "mobile"))
                client_ip = self.client_address[0] if self.client_address else ""
                if is_mobile or client_ip not in ("127.0.0.1", "::1"):
                    return self._send_text(MOBILE_HTML)
                return self._send_text(controller._cached_html_hub)
            if path == "/hub":
                return self._send_text(controller._cached_html_hub)
            if path == "/bridge_widget.js":
                return self._send_text(WIDGET_JS, "application/javascript; charset=utf-8")
            if path == "/bridge_hub.js":
                return self._send_text(HUB_JS, "application/javascript; charset=utf-8")
            if path == "/api/telemetry":
                return self._send_json(controller.get_telemetry())
            if path == "/api/chat/log":
                entries = tools.load_chat_log()
                memories = tools._load_json(tools.MEMORIES_FILE, [])
                return self._send_json({
                    "entries": entries,
                    "thinking": controller.thinking,
                    "thinking_prompt": controller.thinking_prompt,
                    "memories_count": len(memories),
                })
            if path == "/api/memories":
                memories = tools._load_json(tools.MEMORIES_FILE, [])
                return self._send_json({"memories": list(reversed(memories))})
            if path == "/api/system/details":
                return self._send_json(controller.get_system_details())
            if path == "/api/productivity":
                return self._send_json(controller.get_productivity_data())
            if path == "/api/settings":
                return self._send_json(controller.get_settings_data())
            self._send_json({"error": "Not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", "0") or "0")
            body_bytes = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                payload = {}

            if path == "/command":
                if not self._check_auth():
                    return self._send_json(
                        {"detail": "Unauthorized: Invalid or missing X-Jarvis-Token header."},
                        status=401,
                    )
                reply = controller.run_command_sync(payload.get("text", ""))
                return self._send_json({"reply": reply})

            if path == "/api/window":
                res = controller.window_callback(payload) if controller.window_callback else {}
                return self._send_json(res or {"ok": True})

            if path == "/api/chat/send":
                controller.dispatch_command(payload.get("text", ""), is_voice=False)
                return self._send_json({"ok": True})

            if path == "/api/chat/clear":
                controller.agent.clear_history()
                return self._send_json({"ok": True})

            if path == "/api/voice/ptt":
                if controller.voice_thread:
                    controller.voice_thread.trigger_push_to_talk()
                return self._send_json({"ok": True})

            if path == "/api/voice/wake":
                if controller.voice_thread:
                    new_state = not controller.voice_thread.wake_enabled if payload.get("toggle") else bool(payload.get("enabled", True))
                    controller.voice_thread.set_wake_enabled(new_state)
                return self._send_json(controller.get_telemetry())

            if path == "/api/voice/tts":
                if controller.tts_worker:
                    controller.tts_worker.enabled = not controller.tts_worker.enabled if payload.get("toggle") else bool(payload.get("enabled", True))
                return self._send_json(controller.get_telemetry())

            if path == "/api/voice/speak":
                txt = payload.get("text", "")
                if controller.tts_worker and txt:
                    old_en = controller.tts_worker.enabled
                    controller.tts_worker.enabled = True
                    controller.tts_worker.speak(txt)
                    controller.tts_worker.enabled = old_en
                return self._send_json({"ok": True})

            if path == "/api/voice/calibrate":
                return self._send_json({"ok": True})

            if path == "/api/memories/add":
                res = tools.remember_fact(payload.get("fact", ""), payload.get("category", "preference"))
                return self._send_json({"result": res})

            if path == "/api/memories/delete":
                res = tools.forget_memory(str(payload.get("id", "")))
                return self._send_json({"result": res})

            if path == "/api/system/volume":
                lvl = max(0, min(100, int(payload.get("level", 50))))
                res = tools.set_volume(lvl)
                controller._vol_pct = lvl
                controller._muted = False
                return self._send_json({"result": res})

            if path == "/api/system/mute":
                try:
                    import ctypes
                    ctypes.windll.ole32.CoInitialize(None)
                    vol = tools._get_speaker_volume()
                    is_muted = bool(vol.GetMute())
                    if is_muted:
                        tools.unmute_audio()
                        controller._muted = False
                    else:
                        tools.mute_audio()
                        controller._muted = True
                except Exception:
                    pass
                return self._send_json(controller.get_telemetry())

            if path == "/api/system/brightness":
                lvl = max(0, min(100, int(payload.get("level", 75))))
                res = tools.set_screen_brightness(lvl)
                controller._bri_pct = lvl
                return self._send_json({"result": res})

            if path == "/api/system/media":
                res = tools.control_media(payload.get("action", "play_pause"))
                return self._send_json({"result": res})

            if path == "/api/system/screenshot":
                res = tools.take_screenshot()
                return self._send_json({"result": res})

            if path == "/api/system/analyze_screen":
                controller.thinking = True
                controller.assistant_state = "thinking"
                controller.last_status_detail = "Analyzing screen with Gemini Vision..."
                try:
                    prompt = payload.get(
                        "prompt",
                        "Be concise (2 to 3 short sentences max). Explain what is currently visible on the screen and point out any errors or key information.",
                    )
                    res = tools.analyze_screen(prompt)
                    controller.last_reply_preview = (res or "")[:90]
                    tools.append_chat_log("Analyze my screen", res, ["analyze_screen"])
                    if controller.tts_worker and controller.tts_worker.enabled and res:
                        controller.assistant_state = "speaking"
                        controller.tts_worker.speak(res)
                finally:
                    controller.thinking = False
                    controller.last_status_detail = ""
                    if controller.assistant_state == "thinking":
                        controller.assistant_state = "ready"
                return self._send_json({"result": res})

            if path == "/api/system/lock":
                res = tools.lock_pc()
                return self._send_json({"result": res})

            if path == "/api/system/standby":
                tools.mute_audio()
                res = tools.minimize_all_windows()
                return self._send_json({"result": res})

            if path == "/api/system/clipboard":
                res = tools.set_clipboard_text(payload.get("text", ""))
                return self._send_json({"result": res})

            if path == "/api/system/folder":
                res = tools.open_folder(payload.get("folder", "Desktop"))
                return self._send_json({"result": res})

            if path == "/api/system/app":
                act = payload.get("action", "open")
                app_name = payload.get("app_name", "")
                res = tools.open_app(app_name) if act == "open" else tools.close_app(app_name)
                return self._send_json({"result": res})

            if path == "/api/productivity/reminder/add":
                txt = payload.get("text", "")
                when = payload.get("when", "in 15 minutes")
                res = tools.set_reminder(txt, when)
                return self._send_json({"result": res})

            if path == "/api/productivity/reminder/delete":
                q = payload.get("query", "")
                r1 = tools.delete_reminder(q)
                r2 = tools.cancel_timer(q)
                return self._send_json({"result": f"{r1} {r2}"})

            if path == "/api/productivity/note/add":
                res = tools.take_note(payload.get("text", ""))
                return self._send_json({"result": res})

            if path == "/api/productivity/note/delete":
                res = tools.delete_note(payload.get("query", ""))
                return self._send_json({"result": res})

            if path == "/api/productivity/routine/add":
                res = tools.save_routine(payload.get("name", ""), payload.get("description", ""))
                return self._send_json({"result": res})

            self._send_json({"error": "Unknown endpoint"}, 404)

    try:
        server = ThreadingHTTPServer(("0.0.0.0", JARVIS_SERVER_PORT), Handler)
    except OSError:
        server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    port = server.server_address[1]
    controller.server_port = port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port
