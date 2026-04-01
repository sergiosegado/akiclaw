"""
AkiClaw Telegram Bridge

Uses the shared agent_core module for LLM calls, tools, and conversation.
Same agent logic for all agents — unified codebase, per-agent config via env + data volume.
"""

import os
import re
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode, ChatAction

from agent_core import create_store
from agent_core.llm import call_llm, load_config, load_system_prompt, estimate_context_chars, MINIMAX_MAX_CONTEXT_CHARS
from agent_core.tools import execute_tool

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("agent-telegram")

# --- Config ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
AKICLAW_HOME = Path(os.environ.get("AKICLAW_HOME", "/agent-data"))
MAX_MSG_LEN = 4096  # Telegram limit

# Public commands: these slash commands work for ANY user in DMs (no whitelist).
# Configure via env var as comma-separated list. Empty = no public commands.
# Example: PUBLIC_DM_COMMANDS=/credential,/join-agent
PUBLIC_DM_COMMANDS = [
    cmd.strip().lower()
    for cmd in os.environ.get("PUBLIC_DM_COMMANDS", "").split(",")
    if cmd.strip()
]

# Reject message for non-whitelisted DM users
DM_REJECT_MESSAGE = os.environ.get(
    "DM_REJECT_MESSAGE",
    "I only respond to specific commands in DMs. Use /start to see available commands.",
)

