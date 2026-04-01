# AkiClaw Framework

Unified agent framework powering all Telegram bot agents. **Public repo** — never commit secrets.

## Quick Deploy

After editing any framework code:

```bash
./deploy-fleet.sh
```

This uploads code to the server, rebuilds `akiclaw-bot:latest`, and restarts all 4 agents.

To restart a single agent:
```bash
ssh ovh-vps "cd /opt/akiclaw/agents/sese && docker compose up -d --force-recreate"
```

## Architecture

```
akiclaw/
├── agent-core/           # Shared Python module
│   ├── llm.py            # LLM calling, context management, response normalization
│   ├── conversation.py   # Persistent store, compaction, orphan filtering
│   ├── tools.py          # Shell execution, MCP proxy, permissions
│   └── supabase_store.py # Optional Supabase persistence
├── telegram-bot/         # Bot entry point
│   ├── bot.py            # Agent loop, Telegram handlers, auto-compaction
│   ├── Dockerfile        # Shared image definition
│   └── entrypoint.sh     # Volume permission fix (chown on start)
├── proxy/                # Credential proxy (FastAPI, injects MCP auth)
└── deploy-fleet.sh       # One-command deploy to all agents
```

All 4 agents (Sese, Aki, Chatty, Predakir) run the **same Docker image** (`akiclaw-bot:latest`). Agent identity, skills, memory, and MCP config live on per-agent Docker volumes mounted at `/agent-data/`.

## Agent Data Volume Structure

```
/agent-data/
├── core/                # IDENTITY.md, SOUL.md, HEARTBEAT.md
├── skills/              # *.md files loaded by number order into system prompt
├── memory/              # *.md files loaded newest-first with budget cap
├── vault/               # mcp-servers.json (MCP credentials — NEVER committed)
├── tools/               # subagent.py, custom tool scripts
├── permissions.json     # Shell/docker access, restrictions
├── config.json          # Runtime config (max_turns: 75, max_messages: 25)
├── conversation.json    # Persistent conversation store with file locking
└── logs/                # Daily Telegram session logs
```

## Server Layout

```
/opt/akiclaw/                  # Git clone of this repo
├── agent-core/                # Framework code (deployed here)
├── telegram-bot/              # Bot code (deployed here)
├── proxy/                     # Proxy code (deployed here)
├── deploy.sh                  # Server-side: build + restart all
└── agents/                    # Per-agent compose + .env (NOT in git)
    ├── sese/                  # docker-compose.yml + .env
    ├── aki/                   # docker-compose.yml + .env
    ├── chatty/                # docker-compose.yml + .env
    └── predakir/              # docker-compose.yml + .env
```

## LLM Backend: MiniMax M2.7

All agents use MiniMax M2.7 via `https://api.minimax.io/v1/chat/completions` (OpenAI-compatible).

| Setting | Value | Why |
|---------|-------|-----|
| Context window | 204K tokens (~700K chars) | Large but model silently fails near limits |
| max_tokens | 64,000 | MiniMax recommended for agentic work |
| Temperature | 1.0 | MiniMax recommended (does NOT support 0) |
| System prompt cap | 60K chars | Core + skills always included; memory fills remaining budget |
| Tool output cap | 4K chars | Truncated at source in tools.py to prevent context explosion |
| HTTP timeout | 240s | Retries on ReadTimeout with auto-compaction |
| Shell timeout | 30s | sleep >10s blocked to prevent agent hangs |

### MiniMax Quirks (must handle in code)

- **`<think>` tags**: Returns `<think>...</think>` reasoning blocks — stripped in `_normalize_openai_response` before reaching Telegram
- **`choices: null`**: Returns HTTP 200 with null choices on context overflow — detected and triggers emergency compaction
- **`completion_tokens: 0`**: Model accepted prompt but refused to generate — treated as overflow, triggers compaction + retry
- **Error 2013**: `invalid chat setting` = malformed messages (empty content, orphaned tool_calls without matching tool results)
- **Orphaned tool_calls**: After compaction, assistant messages with tool_calls may lack matching tool results — `get_llm_messages()` filters both directions

### Context Management Pipeline

1. **System prompt** (core/ + skills/ + memory/ with 60K budget)
2. **Pre-flight check**: If context > 200K chars, auto-compact before calling LLM
3. **Tool output truncation**: Each tool result capped at 4K chars at source
4. **Mid-loop compaction**: Every 10 turns during tool-use loops
5. **Post-response compaction**: When message count > max_messages (25)
6. **Emergency compaction**: On 400 errors or completion_tokens=0 — drops to last 6 messages without LLM summary
7. **ReadTimeout**: Auto-compacts and retries instead of failing

## Tools

Every agent gets two tools:

| Tool | Description | Limits |
|------|-------------|--------|
| `shell` | Execute commands on the container | 30s timeout, sleep >10s blocked, output 4K cap, dangerous patterns blocked |
| `mcp_request` | HTTP via credential proxy | 30s timeout, output 4K cap, auth injected from vault/mcp-servers.json |

Permissions per agent in `permissions.json`:
- `shellEnabled` / `shellRestricted` — restricted = only own `/agent-data/`
- `dockerEnabled` — can run docker commands (only Chatty has this)
- `destructiveRequiresConfirmation` — always true

## The 4 Agents

| Agent | Bot | Role | Shell | Docker | MCP Servers |
|-------|-----|------|-------|--------|-------------|
| Sese | @sesellow_bot | Interview intelligence | restricted | no | notion, fireflies x2, n8n, telegram |
| Aki | @yellowai_academy_bot | AI Academy educator | restricted | no | searxng, n8n, qdrant |
| Chatty | @chatty_bot | Fleet commander | **unrestricted** | **yes** | searxng, n8n, qdrant |
| Predakir | @predakir_bot | Trading/prediction | enabled | no | searxng, n8n, supabase |

See `agents/{name}/CLAUDE.md` in the parent OVH-SERVER repo for agent-specific docs.

## Security

- **NO secrets in this repo** — it's public on GitHub
- API keys live in `.env` files at `/opt/akiclaw/agents/{name}/.env` on the server
- MCP credentials in `vault/mcp-servers.json` on each agent's data volume
- Error logging redacts response bodies (MiniMax includes API key echoes)
- Entrypoint.sh runs as root briefly to chown volume, then drops to `agent` user via gosu
