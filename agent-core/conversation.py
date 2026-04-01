"""
Shared persistent conversation store for AkiClaw.

All channels (dashboard WebSocket, Telegram) read/write to the same
JSON file so conversation history is unified and persistent.
"""

import json
import fcntl
import time
from pathlib import Path
from datetime import datetime, timezone


class ConversationStore:
    """File-backed conversation store with file locking for concurrency."""

    def __init__(self, path: Path, max_messages: int = 200):
        self.path = path
        self.max_messages = max_messages
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"messages": [], "updated_at": _now()})

    def _read(self) -> dict:
        try:
            with open(self.path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
                return data
        except (json.JSONDecodeError, FileNotFoundError):
            return {"messages": [], "updated_at": _now()}

    def _write(self, data: dict):
        with open(self.path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(data, f, indent=2, default=str)
            fcntl.flock(f, fcntl.LOCK_UN)

    def get_history(self) -> list[dict]:
        """Return all messages as LLM-compatible history."""
        data = self._read()
        return data.get("messages", [])

    def get_display_history(self) -> list[dict]:
        """Return messages with metadata for UI display."""
        data = self._read()
        return data.get("messages", [])

    def append(self, role: str, content, channel: str = "dashboard", **extra):
        """Append a message. Content can be str or dict (for tool_calls)."""
        data = self._read()
        msg = {
            "role": role,
            "content": content,
            "channel": channel,
            "timestamp": _now(),
        }
        msg.update(extra)
        data["messages"].append(msg)
        data["updated_at"] = _now()

        # Compact if over limit — keep recent messages
        if len(data["messages"]) > self.max_messages:
            data["messages"] = data["messages"][-self.max_messages // 2:]

        self._write(data)

    def append_raw(self, msg: dict, channel: str = "dashboard"):
        """Append a raw LLM message dict (for tool_calls etc)."""
        data = self._read()
        msg["channel"] = channel
        msg["timestamp"] = msg.get("timestamp", _now())
        data["messages"].append(msg)
        data["updated_at"] = _now()

        if len(data["messages"]) > self.max_messages:
            data["messages"] = data["messages"][-self.max_messages // 2:]

        self._write(data)

    def get_llm_messages(self) -> list[dict]:
        """Return messages formatted for LLM API (strip metadata, remove orphans)."""
        raw = self.get_history()

        # Collect all tool_call IDs present in assistant messages
        valid_tc_ids = set()
        for msg in raw:
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                if tc_id:
                    valid_tc_ids.add(tc_id)

        messages = []
        for msg in raw:
            # Skip orphaned tool results (tool_call_id not in any assistant message)
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                if msg["tool_call_id"] not in valid_tc_ids:
                    continue

            llm_msg = {"role": msg["role"]}
            if msg.get("tool_calls"):
                llm_msg["tool_calls"] = msg["tool_calls"]
                llm_msg["content"] = msg.get("content", "")
            elif msg.get("tool_call_id"):
                llm_msg["tool_call_id"] = msg["tool_call_id"]
                llm_msg["content"] = msg.get("content", "")
            else:
                llm_msg["content"] = msg.get("content", "")
            messages.append(llm_msg)
        return messages

    def clear(self):
        """Clear all messages."""
        self._write({"messages": [], "updated_at": _now()})

    def needs_compaction(self, max_context_messages: int = 25) -> bool:
        """Check if conversation needs compaction (by count or size)."""
        messages = self.get_history()
        if len(messages) > max_context_messages:
            return True
        # Also compact if total content exceeds 60K chars (safe for MiniMax)
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        if total_chars > 60_000:
            return True
        return False

    def compact(self, summary: str):
        """Replace old messages with a summary, keep recent ones.
        Finds a clean cut point to avoid orphaned tool results."""
        data = self._read()
        messages = data.get("messages", [])
        if len(messages) <= 10:
            return

        # Find a clean cut point: start of a user message (not mid tool-call chain)
        # Work backwards from -10 to find a safe boundary
        keep_from = max(0, len(messages) - 10)
        for i in range(keep_from, max(0, keep_from - 10), -1):
            if messages[i].get("role") == "user":
                keep_from = i
                break
            elif messages[i].get("role") == "assistant" and not messages[i].get("tool_calls"):
                keep_from = i
                break

        recent = messages[keep_from:]
        compacted = [{
            "role": "system",
            "content": f"[Conversation summary from earlier messages]\n{summary}",
            "channel": "system",
            "timestamp": _now(),
            "is_summary": True,
        }] + recent
        data["messages"] = compacted
        data["updated_at"] = _now()
        data["last_compaction"] = _now()
        self._write(data)

    def emergency_compact(self):
        """Drop old messages without LLM summarization. Used when context is too large
        to even call the LLM for a proper summary."""
        data = self._read()
        messages = data.get("messages", [])
        if len(messages) <= 6:
            return
        # Keep only the last 6 messages
        keep_from = len(messages) - 6
        # Find a clean boundary (start of user message)
        for i in range(keep_from, max(0, keep_from - 4), -1):
            if messages[i].get("role") == "user":
                keep_from = i
                break
        recent = messages[keep_from:]
        data["messages"] = [{
            "role": "system",
            "content": "[Previous conversation was truncated to fit context window]",
            "channel": "system",
            "timestamp": _now(),
            "is_summary": True,
        }] + recent
        data["updated_at"] = _now()
        data["last_compaction"] = _now()
        self._write(data)

    def get_compaction_context(self) -> str:
        """Get older messages as text for summarization."""
        messages = self.get_history()
        if len(messages) <= 10:
            return ""
        old = messages[:-10]
        parts = []
        for m in old:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, str) and content:
                channel = m.get("channel", "")
                prefix = f"[{channel}] " if channel else ""
                parts.append(f"{prefix}{role}: {content[:500]}")
        return "\n".join(parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
