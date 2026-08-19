"""
The Jarvis "brain": sends the user's command to Gemini along with the
available tools, executes whatever tool(s) it decides to call, and
returns a final natural-language response.

Uses the OpenAI SDK pointed at Google's Gemini endpoint, since the Gemini
API is OpenAI-compatible.
"""
import json

from openai import OpenAI

from config import DATA_DIR, GOOGLE_API_KEY, GOOGLE_BASE_URL, JARVIS_MODEL
from tools import TOOL_DEFINITIONS, TOOL_FUNCTIONS

client = OpenAI(api_key=GOOGLE_API_KEY, base_url=GOOGLE_BASE_URL)

SYSTEM_PROMPT = (
    "You are Jarvis, a concise personal assistant with access to tools that "
    "control the user's Windows PC. Use tools when the user's request maps "
    "to one. If nothing fits, just answer directly. Keep replies short — "
    "they may be spoken aloud."
)

HISTORY_FILE = DATA_DIR / "history.json"
MAX_HISTORY_TURNS = 20  # Keep last 20 messages to prevent unbounded token growth

# Convert our tool schemas (name, description, input_schema) into the
# OpenAI-style "function" tool format Gemini expects.
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TOOL_DEFINITIONS
]


class JarvisAgent:
    def __init__(self):
        # Load persistent conversation history so context survives restarts
        self.history = self._load_history()

    @staticmethod
    def _clean_messages(messages):
        """Strip fields the Gemini API injects but refuses to accept back."""
        cleaned = []
        for m in messages:
            m = dict(m)  # shallow copy
            m.pop("extra_content", None)
            # Also clean nested tool_calls entries
            if "tool_calls" in m and isinstance(m["tool_calls"], list):
                m["tool_calls"] = [
                    {k: v for k, v in tc.items() if k != "extra_content"}
                    for tc in m["tool_calls"]
                ]
            cleaned.append(m)
        return cleaned

    @staticmethod
    def _trim_to_user_start(messages):
        """Trim so the first message has role 'user' (never a dangling tool/assistant)."""
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                return messages[i:]
        return []  # nothing salvageable

    def _load_history(self):
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    filtered = [m for m in data if m.get("role") != "system"]
                    recent = filtered[-MAX_HISTORY_TURNS:]
                    recent = self._trim_to_user_start(recent)
                    recent = self._clean_messages(recent)
                    if recent:
                        return [{"role": "system", "content": SYSTEM_PROMPT}] + recent
            except Exception:
                pass
        return [{"role": "system", "content": SYSTEM_PROMPT}]

    def _save_history(self):
        try:
            # Keep system prompt + last N turns, cleaned for reload safety
            system_msg = [{"role": "system", "content": SYSTEM_PROMPT}]
            non_system = [m for m in self.history if m.get("role") != "system"][-MAX_HISTORY_TURNS:]
            non_system = self._clean_messages(non_system)
            to_save = system_msg + non_system
            HISTORY_FILE.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
        except Exception:
            pass

    def clear_history(self) -> str:
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._save_history()
        return "Conversation memory cleared."

    def run(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})

        while True:
            try:
                response = client.chat.completions.create(
                    model=JARVIS_MODEL,
                    messages=self.history,
                    tools=OPENAI_TOOLS,
                )
            except Exception as e:
                # Remove un-answered user turn to keep history valid
                if self.history and self.history[-1].get("role") == "user" and self.history[-1].get("content") == user_text:
                    self.history.pop()
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    return "I'm receiving too many requests right now. Please wait a few seconds and try again."
                return f"Sorry, I encountered an error communicating with the AI service: {err_msg}"

            message = response.choices[0].message

            # Append the assistant turn (as a plain dict, matching what
            # the API expects back in messages on the next call).
            self.history.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                reply = (message.content or "").strip()
                self._save_history()
                return reply

            # Execute every tool call Gemini asked for, then loop again so
            # it can see the results and decide what's next.
            for tc in message.tool_calls:
                fn = TOOL_FUNCTIONS.get(tc.function.name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if fn is None:
                    result = f"Unknown tool: {tc.function.name}"
                else:
                    try:
                        result = fn(**args)
                    except Exception as e:
                        result = f"Tool {tc.function.name} failed: {e}"

                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": str(result),
                })
