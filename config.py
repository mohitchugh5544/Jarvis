"""Loads configuration from environment variables / .env file."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
JARVIS_MODEL = os.getenv("JARVIS_MODEL", "gemini-flash-lite-latest")
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Voice configuration (Phase 3)
JARVIS_WHISPER_MODEL = os.getenv("JARVIS_WHISPER_MODEL", "small.en")
JARVIS_WAKE_WORD = os.getenv("JARVIS_WAKE_WORD", "hey_jarvis")
JARVIS_WAKE_THRESHOLD = float(os.getenv("JARVIS_WAKE_THRESHOLD", "0.25"))
JARVIS_TTS_RATE = int(os.getenv("JARVIS_TTS_RATE", "180"))
JARVIS_TTS_VOICE_INDEX = int(os.getenv("JARVIS_TTS_VOICE_INDEX", "0"))

# Server & Mobile Bridge configuration (Phase 4)
JARVIS_SERVER_HOST = os.getenv("JARVIS_SERVER_HOST", "0.0.0.0")
JARVIS_SERVER_PORT = int(os.getenv("JARVIS_SERVER_PORT", "8000"))
JARVIS_AUTH_TOKEN = os.getenv("JARVIS_AUTH_TOKEN", "")

if not GOOGLE_API_KEY:
    raise RuntimeError(
        "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key "
        "from https://aistudio.google.com/apikey"
    )