# Regex to redact secrets from log output
_SECRET_RE = re.compile(
    r'(sk-[a-zA-Z0-9-]{8})[a-zA-Z0-9-]+'
    r'|(key|token|secret|password|apikey)["\s:=]+["\']?([a-zA-Z0-9_-]{8})[a-zA-Z0-9_-]+',
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    """Redact anything that looks like a secret from log output."""
    return _SECRET_RE.sub(r'\1\2...REDACTED', text)


def _md_to_html(text: str) -> str:
    """Convert common markdown to Telegram HTML. Best-effort, won't break on invalid input."""
    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    # Italic: *text* or _text_ (but not inside URLs or already converted)
    text = re.sub(r'(?<![<\w])_([^_\n]+?)_(?![>\w])', r'<i>\1</i>', text)
    # Inline code: `text`
    text = re.sub(r'`([^`\n]+?)`', r'<code>\1</code>', text)
    # Code blocks: ```text``` — convert to <pre>
    text = re.sub(r'```[\w]*\n?(.*?)```', r'<pre>\1</pre>', text, flags=re.DOTALL)
    # Headers: ## text → bold
    text = re.sub(r'^#{1,4}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Links: [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return text


async def safe_reply(message, text: str):
    """Send a message with HTML formatting, fall back to plain text if it fails."""
    chunks = [text[i:i + MAX_MSG_LEN] for i in range(0, len(text), MAX_MSG_LEN)]
    for chunk in chunks:
        try:
            html = _md_to_html(chunk)
            await message.reply_text(html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            try:
                await message.reply_text(chunk)
            except Exception as e:
                log.warning("Failed to send message: %s", e)

# --- Shared persistent conversation store (Supabase primary, file fallback) ---
store = create_store(fallback_path=AKICLAW_HOME / "conversation.json")


async def agent_loop(chat_id: int, user_message: str, chat=None) -> str:
    """Run the agent loop: LLM call -> tool execution -> repeat until text response."""
    store.append("user", user_message, channel="telegram")

    cfg = load_config()
    system_prompt = load_system_prompt()
    turns = 0
    max_turns = cfg.get("max_turns", 20)

    # Build-mode detection: if agent has an active task, allow more turns but add checkpoints
    task_file = AKICLAW_HOME / "tasks" / "current.md"
    is_building = False
    try:
        if task_file.exists():
            task_content = task_file.read_text()
            is_building = "Status: in_progress" in task_content
    except Exception:
        pass
    if is_building:
        max_turns = min(max_turns + 10, 35)
        log.info("Build mode detected — max_turns raised to %d", max_turns)

    async def send_typing():
        if chat:
            try:
                await chat.send_action(ChatAction.TYPING)
            except Exception:
                pass

    empty_tool_streak = 0
    total_empty_calls = 0
    status_message = None

    while turns < max_turns:
        turns += 1
        log.info("Agent turn %d/%d", turns, max_turns)

        # Build-mode checkpoint: every 8 turns, remind agent to update task file
        if is_building and turns > 1 and turns % 8 == 0:
            store.append("system", f"CHECKPOINT (turn {turns}/{max_turns}): You have used {turns} tool calls. Update your tasks/current.md file NOW before continuing. If stuck, STOP and report to the user.", channel="telegram")
            log.info("Injected build checkpoint at turn %d", turns)

        await send_typing()
        messages = [{"role": "system", "content": system_prompt}] + store.get_llm_messages()

        # Pre-flight context size check — compact before calling LLM if too large
        ctx_chars = estimate_context_chars(messages)
        if ctx_chars > MINIMAX_MAX_CONTEXT_CHARS:
            log.warning("Pre-flight: context too large (%d chars), compacting", ctx_chars)
            await _auto_compact(cfg)
            messages = [{"role": "system", "content": system_prompt}] + store.get_llm_messages()

        try:
            response = await call_llm(messages, cfg)
        except httpx.HTTPStatusError as e:
            log.error("LLM API error: %d", e.response.status_code)
            if e.response.status_code == 400 and turns == 1:
                log.info("400 error on first turn — emergency compacting and retrying")
                store.emergency_compact()
                continue
            return f"LLM API error: {e.response.status_code}"
        except httpx.ReadTimeout:
            log.warning("ReadTimeout on turn %d — compacting and retrying", turns)
            if turns <= max_turns - 2:
                await _auto_compact(cfg)
                continue
            return "The model took too long to respond. Try a simpler request or say /reset to start fresh."
        except Exception as e:
            log.error("LLM call failed: %s", type(e).__name__)
            return f"Error calling LLM: {type(e).__name__}"

        # Handle context overflow — compact and retry
        if response.get("_context_overflow"):
            log.warning("Context overflow detected — emergency compacting")
            store.emergency_compact()
            if turns <= 2:
                continue
            return "Context was too large. I've cleared old messages — please try again."

        # Handle malformed API responses (rate limits, model errors, etc.)
        if "choices" not in response or not response["choices"]:
            error_msg = response.get("error", {}).get("message", "") if isinstance(response.get("error"), dict) else str(response.get("error", "Unknown API error"))
            log.error("LLM returned no choices (turn %d): %s", turns, _redact(str(error_msg)[:200]))
            if turns <= max_turns - 1 and "midstream" not in str(error_msg).lower():
                log.info("Retrying after provider error...")
                await asyncio.sleep(2)
                continue
            if status_message:
                try: await status_message.delete()
                except Exception: pass
            return f"LLM error: {error_msg or 'Model returned empty response — try again.'}"

        choice = response["choices"][0]
        msg = choice.get("message", {})
        if not msg:
            log.warning("Empty message in choice at turn %d, retrying", turns)
            continue

        # Check for tool calls
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            store.append_raw(msg, channel="telegram")

            had_meaningful_call = False
            for tc in tool_calls:
                try:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    if not tool_name:
                        log.warning("Tool call missing name, skipping")
                        store.append("tool", "ERROR: Tool call had no function name.", channel="telegram", tool_call_id=tc.get("id", "unknown"))
                        continue
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        log.warning("Malformed tool args: %s", str(func.get("arguments", ""))[:200])
                        store.append("tool", "ERROR: Malformed tool arguments. Use valid JSON. Example: {\"command\": \"ls\"}", channel="telegram", tool_call_id=tc.get("id", "unknown"))
                        continue
                except Exception as e:
                    log.error("Unexpected error parsing tool call: %s", e)
                    continue

                # Detect empty/useless tool calls (LLM stuck in a loop)
                cmd = args.get("command", "")
                if tool_name == "shell" and (not args or not cmd or not cmd.strip()):
                    log.warning("Empty shell call at turn %d — skipping", turns)
                    store.append("tool", "ERROR: Empty command. You must provide a command to execute. If you have completed the task, respond with a text message instead of calling tools.", channel="telegram", tool_call_id=tc["id"])
                    continue

                had_meaningful_call = True
                await send_typing()

                # Send/update temporary status message
                if chat:
                    status_text = f"Working... (step {turns})"
                    try:
                        if status_message is None:
                            status_message = await chat.send_message(status_text)
                        elif turns % 3 == 0:
                            await status_message.edit_text(f"Working... (step {turns})")
                    except Exception:
                        pass

                log.info("TOOL [turn %d] %s: %s", turns, tool_name, str(args)[:150])
                output = await execute_tool(tool_name, args)
                # Cap tool outputs to prevent context explosion
                # MCP responses (search results, API data) can be 50K+ chars
                MAX_TOOL_OUTPUT = 4000
                if len(output) > MAX_TOOL_OUTPUT:
                    output = output[:MAX_TOOL_OUTPUT] + f"\n...(truncated from {len(output)} chars)"
                store.append("tool", output, channel="telegram", tool_call_id=tc["id"])

            # Mid-loop compaction: prevent context from exploding during long builds
            max_msgs = cfg.get("max_messages", 30)
            if turns % 10 == 0 and store.needs_compaction(max_msgs):
                log.info("Mid-loop compaction at turn %d", turns)
                await _auto_compact(cfg)

            # Track empty calls — break on 3 consecutive OR 5 total
            if had_meaningful_call:
                empty_tool_streak = 0
            else:
                empty_tool_streak += 1
                total_empty_calls += 1
                if empty_tool_streak >= 3 or total_empty_calls >= 5:
                    log.warning("Breaking loop: %d consecutive empty tool calls", empty_tool_streak)
                    if status_message:
                        try: await status_message.delete()
                        except Exception: pass
                    store.append("assistant", "I got stuck in a tool loop. Let me try to answer directly. Could you rephrase your request?", channel="telegram")
                    return "I got stuck in a tool loop. Let me try to answer directly. Could you rephrase your request?"

            continue

        # No tool calls — we have a text response
        text = msg.get("content", "")
        store.append("assistant", text, channel="telegram")

        if status_message:
            try:
                await status_message.delete()
            except Exception:
                pass
            status_message = None

        # Auto-compact if conversation is getting long
        max_msgs = cfg.get("max_messages", 40)
        if store.needs_compaction(max_msgs):
            await _auto_compact(cfg)

        _log_session(chat_id, user_message, text)
        return text

    log.warning("Agent hit max turns (%d) for chat %s", max_turns, chat_id)
    if status_message:
        try: await status_message.delete()
        except Exception: pass

    try:
        summary_msgs = [
            {"role": "system", "content": "You ran out of tool calls. Summarize what you accomplished and what remains. Be concise (2-3 sentences). If you saved files, mention their paths."},
            *store.get_llm_messages()[-6:],
        ]
        summary_resp = await call_llm(summary_msgs, cfg)
        if summary_resp.get("choices") and summary_resp["choices"][0].get("message", {}).get("content"):
            summary = summary_resp["choices"][0]["message"]["content"]
            store.append("assistant", summary, channel="telegram")
            return summary
    except Exception as e:
        log.warning("Failed to generate summary: %s", e)

    return f"I used all {max_turns} tool calls on this task. Progress was saved to my data volume. Say 'continue' and I'll pick up where I left off."


async def _auto_compact(cfg: dict):
    """Summarize old messages and compact the conversation.
    Falls back to emergency (no-LLM) compaction if the summary call fails."""
    context = store.get_compaction_context()
    if not context:
        return
    try:
        if len(context) > 8000:
            context = context[:8000] + "\n...(truncated)"
        summary_msgs = [
            {"role": "system", "content": "Summarize this conversation concisely. Keep key facts, decisions, and context. Be brief (under 300 words)."},
            {"role": "user", "content": context},
        ]
        resp = await call_llm(summary_msgs, cfg)
        if resp.get("choices") and resp["choices"][0].get("message", {}).get("content"):
            summary = resp["choices"][0]["message"]["content"]
            store.compact(summary)
            log.info("Conversation auto-compacted with LLM summary")
            return
    except Exception as e:
        log.warning("LLM-based compaction failed: %s — falling back to emergency compact", e)
    store.emergency_compact()
    log.info("Emergency compaction applied (no LLM summary)")


def _log_session(chat_id: int, user_msg: str, assistant_msg: str):
    """Log session to daily file. Redacts potential secrets."""
    log_dir = AKICLAW_HOME / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    log_file = log_dir / f"telegram-{today}.txt"
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] CHAT:{chat_id} USER: {_redact(user_msg[:500])}\n")
        f.write(f"[{timestamp}] CHAT:{chat_id} AGENT: {assistant_msg[:500]}\n\n")


def is_allowed(user_id: int) -> bool:
    """Check if a user is in the whitelist. Deny-by-default when list is empty."""
    if not ALLOWED_USERS or ALLOWED_USERS == [""]:
        log.warning("TELEGRAM_ALLOWED_USERS is empty — rejecting user %s", user_id)
        return False
    return str(user_id) in ALLOWED_USERS


def _is_public_command(text: str) -> bool:
    """Check if a message starts with a configured public command."""
    if not PUBLIC_DM_COMMANDS:
        return False
    text_lower = text.strip().lower()
    for cmd in PUBLIC_DM_COMMANDS:
        if text_lower == cmd or text_lower.startswith(cmd + " "):
            return True
    return False


def split_message(text: str) -> list[str]:
    if len(text) <= MAX_MSG_LEN:
        return [text]
    chunks = []
    while text:
        if len(text) <= MAX_MSG_LEN:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_MSG_LEN)
        if split_at == -1:
            split_at = MAX_MSG_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# --- Handlers ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — show available commands. Works for all users."""
    user_id = update.effective_user.id
    is_dm = update.effective_chat.type == "private"

    if is_allowed(user_id):
        await update.message.reply_text(
            "Agent online.\n"
            "Send me a task or question. I have shell access + MCP integrations.\n"
            "/reset to clear conversation history.\n"
            "/status for system health."
        )
    elif is_dm and PUBLIC_DM_COMMANDS:
        cmds = ", ".join(PUBLIC_DM_COMMANDS)
        await update.message.reply_text(
            f"Welcome! Available commands: {cmds}\n"
            "Send one of these commands to get started."
        )
    elif is_dm:
        await update.message.reply_text("This bot is not accepting public messages.")


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    store.clear()
    await update.message.reply_text("Conversation cleared.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    from agent_core.tools import run_shell
    uptime = run_shell("uptime")
    disk = run_shell("df -h / | tail -1")
    containers = run_shell("docker ps --format '{{.Names}}: {{.Status}}' | head -10")
    msg = f"System Status\n\n{uptime}\n\nDisk: {disk}\n\nContainers:\n{containers}"
    await update.message.reply_text(msg)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    if not user_msg:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    is_group = update.effective_chat.type in ("group", "supergroup")
    is_mentioned = False

    # In groups: respond to @mentions, replies to bot, or messages from allowed users
    if is_group:
        bot_username = (await context.bot.get_me()).username
        is_mentioned = (f"@{bot_username}" in user_msg) if bot_username else False
        is_reply_to_bot = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.is_bot
        )
        is_from_allowed = is_allowed(user_id)

        if not (is_mentioned or is_reply_to_bot or is_from_allowed):
            return

        if is_mentioned and bot_username:
            user_msg = user_msg.replace(f"@{bot_username}", "").strip()
            if not user_msg:
                user_msg = "What can you help me with?"
    else:
        # DMs: check whitelist first, then public commands
        if not is_allowed(user_id):
            if _is_public_command(user_msg):
                # Public command from non-whitelisted user — allow it through
                log.info("Public command from user %s: %s", user_id, user_msg.split()[0])
            else:
                log.info("Rejected DM from non-whitelisted user: %s", user_id)
                await update.message.reply_text(DM_REJECT_MESSAGE)
                return

    log.info("Message from %s (group=%s): %s", user_id, is_group, user_msg[:80])

    await update.message.chat.send_action(ChatAction.TYPING)
    response = await agent_loop(chat_id, user_msg, chat=update.message.chat)

    await safe_reply(update.message, response)


# === SCHEDULED JOBS (Heartbeat & Daily Review) ===

async def _heartbeat_job(context):
    """Runs every 5 minutes: health checks, model file, heartbeat script."""
    try:
        model = os.environ.get("AKICLAW_MODEL", "")
        if model:
            (AKICLAW_HOME / ".current_model").write_text(model)

        hb = Path(__file__).parent / "heartbeat.py"
        if hb.exists():
            import subprocess as sp
            sp.run(["python3", str(hb)], timeout=60, capture_output=True)
        log.info("Heartbeat OK")
    except Exception as e:
        log.warning("Heartbeat error: %s", e)


async def _daily_review_job(context):
    """Runs daily at 08:00 UTC: LLM-powered self-assessment."""
    try:
        dr = Path(__file__).parent / "daily_review.py"
        if dr.exists():
            import subprocess as sp
            sp.run(["python3", str(dr)], timeout=120, capture_output=True)
            log.info("Daily review completed")
        else:
            log.info("No daily_review.py found — skipping")
    except Exception as e:
        log.warning("Daily review error: %s", e)


def main():
    cfg = load_config()
    log.info("Starting AkiClaw Telegram bot...")
    log.info("Model: %s", cfg["model"])
    log.info("Endpoint: %s", cfg["endpoint"])
    log.info("AKC Home: %s", AKICLAW_HOME)

    if not ALLOWED_USERS or ALLOWED_USERS == [""]:
        log.critical(
            "TELEGRAM_ALLOWED_USERS is not set! Bot will reject all DMs. "
            "Set this env var with comma-separated Telegram user IDs."
        )
    else:
        log.info("Allowed users: %s", ALLOWED_USERS)

    if PUBLIC_DM_COMMANDS:
        log.info("Public DM commands: %s", PUBLIC_DM_COMMANDS)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Schedule recurring jobs (if job_queue available)
    jq = app.job_queue
    if jq:
        jq.run_repeating(_heartbeat_job, interval=300, first=10, name="heartbeat")
        jq.run_daily(_daily_review_job, time=datetime.strptime("08:00", "%H:%M").time(), name="daily_review")
        log.info("Scheduled: heartbeat (5min), daily review (08:00 UTC)")
    else:
        log.warning("job_queue not available — install python-telegram-bot[job-queue]")

    log.info("Agent bot ready. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
