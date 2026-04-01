# Building AI Agents with Claude Code

A practical guide for building, deploying, and running autonomous AI agents powered by Claude.

---

## What is a Claude Code Agent?

A Claude Code agent is an autonomous program that:
1. Receives a task (via chat, API, or schedule)
2. Thinks about what to do
3. Uses tools (shell, files, APIs) to act
4. Loops until the task is complete

Claude doesn't just generate text — it executes. It reads files, runs commands, calls APIs, and makes decisions about what to do next. Your job is to give it the right context, tools, and boundaries.

---

## Three Ways to Build Claude Agents

### 1. Claude Agent SDK (Official Library)

Anthropic's official SDK gives you the same tool loop that powers Claude Code, as a Python or TypeScript library.

**Install:**
```bash
# Python
pip install claude-agent-sdk

# TypeScript
npm install @anthropic-ai/claude-agent-sdk
```

**Simple agent (Python):**
```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    async for message in query(
        prompt="Find all TODO comments in this project and create a summary",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "Write"],
        ),
    ):
        if hasattr(message, "text"):
            print(message.text)

asyncio.run(main())
```

**What happens:** Claude searches files with Glob, reads them with Read, finds TODOs with Grep, and writes a summary with Write. All autonomous — you just defined the task and tools.

**Simple agent (TypeScript):**
```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Find all TODO comments and create a summary",
  options: { allowedTools: ["Read", "Glob", "Grep", "Write"] }
})) {
  if ("text" in message) console.log(message.text);
}
```

**Built-in tools:**

| Tool | What it does |
|------|-------------|
| Read | Read any file |
| Write | Create new files |
| Edit | Modify existing files |
| Bash | Run shell commands |
| Glob | Find files by pattern |
| Grep | Search with regex |
| WebSearch | Search the web |
| WebFetch | Fetch and parse web pages |

### 2. Claude Code CLI (Headless Mode)

Run Claude Code from scripts without the interactive terminal.

```bash
# One-shot task
claude -p "Summarize this project"

# With specific tools
claude -p "Run tests and fix failures" --allowedTools "Bash,Read,Edit"

# JSON output for piping
claude -p "List all functions" --output-format json

# Continue a conversation
claude -p "Start analyzing auth.py"
claude -p "Now fix the bug you found" --continue

# Fast mode (no hooks, skills, or MCP — CI-friendly)
claude --bare -p "Count lines of code" --allowedTools "Bash"
```

### 3. AkiClaw Framework (Docker + Telegram)

A lightweight Python framework that wraps the agent loop in Docker with Telegram chat.

```bash
git clone https://github.com/sergiosegado/akiclaw.git my-agent
cd my-agent
cp .env.example .env  # Add your tokens
docker compose up -d --build
```

