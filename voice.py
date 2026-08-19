"""
Desktop Voice Interface for Jarvis (Phase 3).

Listens locally for the wake word ('Hey Jarvis') or keyboard trigger (Enter/Space),
records your speech, transcribes it with faster-whisper, queries the JarvisAgent,
and speaks the response aloud with pyttsx3.

Run:
    python voice.py
"""
import collections
import queue
import sys
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
import openwakeword
from openwakeword.model import Model
import pyttsx3

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import winsound
except ImportError:
    winsound = None

from agent import JarvisAgent
from config import (
    JARVIS_TTS_RATE,
    JARVIS_TTS_VOICE_INDEX,
    JARVIS_WAKE_THRESHOLD,
    JARVIS_WAKE_WORD,
    JARVIS_WHISPER_MODEL,
)
import scheduler


# Audio stream configuration
SAMPLE_RATE = 16000  # 16kHz required by openWakeWord and Whisper
CHUNK_SAMPLES = 1280  # 80ms chunk at 16kHz (openWakeWord standard)
SILENCE_TIMEOUT_SECONDS = 1.3  # Silence duration to conclude user command
MAX_RECORDING_SECONDS = 12.0  # Max duration for a single command
MIN_RECORDING_SECONDS = 0.4  # Ignore accidental triggers under 0.4s
PRE_ROLL_CHUNKS = 5  # ~400ms pre-roll to prevent cutting off the first word

# Domain prompt to condition Whisper's language model for assistant queries
WHISPER_INITIAL_PROMPT = (
    "Hey Jarvis. Open Notepad, Calculator, Spotify, Chrome, Edge, VS Code, "
    "close app, what time is it, set a reminder, take a note, what are my notes, "
    "fetch webpage text, check reminders."
)


def play_chime(kind: str = "activate"):
    """Plays audio cue for state changes (Windows winsound)."""
    if winsound is None:
        return
    try:
        if kind == "activate":
            # Rising tone on wake
            winsound.Beep(900, 100)
            winsound.Beep(1300, 120)
        elif kind == "done":
            # Soft tone on speech capture complete
            winsound.Beep(750, 90)
        elif kind == "error":
            winsound.Beep(400, 150)
    except Exception:
        pass


class VoiceEngine:
    """Manages Text-to-Speech via pyttsx3."""

    def __init__(self):
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", JARVIS_TTS_RATE)
        voices = self.engine.getProperty("voices")
        if voices and 0 <= JARVIS_TTS_VOICE_INDEX < len(voices):
            self.engine.setProperty("voice", voices[JARVIS_TTS_VOICE_INDEX].id)

    def speak(self, text: str):
        if not text or not text.strip():
            return
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as e:
            print(f"\n[TTS Error] {e}")


