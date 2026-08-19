# Jarvis — Personal AI Assistant

## What this project is

A JARVIS-inspired personal assistant. The user speaks or types a command,
an LLM decides which "tool" (Python function) it maps to, the tool runs
on the user's actual devices, and the result comes back as a reply.

Two devices are in scope:
- **Windows PC** (primary, fully controllable)
- **iPhone** (secondary — iOS does not allow arbitrary background
  automation, so phone integration goes through Apple Shortcuts + Siri,
  not direct app control)

## Current status: Phase 5 complete

Phase 1 (core loop), Phase 2 (real tools), Phase 3 (voice on desktop),
Phase 4 (iPhone bridge via Apple Shortcuts), and Phase 5 (Windows power
controls & persistent memory) are done and working.

### Tech stack
- Python 3.12, Windows 11
- LLM: Google Gemini via the OpenAI-compatible endpoint
  (`https://generativelanguage.googleapis.com/v1beta/openai/`), called
  through the standard `openai` Python SDK — **not** the `google-genai`
  SDK. Model is set via `JARVIS_MODEL` env var, default
  `gemini-flash-latest`.
- Voice: `openwakeword` for local "Hey Jarvis" wake word detection,
  `faster-whisper` (`small.en`) for local CPU speech-to-text (int8),
  `pyttsx3` for offline SAPI5 text-to-speech, `sounddevice` for 16kHz audio.
- Windows Controls: `pycaw` (audio volume), `screen-brightness-control` (display),
  `psutil` (battery/system), `ctypes` (media keys, lock screen, GDI screenshot),
  `Pillow` (image formatting).
- FastAPI + uvicorn for the HTTP server (`server.py`)
- `dateparser` for natural-language time parsing (reminders)
- `plyer` for Windows desktop notifications
- `requests` + `beautifulsoup4` for reading webpages

### File structure and what each file owns
```
config.py      # env var loading (.env), constants (API key, model, voice settings, DATA_DIR)
tools.py       # every tool: function + OpenAI-style schema. THE extension point.
agent.py       # the tool-calling loop with persistent memory (data/history.json)
scheduler.py   # background reminder thread with overdue catch-up on boot
cli.py         # terminal REPL for manual testing — starts scheduler, loops on input()
voice.py       # hands-free voice interface: wake word + STT + agent.run + TTS
server.py      # FastAPI wrapper around the same agent, POST /command {text}
data/          # gitignored runtime data: notes.json, reminders.json, history.json
.env           # secrets, NOT committed — GOOGLE_API_KEY, JARVIS_MODEL
```

### The tool extension pattern (follow this for every new capability)
1. Write the function in `tools.py`. Return a short string (it may be
   spoken aloud by future TTS — keep replies concise).
2. Add its schema to `TOOL_DEFINITIONS` (name, description, JSON-schema
   `input_schema`).
3. Add it to the `TOOL_FUNCTIONS` dict.
No changes are needed anywhere else — `agent.py` auto-converts
`TOOL_DEFINITIONS` into the OpenAI tool-calling format and dispatches by
name via `TOOL_FUNCTIONS`.

### Existing tools (don't duplicate, extend if similar)
`open_app`, `close_app`, `set_volume`, `get_volume`, `mute_audio`, `unmute_audio`,
`control_media`, `get_battery_status`, `lock_pc`, `take_screenshot`,
`set_screen_brightness`, `get_screen_brightness`, `open_folder`, `open_url`,
`fetch_webpage_text`, `take_note`, `list_notes`, `set_reminder`, `list_reminders`,
`get_current_time`, `clear_memory`.

## Ground rules for the agent working on this repo

- **Windows-only assumptions are fine** for now (desktop side) — the user
  is on Windows 11. Don't add cross-platform abstraction layers unless
  asked; it adds complexity the user doesn't need yet.
- **Keep the tool pattern intact.** Every new capability should be a tool
  function + schema + registry entry, not a special-cased code path.
