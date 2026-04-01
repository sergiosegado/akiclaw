"""
Unified tool definitions and execution for AkiClaw agent.

Both dashboard (akc-api) and Telegram bot use these same tools.
"""

import json
import os
import re
import subprocess
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("akc.tools")

# Block destructive and evasion commands
DANGEROUS_PATTERNS = re.compile(
    r"rm\s+-rf\s+/(?!agent-data/)|mkfs|dd\s+if=|shutdown|reboot|>\s*/dev/sd|"
    r"chmod\s+-R\s+777\s+/|chown\s+-R.*\s+/(?!agent)|"
    r"base64\s.*\|\s*(sh|bash|zsh)|"
    r"python[23]?\s+-c\s+.*\b(os\.system|subprocess|exec|eval)\b|"
    r"curl\s+.*\|\s*(sh|bash|zsh)|wget\s+.*\|\s*(sh|bash|zsh)|"
    r"docker\s+run\s+.*-v\s+/:/",
    re.IGNORECASE,
)

# Additional patterns blocked when agent has restricted shell
RESTRICTED_SHELL_PATTERNS = re.compile(
    r"docker\s+(rm|stop|kill|rmi|volume\s+rm|system\s+prune)|"
    r"rm\s+-rf|rm\s+-r\s+/|"
    r"cat\s+/(?!.*-data/)|ls\s+/(?!.*-data/)|"
    r"kubectl|systemctl|apt|pip\s+install|npm\s+install",
    re.IGNORECASE,
)


def load_permissions(akc_home: Path) -> dict:
    """Load agent permissions from permissions.json in the data volume."""
    perms_file = akc_home / "permissions.json"
    defaults = {
        "shellEnabled": True,
        "shellRestricted": False,  # If True, only allows commands within agent's data dir
        "dockerEnabled": False,    # Can run docker commands on other containers
        "destructiveRequiresConfirmation": True,
    }
    if perms_file.exists():
        try:
            saved = json.loads(perms_file.read_text())
            defaults.update(saved)
        except Exception:
            pass
    return defaults


def _load_mcp_servers_description(akc_home: Path) -> str:
    """Build a dynamic description of available MCP servers."""
    try:
        mcp_file = akc_home / "vault" / "mcp-servers.json"
        if not mcp_file.exists():
            return "No MCP servers configured."
        data = json.loads(mcp_file.read_text())
        if isinstance(data, list):
            servers = data
        else:
            servers = [{"id": k, **v} for k, v in data.items()]
        enabled = [s for s in servers if s.get("enabled")]
        if not enabled:
            return "No MCP servers are currently enabled."
        lines = ["Available MCP servers (use mcp_request tool to call them):"]
        for s in enabled:
            lines.append(
                f'  - server_id: "{s.get("id", "unknown")}" | {s.get("name", "?")}:'
                f' {s.get("description", "N/A")} | base: {s.get("target_base_url", "N/A")}'
            )
        return "\n".join(lines)
    except Exception:
        return "MCP servers unavailable."


def _mcp_tool_params():
    return {
        "type": "object",
        "properties": {
            "server_id": {"type": "string", "description": "The MCP server ID to call"},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "description": "HTTP method",
            },
            "path": {
                "type": "string",
                "description": "Path to append to the server's base URL (e.g. '/search?q=hello')",
            },
            "body": {
                "type": "string",
                "description": "JSON request body (for POST/PUT/PATCH)",
            },
        },
        "required": ["server_id", "method", "path"],
    }


