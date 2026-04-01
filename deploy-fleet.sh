#!/bin/bash
# Deploy akiclaw framework changes to all agents on OVH VPS
# Run from the akiclaw/ directory after editing code
set -e

echo "=== Uploading framework code ==="
scp agent-core/*.py ovh-vps:/opt/akiclaw/agent-core/
scp telegram-bot/*.py ovh-vps:/opt/akiclaw/telegram-bot/
scp telegram-bot/Dockerfile ovh-vps:/opt/akiclaw/telegram-bot/
scp telegram-bot/entrypoint.sh ovh-vps:/opt/akiclaw/telegram-bot/
scp proxy/*.py ovh-vps:/opt/akiclaw/proxy/ 2>/dev/null || true

echo "=== Rebuilding and restarting all agents ==="
ssh ovh-vps /opt/akiclaw/deploy.sh

echo "=== Fleet deployed ==="