- **Prefer editing `tools.py` over `agent.py`.** `agent.py` is the stable
  orchestration core; it shouldn't need to change for new features
  (voice and the iOS bridge are the exceptions — see phases below).
- **No cloud services beyond the LLM API** unless the user explicitly
  asks. This is a local-first personal tool — don't introduce hosted
  databases, remote queues, etc.
- **Ask before anything destructive or irreversible**: don't add tools
  that delete files, uninstall software, modify system settings, or send
  messages/emails on the user's behalf without an explicit confirmation
  step designed into the tool itself.
- **There is no test suite yet.** Verify changes by running `python
  cli.py` and exercising the relevant command manually. If you add
  non-trivial logic (e.g. time parsing edge cases, scheduler logic),
  write it in a way that's easy to sanity-check via the REPL, and mention
  in your summary what you manually tested.
- **Keep replies from tools short.** They feed back into the LLM and may
  eventually be spoken aloud — a tool returning a wall of text degrades
  both.

## Roadmap — build in this order

### Phase 3 — Voice on desktop
Goal: talk to Jarvis instead of typing, on the Windows PC.
- Wake word detection (e.g. Porcupine, or a simpler always-listening
  approach if latency is acceptable) so Jarvis isn't triggered by every
  sound.
- Speech-to-text: `faster-whisper` running locally (no cloud STT — keep
  voice local for latency and privacy).
- Text-to-speech for replies: local option preferred (e.g. Piper); cloud
  TTS (ElevenLabs) as a stretch/optional upgrade for better voice quality.
- New file suggestion: `voice.py` — wake word + STT + TTS, calling into
  the existing `JarvisAgent.run()` exactly like `cli.py` does. Don't
  duplicate the agent loop.
- Should run as its own entry point (`python voice.py`) alongside/instead
  of `cli.py`, sharing `agent.py`, `tools.py`, `scheduler.py` unchanged.

### Phase 4 — iPhone bridge via Apple Shortcuts
Goal: "Hey Siri, Jarvis" from the phone reaches the same backend.
- The Windows server (`server.py`) needs to be reachable from the phone.
  For a home setup, recommend Tailscale (simplest, no port forwarding,
  no exposing the PC to the open internet) over ngrok or manual port
  forwarding.
- Build an Apple Shortcut: Siri trigger → dictate text → HTTP POST to
  `http://<tailscale-ip>:8000/command` with `{"text": "..."}` → speak the
  `reply` field back via the Shortcut's "Speak Text" action.
- `server.py` likely needs minimal changes — mainly CORS/host binding
  (`--host 0.0.0.0`) and maybe basic auth (a shared secret header) since
  it'll be reachable from another device, even on a private Tailscale
  network. Flag this to the user as a security consideration, don't
  silently skip auth.
- Document the Shortcut setup steps in the README since they're done in
  the Shortcuts app UI, not code.

### Phase 5 — Persistence and polish
- Reminders currently only fire while the process is running. Make them
  survive restarts — options to evaluate: Windows Task Scheduler
  integration, or running Jarvis as a proper Windows background
  service/startup task.
- Conversation memory currently resets on restart (`JarvisAgent.history`
  is in-memory only). Consider persisting recent history to `data/` and
  reloading on startup, with a reasonable cap so context doesn't grow
  unbounded.
- General hardening: better error messages when the API key is
  missing/invalid (currently a raw `RuntimeError` traceback — could be a
  cleaner startup check), logging, and graceful handling of network
  failures (Gemini API down, no internet) so Jarvis degrades instead of
  crashing.

## Known limitations to keep in mind (don't "fix" without asking)
- `open_app`/`close_app` only know the apps listed in `APP_MAP` in
  `tools.py`, plus a best-effort fallback for anything else. This is
  intentional — expanding `APP_MAP` is a fine small task, but don't
  replace it with something heavier (e.g. full window-management
  libraries) without checking with the user first.
- `fetch_webpage_text` does a plain HTTP GET — pages requiring JavaScript
  to render won't work. Adding a headless-browser fallback (Playwright)
  is a reasonable future tool, but it's a new dependency — flag it before
  adding.