class VoiceAssistant:
    def __init__(self):
        print("Initializing Jarvis Voice Assistant...")
        self.agent = JarvisAgent()
        self.tts = VoiceEngine()

        print(f"Loading faster-whisper model ('{JARVIS_WHISPER_MODEL}')...")
        self.whisper_model = WhisperModel(
            JARVIS_WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
        )

        print(f"Loading openWakeWord model ('{JARVIS_WAKE_WORD}')...")
        try:
            openwakeword.utils.download_models()
        except Exception:
            pass
        self.oww_model = Model(
            wakeword_models=[JARVIS_WAKE_WORD],
            inference_framework="onnx",
        )

        self.audio_queue = queue.Queue()
        self.pre_roll_buffer = collections.deque(maxlen=PRE_ROLL_CHUNKS)
        self.speech_energy_threshold = 350.0  # Will be calibrated at startup
        print("Voice models ready.\n")

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback from sounddevice InputStream."""
        self.audio_queue.put(indata.copy())

    def calibrate_noise(self, duration: float = 1.0):
        """Measures ambient room noise to establish dynamic speech threshold."""
        print("Calibrating microphone ambient noise level... (please stay quiet)")
        samples = []
        start = time.time()
        while time.time() - start < duration:
            try:
                chunk = self.audio_queue.get(timeout=0.2)
                rms = np.sqrt(np.mean(chunk.flatten().astype(np.float32) ** 2))
                samples.append(rms)
            except queue.Empty:
                continue

        if samples:
            ambient_rms = float(np.median(samples))
            # Set speech threshold to 2.0x ambient noise, minimum 200
            self.speech_energy_threshold = max(200.0, ambient_rms * 2.0)
            print(f"Ambient noise RMS: {ambient_rms:.1f} | Speech threshold: {self.speech_energy_threshold:.1f}\n")
        else:
            self.speech_energy_threshold = 350.0

    def _transcribe(self, audio_data: np.ndarray) -> str:
        """Transcribes 16kHz float32 audio using faster-whisper with initial prompt conditioning."""
        # Audio normalization: ensure peak amplitude is normalized to 0.95
        peak = np.max(np.abs(audio_data))
        if peak > 1e-4:
            audio_data = (audio_data / peak) * 0.95

        segments, _ = self.whisper_model.transcribe(
            audio_data,
            language="en",
            beam_size=5,
            initial_prompt=WHISPER_INITIAL_PROMPT,
            vad_filter=False,  # Already filtered by our recording loop
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text

    def run(self):
        """Main listening and voice conversation loop."""
        scheduler.start()

        # Query active input device name
        try:
            device_info = sd.query_devices(sd.default.device[0], "input")
            device_name = device_info.get("name", "Default")
        except Exception:
            device_name = "Default"

        print("=" * 65)
        print(" JARVIS VOICE ASSISTANT ACTIVE")
        print("=" * 65)
        print(f" • Input Device : {device_name}")
        print(f" • STT Model    : {JARVIS_WHISPER_MODEL}")
        print(f" • Wake Word    : 'Hey Jarvis' (threshold: {JARVIS_WAKE_THRESHOLD})")
        print(f" • Quick Trigger: Press [Enter] or [Space] anytime to speak")
        print(f" • Exit         : Press Ctrl+C\n")

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            callback=self._audio_callback,
        ):
            # Calibrate ambient noise
            self.calibrate_noise(1.0)

            last_meter_time = 0
            while True:
                try:
                    # Check for keyboard trigger (Push-to-Talk)
                    manual_trigger = False
                    if msvcrt and msvcrt.kbhit():
                        key = msvcrt.getch()
                        if key in (b"\r", b"\n", b" "):  # Enter or Space
                            manual_trigger = True

                    # Drain queue chunk
                    try:
                        chunk = self.audio_queue.get(timeout=0.05)
                    except queue.Empty:
                        chunk = None

                    if chunk is not None:
                        chunk_flat = chunk.flatten()
                        self.pre_roll_buffer.append(chunk_flat)
                        rms = float(np.sqrt(np.mean(chunk_flat.astype(np.float32) ** 2)))

                        # Predict wake word
                        pred = self.oww_model.predict(chunk_flat)
                        score = 0.0
                        for k, v in pred.items():
                            if JARVIS_WAKE_WORD in k:
                                score = float(v)
                                break

                        # Live meter update every 120ms
                        now = time.time()
                        if now - last_meter_time > 0.12:
                            bars = "■" * min(int(rms / 80), 10)
                            sys.stdout.write(
                                f"\r[🎤 Listening | RMS: {rms:4.0f} {bars:<10} | Wake score: {score:.2f} | Press Enter to speak]   "
                            )
                            sys.stdout.flush()
                            last_meter_time = now

                        # Trigger condition: wake word or keypress
                        if score >= JARVIS_WAKE_THRESHOLD or manual_trigger:
                            sys.stdout.write("\r" + " " * 80 + "\r")
                            trigger_reason = "Wake Word ('Hey Jarvis')" if score >= JARVIS_WAKE_THRESHOLD else "Manual Trigger [Key]"
                            print(f"\n[{trigger_reason} Detected!]")
                            play_chime("activate")
                            self.oww_model.reset()
                            self._handle_voice_command()
                            self.oww_model.reset()
                            self.pre_roll_buffer.clear()
                            print("\nListening for 'Hey Jarvis' or press Enter...")
                            last_meter_time = 0

                    elif manual_trigger:
                        sys.stdout.write("\r" + " " * 80 + "\r")
                        print("\n[Manual Trigger [Key] Detected!]")
                        play_chime("activate")
                        self.oww_model.reset()
                        self._handle_voice_command()
                        self.oww_model.reset()
                        self.pre_roll_buffer.clear()
                        print("\nListening for 'Hey Jarvis' or press Enter...")
                        last_meter_time = 0

                except KeyboardInterrupt:
                    print("\n\nExiting voice mode.")
                    break
                except Exception as e:
                    print(f"\n[Error in loop] {e}")
                    time.sleep(0.1)

    def _handle_voice_command(self):
        """Records command until silence, transcribes, queries agent, and speaks reply."""
        print("Jarvis: Listening for your command...")

        # Initialize recorded buffer with pre-roll to avoid clipping the start of speech
        recorded_chunks = list(self.pre_roll_buffer)
        speech_started = False
        silence_start_time = None
        start_time = time.time()

        # Check if pre-roll already has speech
        for c in recorded_chunks:
            if np.sqrt(np.mean(c.astype(np.float32) ** 2)) >= self.speech_energy_threshold:
                speech_started = True
                break

        while True:
            try:
                chunk = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                chunk = None

            if chunk is None:
                continue

            chunk_flat = chunk.flatten()
            recorded_chunks.append(chunk_flat)

            rms = float(np.sqrt(np.mean(chunk_flat.astype(np.float32) ** 2)))
            elapsed = time.time() - start_time

            if rms >= self.speech_energy_threshold:
                if not speech_started:
                    speech_started = True
                    sys.stdout.write(" [Speech detected...]\r")
                    sys.stdout.flush()
                silence_start_time = None  # Reset silence timer
            else:
                if speech_started:
                    if silence_start_time is None:
                        silence_start_time = time.time()
                    elif time.time() - silence_start_time >= SILENCE_TIMEOUT_SECONDS:
                        # User finished speaking
                        break

            # Timeout if no speech started after 4.5 seconds
            if not speech_started and elapsed >= 4.5:
                print("\n[No speech detected - resuming listening]")
                return

            # Safety duration cap
            if elapsed >= MAX_RECORDING_SECONDS:
                break

        play_chime("done")
        sys.stdout.write("\r" + " " * 40 + "\r")
        print("Jarvis: Transcribing speech...")

        if not recorded_chunks:
            return

        full_audio_int16 = np.concatenate(recorded_chunks)
        duration = len(full_audio_int16) / SAMPLE_RATE

        if duration < MIN_RECORDING_SECONDS:
            return

        # Normalize to float32 [-1.0, 1.0] for whisper
        audio_float32 = full_audio_int16.astype(np.float32) / 32768.0

        user_text = self._transcribe(audio_float32)
        if not user_text or len(user_text.strip()) == 0:
            print("[Could not recognize speech]")
            return

        # Clean leading wake word if present in transcribed command
        cleaned_text = user_text
        for prefix in ("hey jarvis,", "hey jarvis", "jarvis,", "jarvis"):
            if cleaned_text.lower().startswith(prefix):
                cleaned_text = cleaned_text[len(prefix):].strip(" ,:.-")
                break

        if not cleaned_text:
            cleaned_text = user_text

        print(f"\nYou: {cleaned_text}")

        # Send to Jarvis Agent loop
        reply = self.agent.run(cleaned_text)
        print(f"Jarvis: {reply}\n")

        # Speak reply
        self.tts.speak(reply)


def main():
    try:
        assistant = VoiceAssistant()
        assistant.run()
    except KeyboardInterrupt:
        print("\nShutdown.")
    except Exception as e:
        print(f"\nFatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
