> Based on [SubZeroClaw](https://github.com/jmlago/subzeroclaw) by [@jmlago](https://github.com/jmlago) — the original ~380-line C agentic daemon.

# AkiClaw Agent Framework

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-green.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://docs.docker.com/compose/)

## What is AkiClaw?

AkiClaw is a lightweight, open-source AI agent framework that runs entirely in Docker containers. It gives a large language model (LLM) a persistent identity, long-term memory, shell access, and authenticated connections to external APIs -- all controlled through a simple Telegram chat interface. Think of it as giving an LLM a body: a process that lives on a server, remembers what happened yesterday, can run commands, call APIs, and message you proactively when something needs attention.

The framework is built around plain Markdown files. Your agent's name, personality, knowledge, and skills are all defined in `.md` files that get loaded into the LLM's system prompt on every conversation turn. There's no complex configuration language to learn -- if you can write Markdown, you can build an AI agent. The credential proxy keeps your API keys safe by injecting authentication headers automatically, so the LLM never sees raw secrets. Everything runs in two Docker containers with a shared data volume.

## Architecture

```
+----------------------------------------------------------+
|                      Docker Host                          |
|                                                           |
|  +------------------+        +------------------------+   |
|  |   Telegram Bot   | <----> |   Credential Proxy     |   |
|  |   (agent-bot)    |        |   (agent-proxy)        |   |
|  |                  |        |                        |   |
|  |  - Receives msgs |        |  - Injects API keys    |   |
|  |  - Runs agent    |        |  - Rate limiting       |   |
|  |    loop          |        |  - Audit logging       |   |
|  |  - Sends replies |        |  - Strips secrets from |   |
|  +--------+---------+        |    responses           |   |
|           |                  +-----------+------------+   |
|           |                              |                |
|           v                              v                |
|  +----------------------------------------------------+  |
|  |              Shared Data Volume                     |  |
|  |                                                     |  |
|  |  data/core/      Identity, Soul, Heartbeat, Boot    |  |
|  |  data/skills/    Capability definitions (*.md)      |  |
|  |  data/memory/    MEMORY.md (persistent storage)     |  |
|  |  data/vault/     mcp-servers.json (API configs)     |  |
|  |  data/logs/      Audit trail (auto-created)         |  |
|  +----------------------------------------------------+  |
+----------------------------------------------------------+
                          |
                          v
                  +-----------------+
                  |    LLM API      |
                  |  (Anthropic or  |
                  |   OpenRouter)   |
                  +-----------------+
```

**How it works:** When you send a message on Telegram, the bot loads all your Markdown files into a system prompt, sends them along with your message to the LLM, and the LLM can respond with text or request tool calls (shell commands or API requests). Tool results get fed back to the LLM in a loop until it produces a final text response.

## Prerequisites

Before you start, make sure you have:

| Requirement | How to get it |
|-------------|---------------|
| **Docker + Docker Compose** | [Install Docker](https://docs.docker.com/get-docker/) |
| **Telegram Bot Token** | Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, follow the prompts |
| **Anthropic API Key** | Sign up at [console.anthropic.com](https://console.anthropic.com/), go to API Keys |
| **Your Telegram User ID** | Message [@userinfobot](https://t.me/userinfobot) on Telegram, it replies with your numeric ID |

**Alternative:** Instead of an Anthropic key, you can use an [OpenRouter](https://openrouter.ai/) key to access many different LLM models.

## Quick Start

### Step 1: Clone the repository

```bash
git clone https://github.com/sergiosegado/akiclaw.git my-agent
cd my-agent
```

### Step 2: Create your environment file

```bash
cp .env.example .env
```

Open `.env` in a text editor and fill in your values:

```bash
# Required: your Telegram bot token from @BotFather
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ

# Required: your LLM API key
AKICLAW_API_KEY=your-api-key-here

# Required: your Telegram user ID (comma-separated for multiple users)
# IMPORTANT: If empty, the bot rejects ALL DMs for security
TELEGRAM_ALLOWED_USERS=123456789

# Optional: public slash commands available to non-whitelisted users
# PUBLIC_DM_COMMANDS=/credential,/join-agent
```

### Step 3: Customize your agent's identity

Edit the files in `data/core/` to define who your agent is:

```bash
# Give your agent a name and purpose
nano data/core/IDENTITY.md

# Define behavioral rules and personality
nano data/core/SOUL.md

# Set up monitoring tasks (optional)
nano data/core/HEARTBEAT.md
```

See [Setting Up Your Agent Identity](#setting-up-your-agent-identity) below for detailed instructions on each file.

### Step 4: (Optional) Connect external APIs

```bash
cp data/vault/mcp-servers.example.json data/vault/mcp-servers.json
nano data/vault/mcp-servers.json
```

### Step 5: Build and start the containers

```bash
docker compose up -d --build
```

### Step 6: Seed the data volume

The agent's "brain" files need to be copied into the Docker volume:

```bash
docker compose cp data/. agent-bot-1:/agent-data/
```

### Step 7: Check the logs

```bash
docker compose logs -f agent-bot
```

You should see output like:
```
Starting AkiClaw Telegram bot...
Model: claude-sonnet-4-20250514
Agent bot ready. Polling...
```

### Step 8: Talk to your agent

Open Telegram, find your bot by its username, and send `/start`.

---

## Setting Up Your Agent Identity

All identity files live in `data/core/`. They are loaded into the LLM's system prompt in this order: IDENTITY.md -> SOUL.md -> HEARTBEAT.md -> BOOTSTRAP.md -> skills -> memory.

### IDENTITY.md -- Who Your Agent Is

This is the most important file. It loads first and defines the agent's core identity.

**Fields explained:**

| Field | What it does | Example |
|-------|-------------|---------|
| `name` | The agent's full name | `TaskBot`, `ServerGuard`, `ResearchHelper` |
| `alias` | Short name used in responses | `Task`, `Guard`, `Res` |
| `emoji` | Optional emoji for personality | Any emoji you like |
| `version` | Version tracking | `1.0.0` |

**Key sections to customize:**

1. **Who You Are** -- A 1-2 sentence description of what your agent does. Be specific. "You are a DevOps monitoring assistant" is better than "You are a helpful AI".

2. **Authorized Users** -- List every person who should be able to talk to the bot. Include their Telegram user ID (numeric). Anyone not listed will be silently ignored.

3. **Presentation** -- How the agent formats its responses. Telegram does not render Markdown well, so plain text with bullet points works best.

4. **Security Anchor** -- Do not modify this section. It prevents prompt injection attacks.

5. **Capabilities** -- List what your agent can actually do. This helps the LLM understand its own tools.

**Example:**

```markdown
## Identity -- StudyBuddy

name: StudyBuddy
alias: Buddy
emoji: books emoji
version: 1.0.0

### Who You Are
You are StudyBuddy -- a study assistant that helps track assignments,
quiz on material, and find research papers. You run on a server
with access to a Notion database of coursework.

### Authorized Users
Only these people can talk to you:
- Alice (Telegram ID: 111222333)
- Bob (Telegram ID: 444555666)
```

### SOUL.md -- How Your Agent Behaves

This file defines personality and behavioral rules. Unlike IDENTITY.md (which defines *who*), SOUL.md defines *how*.

**Sections to fill in:**

1. **Communication Style** -- How should the agent talk? Formal or casual? Bullet points or paragraphs? Short or detailed?

2. **Domain Knowledge** -- What domain expertise should the agent have? List the topics it should be knowledgeable about.

3. **Safety Rules** -- Hard limits on what the agent must never do. These are important guardrails.

**Example:**

```markdown
### Communication Style
- Use casual, friendly language
- Always explain technical terms
- When giving code examples, explain each line
- If you don't know something, say so

### Domain Knowledge
- Python programming and debugging
- Docker container management
- Git version control
- REST API design

### Safety Rules
- NEVER delete files without explicit confirmation
- NEVER share API keys or credentials in chat
- Always ask before running commands that modify data
```

### HEARTBEAT.md -- Proactive Monitoring

The heartbeat system runs on a schedule (configurable, default every 30 minutes). It tells the agent what to check proactively, even when you haven't sent a message.

**Sections:**

1. **System Health** -- Server-level checks (disk, memory, containers)
2. **Application Checks** -- Your specific services and URLs
3. **Agent Self-Check** -- Internal housekeeping
4. **Escalation Rules** -- When to alert you vs. just log it

**Example custom check:**

```markdown
### Application Checks (every 1h)
- Check if my web app is responding: curl -s https://myapp.example.com/health
- Verify the database has less than 1000 pending jobs
- Check if SSL certificate expires within 30 days
```

### BOOTSTRAP.md -- First-Run Checklist

This file runs once when the agent starts for the first time. It's a checklist of things to verify before the agent is "ready."

You typically don't need to modify this much unless you add custom infrastructure checks.

---

## Adding Skills

Skills are Markdown files in `data/skills/` that teach your agent new capabilities. Each `.md` file is automatically loaded into the system prompt -- no code changes needed.

### How to create a skill

1. Create a new file in `data/skills/` (e.g., `001-my-skill.md`)
2. Describe when the skill should activate
3. List the exact steps the agent should follow
4. Include example tool calls with parameters

### Naming convention

Files are loaded alphabetically. Use numbered prefixes to control load order:
- `000-core-skill.md` (loads first)
- `001-secondary-skill.md`
- `002-another-skill.md`

### Example: A weather checking skill

```markdown
## Skill: Weather Checker

When the user asks about weather for a city:

1. Use the mcp_request tool:
   - server_id: "weather"
   - method: "GET"
   - path: "/weather?q={CITY_NAME}&units=metric"
2. Parse the JSON response
3. Report: temperature, conditions, humidity, wind speed
4. If the request fails, suggest the user check the city name spelling

Example:
  mcp_request(server_id="weather", method="GET", path="/weather?q=London&units=metric")
```

### Tips for writing good skills

- Be explicit about which tool to use (`shell` or `mcp_request`)
- Include example parameters so the LLM knows the exact format
- Describe error handling ("if X fails, do Y")
- Keep each skill focused on one capability
- After adding a skill, restart the bot or re-seed the data volume

---

## Configuring MCP Servers

MCP (Model Context Protocol) servers let your agent call external APIs securely. The credential proxy handles authentication, so the agent never sees raw API keys.

### Step 1: Create the config file

```bash
cp data/vault/mcp-servers.example.json data/vault/mcp-servers.json
```

### Step 2: Edit the config

Open `data/vault/mcp-servers.json` and add your servers. Each server entry looks like this:

```json
{
  "id": "unique-server-id",
  "name": "Human-readable name",
  "description": "What this API does (the agent sees this)",
  "enabled": true,
  "target_base_url": "https://api.example.com/v1",
  "auth_type": "bearer",
  "auth_value": "your-api-key-here",
  "rate_limit": "60/minute"
}
```

**Fields explained:**

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier the agent uses in `mcp_request(server_id="...")` |
| `name` | Yes | Human-readable name shown in descriptions |
| `description` | Yes | What the API does -- the LLM reads this to understand capabilities |
| `enabled` | Yes | Set to `false` to disable without deleting |
| `target_base_url` | Yes | The API's base URL (path from `mcp_request` is appended) |
| `auth_type` | Yes | One of: `bearer`, `header`, `basic`, `none` |
| `auth_value` | Depends | The API key or token (for `bearer` and `header` types) |
| `auth_header` | For `header` | Custom header name (default: `Authorization`) |
| `auth_username` | For `basic` | Username for basic auth |
| `auth_password` | For `basic` | Password for basic auth |
| `extra_headers` | No | Additional headers to send with every request |
| `rate_limit` | No | Request limit (e.g., `"60/minute"`, `"10/second"`) |

### Authentication types

**Bearer token** (most common):
```json
{
  "auth_type": "bearer",
  "auth_value": "your-api-key"
}
```
Sends: `Authorization: Bearer your-api-key`

**Custom header**:
```json
{
  "auth_type": "header",
  "auth_header": "X-API-Key",
  "auth_value": "your-api-key"
}
```
Sends: `X-API-Key: your-api-key`

**Basic auth**:
```json
{
  "auth_type": "basic",
  "auth_username": "myuser",
  "auth_password": "mypassword"
}
```
Sends: `Authorization: Basic base64(myuser:mypassword)`

**No auth** (public APIs):
```json
{
  "auth_type": "none"
}
```

### Example: Connecting to the Notion API

```json
{
  "id": "notion",
  "name": "Notion API",
  "description": "Query and update Notion databases and pages",
  "enabled": true,
  "target_base_url": "https://api.notion.com/v1",
  "auth_type": "header",
  "auth_header": "Authorization",
  "auth_value": "Bearer ntn_YOUR_NOTION_INTEGRATION_TOKEN",
  "extra_headers": {
    "Notion-Version": "2022-06-28"
  },
  "rate_limit": "60/minute"
}
```

### Step 3: Re-seed the data volume

After editing the config, copy it into the container:

```bash
docker compose cp data/vault/mcp-servers.json agent-bot-1:/agent-data/vault/mcp-servers.json
```

Or restart the containers:

```bash
docker compose restart
```

---

## Memory System

The agent's long-term memory lives in `data/memory/MEMORY.md`. This file is read on every conversation turn and included in the system prompt.

### How it works

- The agent reads MEMORY.md at the start of every interaction
- When the agent learns something important, it can update this file using the `shell` tool
- Memory survives container restarts (it lives in the Docker volume)
- You can also edit it manually and re-seed

### What to put in MEMORY.md

- Key facts the agent should always remember
- User preferences and settings
- Project status and ongoing tasks
- Important dates and deadlines

### When to update

- The agent updates memory automatically when it learns something important
- You can ask the agent: "Remember that I prefer short responses"
- You can edit the file manually for bulk updates

### Example

```markdown
# MEMORY.md -- Long-term Memory

_Last synthesis: 2025-03-25_

## Key Facts
- Project Alpha launches on April 15
- Production server runs Ubuntu 22.04
- Database backups run at 3am UTC daily

## User Preferences
- Prefers concise bullet-point responses
- Wants alerts for disk usage over 80%
- Timezone: UTC+7

## Pending Work
- Need to review deployment script by Friday
- Waiting for API access from vendor
```

---

## Docker Deployment

### Building and starting

```bash
# Build images and start containers in the background
docker compose up -d --build

# Seed the data volume (required on first run)
docker compose cp data/. agent-bot-1:/agent-data/
```

### Checking logs

```bash
# Follow all logs
docker compose logs -f

# Follow only the bot logs
docker compose logs -f agent-bot

# Follow only the proxy logs
docker compose logs -f agent-proxy
```

### Restarting after changes

If you edit files in `data/`:

```bash
# Re-seed the data volume
docker compose cp data/. agent-bot-1:/agent-data/

# Restart the bot to pick up changes
docker compose restart agent-bot
```

If you edit Python code:

```bash
# Rebuild and restart
docker compose up -d --build
```

### Stopping

```bash
# Stop containers (data volume persists)
docker compose down

# Stop and DELETE the data volume (loses memory and logs)
docker compose down -v
```

---

## LLM Configuration

AkiClaw supports any OpenAI-compatible API plus Anthropic's native Messages API. Set the configuration in your `.env` file.

### Option 1: Anthropic Claude (Direct API)

Best for: Most users. Direct connection, lowest latency.

```env
AKICLAW_API_KEY=your-anthropic-api-key-here
AKICLAW_MODEL=claude-sonnet-4-20250514
OPENROUTER_ENDPOINT=https://api.anthropic.com/v1/messages
```

### Option 2: MiniMax M2.7 (Direct API)

Best for: Cost-effective agents with 128K context window.

```env
AKICLAW_API_KEY=your-minimax-api-key-here
AKICLAW_MODEL=MiniMax-M2.7
OPENROUTER_ENDPOINT=https://api.minimax.io/v1/chat/completions
```

AkiClaw includes MiniMax-specific hardening: automatic context truncation at 80K chars, silent overflow detection (200 OK + 0 tokens), emergency compaction, and extended timeouts.

### Option 3: OpenRouter (Multi-model)

Best for: Trying different models, using non-Anthropic LLMs.

```env
AKICLAW_API_KEY=your-openrouter-api-key-here
AKICLAW_MODEL=anthropic/claude-3.5-sonnet
OPENROUTER_ENDPOINT=https://openrouter.ai/api/v1/chat/completions
```

Other models you can use via OpenRouter:
- `google/gemini-2.5-pro`
- `openai/gpt-4o`
- `meta-llama/llama-3.1-405b-instruct`
- See [openrouter.ai/models](https://openrouter.ai/models) for the full list

### Switching models

1. Edit `.env` with the new model name and endpoint
2. Restart: `docker compose restart agent-bot`

The agent-core automatically detects whether to use Anthropic Messages format or OpenAI Chat Completions format based on the endpoint URL.

---

## Security

### Authentication (deny-by-default)

The bot uses a **deny-by-default** model. If `TELEGRAM_ALLOWED_USERS` is empty or unset, **all DMs are rejected**. You must explicitly set the Telegram user IDs that are allowed to interact with the bot.

### Public Commands

Some agents need to expose specific commands to non-whitelisted users (e.g., a `/credential` command for an academy bot). Configure this with:

```env
PUBLIC_DM_COMMANDS=/credential,/join-agent
```

Non-whitelisted users can only use these exact commands. Everything else is rejected with a configurable message:

```env
DM_REJECT_MESSAGE=I only respond to /credential and /join-agent commands. Join the group for more.
```

### Shell Command Filtering

The framework blocks dangerous commands via regex patterns:
- Destructive: `rm -rf /`, `mkfs`, `dd if=`, `shutdown`, `reboot`
- Evasion: `base64 | sh`, `python -c os.system(...)`, `curl | bash`
- Docker escape: `docker run -v /:/host`

**Note:** Regex filtering is defense-in-depth, not a security boundary. The bot container should be treated as having the same trust level as the LLM. For production deployments, consider running shell commands in a nested disposable container.

### Credential Proxy

API keys are never exposed to the LLM. The credential proxy:
- Strips all auth headers from inbound requests
- Injects the correct credentials per-server from `mcp-servers.json`
- Strips auth headers from upstream responses
- Rate-limits requests per server (token-bucket)
- Logs all requests to an append-only audit trail

### Log Sanitization

Session logs automatically redact patterns that look like API keys or tokens before writing to disk.

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Bot doesn't respond to messages | Token wrong or bot not started | Check `docker compose logs agent-bot` for errors. Verify `TELEGRAM_BOT_TOKEN` in `.env` |
| "Not authorized" or silent ignore | Your Telegram ID not in allowlist | Add your numeric Telegram user ID to `TELEGRAM_ALLOWED_USERS` in `.env` |
| MCP requests return errors | Server config wrong or API key invalid | Check proxy logs: `docker compose logs agent-proxy`. Verify `mcp-servers.json` |
| "Context too long" errors | Conversation grew too large | Send `/reset` in Telegram to clear history, or reduce `max_messages` in config |
| Agent tries to use tools that don't exist | Skill descriptions are unclear | Edit skill files to be more explicit about which tool (`shell` or `mcp_request`) to use |
| Container keeps restarting | Missing environment variables | Run `docker compose logs agent-bot` and check for `KeyError` or missing env vars |
| "MCP server config not found" | mcp-servers.json not in volume | Re-seed: `docker compose cp data/. agent-bot-1:/agent-data/` |
| Bot responds but very slowly | Model is large or API is slow | Try a smaller/faster model, or check your API key has enough credits |
| "BLOCKED: potentially destructive command" | Agent tried a dangerous command | This is working as intended. Dangerous commands are blocked for safety |

### Useful commands

```bash
# Check container status
docker compose ps

# Check if proxy is healthy
docker compose exec agent-proxy curl -s http://localhost:9090/health

# List available MCP servers
docker compose exec agent-proxy curl -s http://localhost:9090/proxy/_catalog

# View today's conversation logs
docker compose exec agent-bot cat /agent-data/logs/telegram-$(date +%Y-%m-%d).txt

# Manually edit memory
docker compose exec agent-bot cat /agent-data/memory/MEMORY.md
```

---

## File Reference

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Container orchestration -- defines both services and the shared volume |
| `.env.example` | Template for environment variables (copy to `.env`) |
| `.env` | Your actual secrets (never commit this) |
| `agent-core/__init__.py` | Store factory -- creates file or Supabase conversation store |
| `agent-core/llm.py` | LLM API calls -- handles both Anthropic and OpenAI formats |
| `agent-core/conversation.py` | Message persistence, history retrieval, and compaction |
| `agent-core/tools.py` | Tool definitions and execution (shell + mcp_request) |
| `agent-core/supabase_store.py` | Optional cloud persistence via Supabase |
| `telegram-bot/Dockerfile` | Bot container image definition |
| `telegram-bot/bot.py` | Telegram polling, message handling, agent loop |
| `telegram-bot/heartbeat.py` | Scheduled proactive monitoring |
| `telegram-bot/daily_review.py` | LLM-powered daily self-assessment |
| `telegram-bot/requirements.txt` | Python dependencies for the bot |
| `proxy/Dockerfile` | Proxy container image definition |
| `proxy/main.py` | FastAPI credential-injecting reverse proxy |
| `proxy/audit.py` | JSONL request audit logging |
| `proxy/rate_limiter.py` | Token-bucket rate limiting per server |
| `proxy/requirements.txt` | Python dependencies for the proxy |
| `data/core/IDENTITY.md` | Agent name, role, authorized users, security anchor |
| `data/core/SOUL.md` | Behavioral rules, communication style, safety limits |
| `data/core/HEARTBEAT.md` | Proactive monitoring checks and escalation rules |
| `data/core/BOOTSTRAP.md` | First-run verification checklist |
| `data/skills/*.md` | Capability definitions (auto-loaded into system prompt) |
| `data/memory/MEMORY.md` | Persistent long-term memory |
| `data/vault/mcp-servers.json` | MCP server configs with credentials (never commit) |
| `data/vault/mcp-servers.example.json` | Template for MCP server configuration |

---

## License

[MIT](LICENSE) -- free to use, modify, and distribute.
