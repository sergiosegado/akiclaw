FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl jq git docker.io openssh-client gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY telegram-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared agent-core module
COPY agent-core/ /app/agent_core/

# Copy bot code
COPY telegram-bot/ /app/

# Create non-root user
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

# Entrypoint: fix volume ownership then drop to agent user
COPY telegram-bot/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-u", "bot.py"]
