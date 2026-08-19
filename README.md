# Jarvis — Personal AI Assistant (Phase 5 Complete)

A Jarvis personal assistant with a tool-calling LLM brain (Google Gemini), native Windows 11 hardware/system control, persistent conversation memory across restarts, desktop voice control (Wake word + Whisper + TTS), and mobile iOS integration via Apple Shortcuts.

## Setup

1. Install Python 3.10+ on Windows 11.
2. Open a terminal in this folder and install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Create your `.env` file with your Google API key:
   ```env
   GOOGLE_API_KEY=your_gemini_api_key_here
   JARVIS_MODEL=gemini-flash-latest

   # Optional security token for iPhone bridge
   JARVIS_AUTH_TOKEN=my_secret_token_123
   ```

---

## Windows Control Capabilities (Phase 5)

Jarvis can directly control Windows hardware and system settings via voice or text:

- **Clipboard AI**:
  - *"Summarize what I just copied"* / *"What's on my clipboard?"*
  - *"Copy this text to clipboard"*
- **Live Web Search**:
  - *"Search the web for Python 3.14 release date"*
  - *"Search for who won the football match today"*
- **Screen Vision (AI Multimodal)**:
  - *"Explain the error on my screen"*
  - *"What is visible on my screen right now?"*
  - *"Summarize this code on my display"*
- **Audio & Volume**:
  - *"Set volume to 50%"* / *"Turn up the volume"*
  - *"What is the volume?"*
  - *"Mute audio"* / *"Unmute"*
- **Media Controls**:
  - *"Pause music"* / *"Play"*
  - *"Next song"* / *"Previous track"*
- **Battery & System Status**:
  - *"What is my battery level?"*
  - *"Lock my PC"*
- **Display & Screenshots**:
  - *"Take a screenshot"* (saved automatically to `Pictures/`)
  - *"Set brightness to 80%"* / *"What is my screen brightness?"*
- **Quick Folder Navigation**:
  - *"Open my Downloads folder"*
  - *"Open Documents"* / *"Open Desktop"*
- **Persistent Conversation Memory**:
  - Remembers past context, names, and preferences across restarts (`data/history.json`).
  - *"Clear memory"* (wipes conversation history on demand).

---

## Usage Modes

### 1. Hands-Free Voice Mode (Desktop PC)
```powershell
python voice.py
```
- Say **"Hey Jarvis"** (or press **`[Enter]`** for Push-to-Talk).
- Speak naturally (e.g., *"Open Spotify"*, *"Set volume to 40%"*, *"What's my battery percentage?"*).
- Jarvis executes the action and speaks the response back using native Windows voices.

### 2. Text Mode (CLI REPL)
```powershell
python cli.py
```
Quick keyboard testing loop without audio.

### 3. iPhone Bridge (Siri Integration)
Start the Jarvis bridge server on your PC:
```powershell
python server.py
```
*(On startup, the server automatically prints your Local Wi-Fi and Tailscale IP addresses)*

#### Setting Up the Apple Shortcut on your iPhone:
1. Open the **Shortcuts** app on your iPhone and tap **+** (New Shortcut).
2. Rename the shortcut to **`Jarvis`** (so saying *"Hey Siri, Jarvis"* triggers it).
3. Add the following 4 actions in order:
   - **Dictate Text** (*Stop Listening: After Pause*)
   - **Get Contents of URL**:
     - URL: `http://<YOUR-PC-IP>:8000/command`
     - Method: `POST`
     - Headers: `Content-Type: application/json` (and `X-Jarvis-Token: <token>` if auth enabled)
     - Request Body: `JSON` -> Key: `text` -> Value: `Dictated Text`
   - **Get Value from Dictionary**: Key: `reply`
   - **Speak Text**: `Dictionary Value`
4. Tap **Done**.

---

## File Architecture

- `tools.py` — Windows system tools (volume, media, battery, lock, screenshot, brightness, folders, apps, notes, reminders, web).
- `agent.py` — tool-calling loop communicating with Gemini with persistent memory (`data/history.json`).
- `scheduler.py` — background reminder thread polling `data/reminders.json`, with missed reminder catch-up on boot.
- `voice.py` — 16kHz audio capture with `sounddevice`, local wake word detection with `openwakeword`, STT with `faster-whisper` (`small.en`), and TTS with `pyttsx3`.
- `server.py` — FastAPI HTTP server (POST `/command`, GET `/health`, CORS, and token authentication) for the iPhone Shortcut.
- `config.py` — environment settings and constant loading.