Your agent runs in Docker, chats via Telegram, and uses a credential proxy for secure API access. See the [AkiClaw README](https://github.com/sergiosegado/akiclaw) for full setup.

---

## Core Concepts

### The Agent Loop

Every Claude agent follows the same pattern:

```
Receive Task
    |
    v
Build System Prompt
  (identity + skills + memory + context)
    |
    v
Call Claude LLM
    |
    +---> Text Response ---> Return to user
    |
    +---> Tool Call ---> Execute tool
                            |
                            v
                        Get result
                            |
                            v
                        Feed back to Claude
                            |
                            v
                        Call Claude again (loop)
```

The loop continues until Claude produces a text response (no more tool calls) or hits the turn limit.

### CLAUDE.md — Agent Configuration

A CLAUDE.md file is a markdown document that becomes part of Claude's system prompt. It tells the agent who it is, what it knows, and how to behave.

```markdown
# My Agent

## Identity
You are a DevOps assistant that monitors servers and fixes issues.

## Tools Available
- Shell: run any command
- Notion API: query project databases
- Telegram: send alerts

## Rules
- Always check disk space before deploying
- Never run rm -rf without confirmation
- Log all actions to /var/log/agent.log

## Context
Server: Ubuntu 22.04 on AWS
Services: nginx, PostgreSQL, Redis
Monitoring: Grafana at https://grafana.example.com
```

**Where to put it:**
- Project: `./CLAUDE.md` or `.claude/CLAUDE.md`
- Personal (all projects): `~/.claude/CLAUDE.md`
- Per-directory: `packages/api/CLAUDE.md`

### Skills — Teachable Capabilities

Skills are markdown files that add capabilities to your agent. Drop them in a folder, they become part of the system prompt.

```markdown
## Skill: Deploy to Production

When asked to deploy:
1. Run tests: `npm test`
2. If tests pass, build: `npm run build`
3. Deploy: `rsync -avz dist/ server:/app/`
4. Verify: `curl -s https://app.example.com/health`
5. If health check fails, rollback: `ssh server 'cd /app && git checkout HEAD~1'`
6. Notify team via Telegram
```

The agent reads this and knows HOW to deploy. You teach by writing, not by coding.

**In AkiClaw:** put skills in `data/skills/*.md`
**In Claude Code:** put skills in `.claude/skills/skill-name/SKILL.md`

### Memory — Persistent Context

Memory survives restarts. The agent reads it every session.

```markdown
# Memory

## Learned
- Production server rebooted on March 15 (kernel update)
- Database backup runs at 3am UTC
- User prefers Slack over email for notifications

## Active Tasks
- Monitor API latency (threshold: 500ms)
- Weekly security scan every Monday 6am

## Key Decisions
- Switched from MySQL to PostgreSQL on March 10
- API rate limit set to 100 req/min
```

**In AkiClaw:** `data/memory/MEMORY.md`
**In Claude Code:** `~/.claude/projects/project-name/memory/`

### MCP — External Tool Connections

MCP (Model Context Protocol) connects your agent to external services without exposing credentials.

**Configuration (.mcp.json):**
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_your_token"
      }
    },
    "postgres": {
      "command": "npx",
      "args": ["@modelcontextprotocol/server-postgres"],
      "env": {
        "DATABASE_URL": "postgresql://user:pass@localhost/db"
      }
    }
  }
}
```

Claude calls these tools as `mcp__github__search_repositories` or `mcp__postgres__query`. The credentials are handled by the MCP server process, never visible to the agent.

**In AkiClaw:** credentials go in `data/vault/mcp-servers.json` and are injected by the proxy container.

---

## Building Your First Agent

### Option A: Claude Agent SDK (Programmatic)

```python
# agent.py
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def code_reviewer():
    """Agent that reviews code and suggests improvements."""
    async for message in query(
        prompt="""Review all Python files in this project for:
        1. Security vulnerabilities
        2. Performance issues
        3. Code style problems
        Write a report to REVIEW.md""",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "Write"],
            permission_mode="acceptEdits",
        ),
    ):
        if hasattr(message, "result"):
            print(f"Done: {message.result}")

asyncio.run(code_reviewer())
```

Run it: `python agent.py`

### Option B: Claude Code CLI (Script)

```bash
#!/bin/bash
# review.sh — Code review agent

claude --bare -p "
Review all Python files for security issues, performance problems,
and code style. Write findings to REVIEW.md.
" --allowedTools "Read,Glob,Grep,Write" --output-format json
```

Run it: `bash review.sh`

### Option C: AkiClaw (Docker + Telegram)

```bash
# 1. Clone
git clone https://github.com/sergiosegado/akiclaw.git my-agent
cd my-agent

# 2. Configure
cp .env.example .env
# Edit .env: add TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, TELEGRAM_ALLOWED_USERS

# 3. Customize identity
cat > data/core/IDENTITY.md << 'EOF'
## Identity — CodeBot

name: CodeBot
emoji: 🔍

### Who You Are
You are CodeBot — a code review assistant that lives in Telegram.
You analyze code, find bugs, and suggest improvements.

### Authorized Users
- [YOUR_NAME] (Telegram ID: [YOUR_ID])
EOF

# 4. Deploy
docker compose up -d --build
docker compose cp data/. agent-bot:/agent-data/

# 5. Chat on Telegram!
```

---

## Custom Tools with the Agent SDK

Create tools that Claude can call autonomously.

```python
from claude_agent_sdk import tool, create_sdk_mcp_server, query, ClaudeAgentOptions

# Define a custom tool
@tool(
    "check_server_health",
    "Check if a server is responding and return status",
    {"url": str, "expected_status": int}
)
async def check_server_health(args):
    import httpx
    try:
        resp = await httpx.AsyncClient().get(args["url"], timeout=10)
        ok = resp.status_code == args["expected_status"]
        return {
            "content": [{
                "type": "text",
                "text": f"{'OK' if ok else 'FAIL'}: {args['url']} returned {resp.status_code}"
            }]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"ERROR: {e}"}],
            "is_error": True
        }

# Package as MCP server
health_server = create_sdk_mcp_server(
    name="health",
    version="1.0.0",
    tools=[check_server_health]
)

# Use in agent
async def monitor():
    async for msg in query(
        prompt="Check all our servers: api.example.com, web.example.com, db.example.com",
        options=ClaudeAgentOptions(
            mcp_servers={"health": health_server},
            allowed_tools=["mcp__health__check_server_health"]
        ),
    ):
        if hasattr(msg, "text"):
            print(msg.text)
```

---

## Hooks — Automation and Safety

Hooks run before or after Claude uses tools.

**Block dangerous commands (.claude/settings.json):**
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{
          "type": "command",
          "command": "bash -c 'CMD=$(cat | jq -r .tool_input.command); echo \"$CMD\" | grep -qE \"rm -rf /|drop table|shutdown\" && { echo \"Blocked: dangerous command\" >&2; exit 2; } || exit 0'"
        }]
      }
    ]
  }
}
```

**Log all file changes:**
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{
          "type": "command",
          "command": "bash -c 'echo \"$(date): $(cat | jq -r .tool_input.file_path)\" >> .claude/audit.log'"
        }]
      }
    ]
  }
}
```

**Auto-format after edits:**
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{
          "type": "command",
          "command": "bash -c 'cat | jq -r .tool_input.file_path | xargs npx prettier --write 2>/dev/null || true'"
        }]
      }
    ]
  }
}
```

---

## Sessions — Multi-Turn Conversations

Keep context across multiple interactions.

**Python:**
```python
from claude_agent_sdk import query, ClaudeAgentOptions

async def investigation():
    session_id = None

    # Step 1: Analyze
    async for msg in query(
        prompt="Read auth.py and find potential security issues",
        options=ClaudeAgentOptions(allowed_tools=["Read", "Glob"]),
    ):
        if hasattr(msg, "session_id"):
            session_id = msg.session_id

    # Step 2: Fix (same context, remembers what it found)
    async for msg in query(
        prompt="Fix the issues you found",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Read", "Edit"],
            permission_mode="acceptEdits",
        ),
    ):
        if hasattr(msg, "result"):
            print(msg.result)
```

**CLI:**
```bash
# Start investigation
claude -p "Analyze the authentication system" --output-format json > /tmp/step1.json
SESSION=$(jq -r '.session_id' /tmp/step1.json)

# Continue with context
claude -p "Now write tests for the issues you found" --resume "$SESSION"
```

---

## Subagents — Task Delegation

Spawn specialized agents for focused subtasks.

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

async def project_review():
    async for msg in query(
        prompt="Do a full project review: security, performance, and code quality",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "Agent"],
            agents={
                "security-reviewer": AgentDefinition(
                    description="Security vulnerability scanner",
                    prompt="Find OWASP Top 10 issues, SQL injection, XSS, hardcoded secrets",
                    tools=["Read", "Glob", "Grep"],
                ),
                "performance-reviewer": AgentDefinition(
                    description="Performance optimization expert",
                    prompt="Find N+1 queries, memory leaks, slow algorithms, missing indexes",
                    tools=["Read", "Glob", "Grep"],
                ),
                "style-reviewer": AgentDefinition(
                    description="Code style and best practices",
                    prompt="Check naming conventions, function length, error handling, documentation",
                    tools=["Read", "Glob", "Grep"],
                ),
            }
        ),
    ):
        if hasattr(msg, "text"):
            print(msg.text)
```

Claude orchestrates the three subagents, merges their findings, and produces a unified report.

---

## Deploying in Docker

### Dockerfile for Claude Agent SDK

```dockerfile
FROM python:3.12-slim

RUN pip install claude-agent-sdk httpx

WORKDIR /app
COPY agent.py .
COPY .claude/ .claude/

ENV ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

CMD ["python", "agent.py"]
```

### Dockerfile for Claude Code CLI

```dockerfile
FROM node:20-slim

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /workspace
COPY . .

ENV ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

CMD ["claude", "--bare", "-p", "Run the monitoring task", "--allowedTools", "Read,Bash,Grep"]
```

### docker-compose.yml

```yaml
services:
  agent:
    build: .
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - agent-data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
    restart: unless-stopped

volumes:
  agent-data:
```

---

## Security Best Practices

1. **Never expose API keys to the agent** — use MCP servers or a credential proxy
2. **Use permission modes** — `acceptEdits` for file changes, `dontAsk` to deny unlisted tools
3. **Block dangerous commands** — hooks with `exit 2` to block before execution
4. **Allowlist tools** — only give the tools the agent actually needs
5. **Audit everything** — PostToolUse hooks log every action
6. **Docker isolation** — agent runs in a container, not on bare metal
7. **User allowlists** — only authorized users can interact (Telegram ID check)

---

## Comparison: When to Use What

| Approach | Best For | Setup Time | Flexibility |
|----------|----------|------------|-------------|
| **Agent SDK** | Custom applications, pipelines, integrations | 5 min | Maximum |
| **Claude Code CLI** | Scripts, CI/CD, one-shot tasks | 1 min | Medium |
| **AkiClaw** | Telegram bots, persistent agents, team tools | 15 min | High |
| **CLAUDE.md only** | Project context, team conventions | 1 min | Low |

---

## Challenge Ideas for Students

1. **Build a Telegram bot** that monitors a GitHub repo and reports new issues
2. **Create a code review agent** that runs on every PR via GitHub Actions
3. **Build a research agent** that searches the web and writes summaries
4. **Create a DevOps agent** that monitors server health and auto-fixes common issues
5. **Build a data pipeline agent** that fetches, transforms, and stores data from APIs
6. **Create a study buddy** that quizzes you on topics from your notes
7. **Build a meeting summarizer** that processes transcripts and extracts action items

---

## Resources

- [Claude Agent SDK Docs](https://docs.anthropic.com/en/docs/agent-sdk)
- [Claude Code Docs](https://docs.anthropic.com/en/docs/claude-code)
- [MCP Protocol](https://modelcontextprotocol.io)
- [AkiClaw Framework](https://github.com/sergiosegado/akiclaw)
- [Original SubZeroClaw (C)](https://github.com/jmlago/subzeroclaw)

---

*Build agents that act, not just talk. Start small, iterate fast, deploy in Docker.*
