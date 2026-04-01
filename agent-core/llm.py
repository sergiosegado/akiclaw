"""
Unified LLM calling logic for AkiClaw agent.

Supports both Anthropic Messages API and OpenAI-compatible APIs.
MiniMax M2.7 hardened: context size tracking, auto-truncation, null-response handling.
"""

import asyncio
import json
import os
import logging
from pathlib import Path

import httpx

from .tools import build_openai_tools, build_anthropic_tools

log = logging.getLogger("akc.llm")

AKICLAW_HOME = Path(os.environ.get("AKICLAW_HOME", "/agent-data"))
CONFIG_FILE = AKICLAW_HOME / "config.json"

# MiniMax M2.7 context limit: 128K tokens. 1 token ≈ 3.5 chars for English.
# Use 80K chars as safe ceiling (leaves room for system prompt + tool defs + response).
MINIMAX_MAX_CONTEXT_CHARS = 80_000


def load_config() -> dict:
    """Load LLM configuration from env vars + config file."""
    defaults = {
        "api_key": os.environ.get("AKICLAW_API_KEY", ""),
        "model": os.environ.get("AKICLAW_MODEL", "MiniMax-M2.7"),
        "endpoint": os.environ.get(
            "OPENROUTER_ENDPOINT",
            "https://openrouter.ai/api/v1/chat/completions",
        ),
        "max_turns": 50,
        "max_messages": 25,
    }
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            defaults.update(saved)
        except Exception:
            pass
    return defaults


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def is_anthropic(cfg: dict) -> bool:
    return "anthropic.com" in cfg.get("endpoint", "")


def load_system_prompt(data_home: Path = None) -> str:
    """Load the full system prompt from markdown personality/skill files.

    Supports bootstrapping: if .bootstrapped flag exists, BOOTSTRAP.md is skipped.
    Memory files are loaded most-recent-first and capped to fit context limits.
    """
    home = data_home or AKICLAW_HOME
    bootstrapped = (home / ".bootstrapped").exists()

    # Load core and skills (always included — these define the agent)
    core_parts = []
    for subdir in ["core", "skills"]:
        d = home / subdir
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name == "BOOTSTRAP.md" and bootstrapped:
                continue
            try:
                core_parts.append(f.read_text())
            except Exception:
                continue

    program = home / "autoresearch" / "PROGRAM.md"
    if program.exists():
        core_parts.append(program.read_text())

    core_text = "\n\n---\n\n".join(core_parts)

    # Load memory files (most recent first, capped at budget)
    # Reserve 40K chars for system prompt total — leave rest for conversation
    MAX_SYSTEM_CHARS = 40_000
    memory_budget = max(0, MAX_SYSTEM_CHARS - len(core_text))

    memory_dir = home / "memory"
    if memory_dir.is_dir() and memory_budget > 0:
        mem_files = sorted(memory_dir.glob("*.md"), reverse=True)  # newest first
        mem_parts = []
        mem_chars = 0
        for f in mem_files:
            try:
                content = f.read_text()
                if mem_chars + len(content) > memory_budget:
                    # Include truncated version of this file if we have room
                    remaining = memory_budget - mem_chars
                    if remaining > 200:
                        mem_parts.append(content[:remaining] + "\n...(memory truncated)")
                    break
                mem_parts.append(content)
                mem_chars += len(content)
            except Exception:
                continue
        if mem_parts:
            mem_parts.reverse()  # Back to chronological order
            core_text += "\n\n---\n\n[Agent Memory]\n\n" + "\n\n---\n\n".join(mem_parts)

    if len(core_text) > MAX_SYSTEM_CHARS:
        log.warning("System prompt still too large (%d chars), hard-truncating to %d",
                    len(core_text), MAX_SYSTEM_CHARS)
        core_text = core_text[:MAX_SYSTEM_CHARS] + "\n...(system prompt truncated)"

    return core_text


def _anthropic_to_openai(resp_data: dict) -> dict:
    """Normalize Anthropic Messages API response to OpenAI chat completions format."""
    content_blocks = resp_data.get("content", [])
    text_parts = []
    tool_calls = []
    for block in content_blocks:
        if block["type"] == "text":
            text_parts.append(block["text"])
        elif block["type"] == "tool_use":
            tool_calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block["input"]),
                    },
                }
            )
    msg = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else "",
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [
            {"message": msg, "finish_reason": resp_data.get("stop_reason", "end_turn")}
        ]
    }


