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
from tools import (
    TOOL_DEFINITIONS,
    TOOL_FUNCTIONS,
    append_chat_log,
    get_memories_prompt_block,
    get_routines_prompt_block,
    load_chat_log,
)

client = OpenAI(api_key=GOOGLE_API_KEY, base_url=GOOGLE_BASE_URL)

SYSTEM_PROMPT = (
    "You are Jarvis, a concise personal assistant with access to tools that "
    "control the user's Windows PC. Use tools when the user's request maps "
    "to one. If nothing fits, just answer directly. Keep replies short — "
    "they may be spoken aloud.\n\n"
    "LONG-TERM MEMORY & LEARNING RULES:\n"
    "- Whenever the user shares a personal fact (name, work, projects), a preference "
    "(preferred browser, apps, streaming platforms, habits), or corrects you "
    "(e.g. 'Bigg Boss is on JioHotstar', 'I use Opera'), ALWAYS call `remember_fact` "
    "to permanently learn it alongside fulfilling their request.\n"
    "- Always respect the LEARNED LONG-TERM MEMORIES listed below when choosing tools "
    "or arguments (for example, using the user's preferred browser or streaming site).\n"
    "- If the user asks to run a saved routine (e.g. 'start coding mode', 'run focus mode', "
    "'morning briefing'), execute all the tools required by that routine's description.\n"
    "- If the user asks about older conversations not in the immediate context, "
    "call `search_chat_history`."
)

HISTORY_FILE = DATA_DIR / "history.json"
MAX_HISTORY_TURNS = 20  # Keep last 20 messages to prevent unbounded token growth

# Tools whose return string is already a complete, concise user-facing reply.
# When a turn ONLY invokes tools from this set, we return the tool output(s)
# directly and skip the 2nd LLM network round-trip (cutting latency in half).
DIRECT_RETURN_TOOLS = {
    "open_app",
    "close_app",
    "set_volume",
    "get_volume",
    "mute_audio",
    "unmute_audio",
    "control_media",
    "get_battery_status",
    "lock_pc",
    "take_screenshot",
    "set_screen_brightness",
    "get_screen_brightness",
    "open_folder",
    "open_url",
    "take_note",
    "delete_note",
    "set_reminder",
    "delete_reminder",
    "set_timer",
    "cancel_timer",
    "minimize_all_windows",
    "save_routine",
    "delete_routine",
    "get_current_time",
    "remember_fact",
    "forget_memory",
    "clear_memory",
    "set_clipboard_text",
    "analyze_screen",
    "play_media_query",
    "clipboard_action",
}

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
        # Ensure permanent chat archive is initialized (bootstraps from history.json if needed)
        load_chat_log()
        self.last_tools_used: list[str] = []
        # Load persistent conversation history so context survives restarts
        self.history = self._load_history()

    @staticmethod
    def _build_system_prompt() -> str:
        prompt = SYSTEM_PROMPT
        memories_block = get_memories_prompt_block()
        if memories_block:
            prompt += f"\n\nLEARNED LONG-TERM MEMORIES ABOUT THE USER:\n{memories_block}"
        routines_block = get_routines_prompt_block()
        if routines_block:
            prompt += f"\n\nSAVED EVERYDAY ROUTINES:\n{routines_block}"
        return prompt

    def _refresh_system_prompt(self):
        prompt = self._build_system_prompt()
        if self.history and self.history[0].get("role") == "system":
            self.history[0]["content"] = prompt
        else:
            self.history.insert(0, {"role": "system", "content": prompt})

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
        sys_msg = [{"role": "system", "content": self._build_system_prompt()}]
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    filtered = [m for m in data if m.get("role") != "system"]
                    recent = filtered[-MAX_HISTORY_TURNS:]
                    recent = self._trim_to_user_start(recent)
                    recent = self._clean_messages(recent)
                    if recent:
                        return sys_msg + recent
            except Exception:
                pass
        return sys_msg

    def _save_history(self):
        try:
            # Keep system prompt + last N turns, cleaned for reload safety
            system_msg = [{"role": "system", "content": self._build_system_prompt()}]
            non_system = [m for m in self.history if m.get("role") != "system"][-MAX_HISTORY_TURNS:]
            non_system = self._clean_messages(non_system)
            to_save = system_msg + non_system
            HISTORY_FILE.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
        except Exception:
            pass

    def clear_history(self) -> str:
        self.history = [{"role": "system", "content": self._build_system_prompt()}]
        if HISTORY_FILE.exists():
            try:
                HISTORY_FILE.unlink()
            except Exception:
                pass
        return "Conversation memory cleared."

    def run(self, user_text: str) -> str:
        self._refresh_system_prompt()
        self.last_tools_used = []
        self.history.append({"role": "user", "content": user_text})
        cleared_during_turn = False

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
                if cleared_during_turn:
                    self.history = [{"role": "system", "content": self._build_system_prompt()}]
                else:
                    self._save_history()
                append_chat_log(user_text, reply, self.last_tools_used)
                return reply

            # Execute every tool call Gemini asked for, then loop again so
            # it can see the results and decide what's next.
            turn_results: list[tuple[str, str]] = []
            all_direct = True
            for tc in message.tool_calls:
                tool_name = tc.function.name
                self.last_tools_used.append(tool_name)
                if tool_name == "clear_memory":
                    cleared_during_turn = True
                if tool_name not in DIRECT_RETURN_TOOLS:
                    all_direct = False
                fn = TOOL_FUNCTIONS.get(tool_name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if fn is None:
                    result = f"Unknown tool: {tool_name}"
                    all_direct = False
                else:
                    try:
                        result = fn(**args)
                    except Exception as e:
                        result = f"Tool {tool_name} failed: {e}"
                        all_direct = False

                # Refresh system prompt immediately if a memory or routine tool updated disk state
                if tool_name in ("remember_fact", "forget_memory", "save_routine", "delete_routine"):
                    self._refresh_system_prompt()

                result_str = str(result)
                turn_results.append((tool_name, result_str))
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tool_name,
                    "content": result_str,
                })

            # Fast-path: if all executed tools produce final user-ready replies,
            # return immediately without making a second round-trip to Gemini.
            if all_direct and turn_results:
                non_memory = [r for name, r in turn_results if name != "remember_fact"]
                reply_parts = non_memory if non_memory else [r for _, r in turn_results]
                reply = " ".join(p.strip() for p in reply_parts if p and p.strip()).strip()
                if reply:
                    self.history.append({"role": "assistant", "content": reply})
                    if cleared_during_turn:
                        self.history = [{"role": "system", "content": self._build_system_prompt()}]
                    else:
                        self._save_history()
                    append_chat_log(user_text, reply, self.last_tools_used)
                    return reply