def build_openai_tools(akc_home: Path) -> list[dict]:
    """Build OpenAI-format tool definitions based on agent permissions."""
    perms = load_permissions(akc_home)
    tools = []

    if perms.get("shellEnabled", True):
        shell_desc = "Execute a shell command on the server."
        if perms.get("shellRestricted", False):
            agent_name = akc_home.name.replace("-data", "")
            shell_desc += (
                f"\nRESTRICTED MODE: You can only access your own data directory (/{akc_home.name}/)."
                f"\nYou CANNOT: run docker commands on other agents, access other directories, install packages."
                f"\nYou CAN: read/write your own files, use curl, run python scripts within your data dir."
            )
        if perms.get("destructiveRequiresConfirmation", True):
            shell_desc += (
                "\nSAFETY: NEVER delete, remove, or destroy anything without asking the user for confirmation first."
                "\nAlways explain what you're about to do and wait for 'yes' before executing destructive commands."
            )
        tools.append({
            "type": "function",
            "function": {
                "name": "shell",
                "description": shell_desc,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The shell command to execute"}
                    },
                    "required": ["command"],
                },
            },
        })

    mcp_desc = _load_mcp_servers_description(akc_home)
    if "server_id" in mcp_desc:
        tools.append({
            "type": "function",
            "function": {
                "name": "mcp_request",
                "description": (
                    "Make an HTTP request to an MCP server through the credential proxy. "
                    f"Auth is injected automatically.\n{mcp_desc}"
                ),
                "parameters": _mcp_tool_params(),
            },
        })
    return tools


def build_anthropic_tools(akc_home: Path) -> list[dict]:
    """Build Anthropic-format tool definitions."""
    tools = [
        {
            "name": "shell",
            "description": "Execute a shell command on the host system",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"],
            },
        }
    ]
    mcp_desc = _load_mcp_servers_description(akc_home)
    if "server_id" in mcp_desc:
        tools.append(
            {
                "name": "mcp_request",
                "description": (
                    "Make an HTTP request to an MCP server through the credential proxy. "
                    f"Auth is injected automatically.\n{mcp_desc}"
                ),
                "input_schema": _mcp_tool_params(),
            }
        )
    return tools


def run_shell(command: str, timeout: int = 60, akc_home: Path = None) -> str:
    """Execute a shell command with permission enforcement."""
    # Always block catastrophic commands
    if DANGEROUS_PATTERNS.search(command):
        return "BLOCKED: destructive command detected. Ask for human confirmation before retrying."

    # Check permissions
    home = akc_home or Path(os.environ.get("AKICLAW_HOME", "/agent-data"))
    perms = load_permissions(home)

    if not perms.get("shellEnabled", True):
        return "BLOCKED: Shell access is disabled for this agent. Use mcp_request for external APIs."

    if perms.get("shellRestricted", False):
        # In restricted mode, block access to other agents and system dirs
        if RESTRICTED_SHELL_PATTERNS.search(command):
            return (
                "BLOCKED: This command is not allowed in restricted shell mode. "
                "You can only access your own data directory. "
                "Ask the owner to enable full shell access if needed."
            )

    log.info("SHELL: %s", command[:200])
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = (result.stdout + result.stderr).strip()
        if len(output) > 16000:
            output = output[:8000] + "\n...[truncated]...\n" + output[-8000:]
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"ERROR: timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


async def run_mcp_request(
    server_id: str, method: str, path: str, body: str = None
) -> str:
    """Route a request through the credential proxy for auth injection."""
    proxy_url = os.environ.get("AKC_PROXY_URL", "http://agent-proxy:9090")
    url = f"{proxy_url}/proxy/{server_id}/{path.lstrip('/')}"
    headers = {"Accept-Encoding": "identity"}
    if body:
        headers["Content-Type"] = "application/json"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body.encode() if body else None,
            )
            text = resp.text
            if len(text) > 16000:
                text = text[:8000] + "\n...[truncated]...\n" + text[-8000:]
            return f"HTTP {resp.status_code}\n{text}"
    except httpx.TimeoutException:
        return "ERROR: MCP request timed out"
    except Exception as e:
        return f"ERROR: MCP request failed: {e}"


async def execute_tool(tool_name: str, args: dict) -> str:
    """Execute a tool call and return the output string."""
    if tool_name == "mcp_request":
        return await run_mcp_request(
            server_id=args.get("server_id", ""),
            method=args.get("method", "GET"),
            path=args.get("path", ""),
            body=args.get("body"),
        )
    elif tool_name == "shell":
        return run_shell(args.get("command", ""))
    else:
        return f"ERROR: Unknown tool: {tool_name}"