def _convert_messages_for_anthropic(
    messages: list[dict],
) -> tuple[str, list[dict]]:
    """Extract system prompt and convert messages to Anthropic format."""
    system = ""
    converted = []
    for m in messages:
        if m["role"] == "system":
            system = m.get("content", "")
        elif m["role"] == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", ""),
                            "content": m.get("content", ""),
                        }
                    ],
                }
            )
        elif m["role"] == "assistant" and m.get("tool_calls"):
            content = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"]),
                    }
                )
            converted.append({"role": "assistant", "content": content})
        else:
            converted.append({"role": m["role"], "content": m.get("content", "")})
    return system, converted


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Make an HTTP request with retry on 429/529 rate limits."""
    max_retries = 4
    for attempt in range(max_retries + 1):
        resp = await client.request(method, url, **kwargs)
        if resp.status_code in (429, 529) and attempt < max_retries:
            retry_after = float(resp.headers.get("retry-after", 0))
            wait = max(retry_after, 2 ** attempt * 5)  # 5s, 10s, 20s, 40s
            log.warning("Rate limited (%d), retrying in %.0fs (attempt %d/%d)",
                        resp.status_code, wait, attempt + 1, max_retries)
            await asyncio.sleep(wait)
            continue
        if resp.status_code >= 400:
            log.error("LLM API error %d: (response body redacted)", resp.status_code)
        resp.raise_for_status()
        return resp
    if resp.status_code >= 400:
        log.error("LLM API error %d after retries: (response body redacted)", resp.status_code)
    resp.raise_for_status()
    return resp


def estimate_context_chars(messages: list[dict]) -> int:
    """Estimate total character count of messages for context size tracking."""
    total = 0
    for m in messages:
        total += len(m.get("content", "") or "")
        for tc in m.get("tool_calls", []):
            func = tc.get("function", {})
            total += len(func.get("name", "")) + len(func.get("arguments", ""))
    return total


def _truncate_tool_outputs(messages: list[dict], max_chars: int) -> list[dict]:
    """Truncate long tool outputs to fit within context limit.
    Preserves system prompt and recent messages, truncates old tool results first."""
    current = estimate_context_chars(messages)
    if current <= max_chars:
        return messages

    # Truncate tool results from oldest to newest, cap each at 500 chars
    result = []
    for i, m in enumerate(messages):
        if m.get("role") == "tool" and i < len(messages) - 6:
            content = m.get("content", "")
            if len(content) > 500:
                m = {**m, "content": content[:500] + "\n...(truncated)"}
        result.append(m)

    current = estimate_context_chars(result)
    if current <= max_chars:
        return result

    # Still too big — drop oldest non-system messages until it fits
    system_msgs = [m for m in result if m.get("role") == "system"]
    other_msgs = [m for m in result if m.get("role") != "system"]
    while estimate_context_chars(system_msgs + other_msgs) > max_chars and len(other_msgs) > 4:
        other_msgs.pop(0)

    return system_msgs + other_msgs


def _clean_messages_for_openai(messages: list[dict]) -> list[dict]:
    """Filter empty messages and ensure valid structure for OpenAI-compat APIs."""
    clean = []
    for m in messages:
        content = m.get("content", "")
        if content or m.get("tool_calls") or m.get("tool_call_id"):
            clean.append(m)
        elif m["role"] == "system":
            clean.append({**m, "content": "(system context)"})
    if not any(m["role"] == "user" for m in clean):
        clean.append({"role": "user", "content": "(continue)"})
    return clean


async def call_llm(messages: list[dict], cfg: dict) -> dict:
    """Call the configured LLM and return OpenAI-format response dict."""
    if is_anthropic(cfg):
        system, anthropic_msgs = _convert_messages_for_anthropic(messages)
        body = {
            "model": cfg["model"],
            "max_tokens": 4096,
            "messages": anthropic_msgs,
            "tools": build_anthropic_tools(AKICLAW_HOME),
        }
        if system:
            body["system"] = system
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await _request_with_retry(
                client, "POST", cfg["endpoint"],
                headers={
                    "x-api-key": cfg["api_key"],
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            return _anthropic_to_openai(resp.json())
    else:
        is_minimax = "minimax" in cfg.get("endpoint", "")
        clean_messages = _clean_messages_for_openai(messages)

        # Auto-truncate if context is too large for MiniMax
        if is_minimax:
            ctx_chars = estimate_context_chars(clean_messages)
            if ctx_chars > MINIMAX_MAX_CONTEXT_CHARS:
                log.warning("Context too large (%d chars > %d limit), truncating",
                            ctx_chars, MINIMAX_MAX_CONTEXT_CHARS)
                clean_messages = _truncate_tool_outputs(clean_messages, MINIMAX_MAX_CONTEXT_CHARS)
                log.info("Context after truncation: %d chars", estimate_context_chars(clean_messages))

        body = {
            "model": cfg["model"],
            "messages": clean_messages,
            "tools": build_openai_tools(AKICLAW_HOME),
            "tool_choice": "auto",
            "max_tokens": 16384 if is_minimax else 4096,
        }
        if is_minimax:
            body["temperature"] = 0.7
        async with httpx.AsyncClient(timeout=180 if is_minimax else 120) as client:
            resp = await _request_with_retry(
                client, "POST", cfg["endpoint"],
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            data = resp.json()

            # Log token usage for debugging
            usage = data.get("usage", {})
            if usage:
                log.info("Tokens — prompt: %s, completion: %s, total: %s",
                         usage.get("prompt_tokens", "?"),
                         usage.get("completion_tokens", "?"),
                         usage.get("total_tokens", "?"))

            # Detect MiniMax silent context overflow: 200 OK but 0 tokens used
            if is_minimax and usage.get("total_tokens", 1) == 0 and not data.get("choices"):
                log.error("MiniMax returned 0 tokens (likely context overflow)")
                return {
                    "choices": [],
                    "error": {"message": "Context too large for model — auto-compacting"},
                    "_context_overflow": True,
                }

            data = _normalize_openai_response(data)
            # Preserve MiniMax reasoning_details
            if is_minimax and data.get("choices"):
                for choice in data["choices"]:
                    msg = choice.get("message", {})
                    if "reasoning_details" in msg:
                        msg["_reasoning"] = msg["reasoning_details"]
            return data


def _normalize_openai_response(data: dict) -> dict:
    """Validate and normalize an OpenAI-format response. Handle provider quirks."""
    # Provider returned an error inside a 200 response (MiniMax, some OpenRouter models)
    if "error" in data and "choices" not in data:
        error = data["error"]
        msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        log.error("Provider error in 200 response: %s", msg)
        return {"choices": [], "error": data["error"]}

    if "choices" not in data or not data["choices"]:
        log.error("Response missing choices: %s", str(data)[:300])
        return {"choices": [], "error": {"message": "Empty response from model"}}

    choice = data["choices"][0]
    msg = choice.get("message")
    if not msg:
        return {"choices": [], "error": {"message": "Empty message in response"}}

    # Fix malformed tool_calls (some models return broken structures)
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        clean_calls = []
        for tc in tool_calls:
            try:
                func = tc.get("function", {})
                if not func.get("name"):
                    log.warning("Tool call missing function name, skipping")
                    continue
                # Ensure arguments is a valid JSON string
                args_str = func.get("arguments", "{}")
                if not isinstance(args_str, str):
                    args_str = json.dumps(args_str)
                # Validate JSON
                json.loads(args_str)
                tc["function"]["arguments"] = args_str
                # Ensure tool call has an ID
                if not tc.get("id"):
                    import uuid
                    tc["id"] = f"call_{uuid.uuid4().hex[:12]}"
                clean_calls.append(tc)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                log.warning("Skipping malformed tool call: %s", str(e))
                continue

        if clean_calls:
            msg["tool_calls"] = clean_calls
        else:
            # All tool calls were malformed — treat as text response
            del msg["tool_calls"]
            if not msg.get("content"):
                msg["content"] = "I encountered an error with my tool calls. Let me try a different approach."

    return data
