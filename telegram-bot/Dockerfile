FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl jq git docker.io openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY telegram-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared agent-core module
COPY agent-core/ /app/agent_core/

# Copy bot code
COPY telegram-bot/ /app/

# Run as non-root user
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent
RUN mkdir -p /agent-data/logs && chown -R agent:agent /agent-data
USER agent

CMD ["python", "-u", "bot.py"]
