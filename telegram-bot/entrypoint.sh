#!/bin/sh
# Fix volume ownership (volumes may be owned by root from previous deployments)
DATA_DIR="${AKICLAW_HOME:-/agent-data}"
chown -R agent:agent "$DATA_DIR" 2>/dev/null || true

# Drop privileges and exec CMD as agent user
exec gosu agent "$@"
