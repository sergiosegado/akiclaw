## Skill: Platform Builder

Use this skill when asked to build, update, or deploy your web platform/dashboard.
DO NOT use for simple tasks (reading files, checking status, answering questions).

### RULE 1: PLAN FIRST — NEVER EXECUTE WITHOUT A PLAN

Before writing ANY code or running ANY build command:

1. Create your plan:
   shell: mkdir -p /{name}-data/tasks
   shell: cat > /{name}-data/tasks/current.md << 'PLANEOF'
   # Task: [title]
   Started: [date]
   Status: in_progress
   Phase: planning

   ## Steps
   1. [ ] [First step - ONE command]
   2. [ ] [Second step]
   ...
   PLANEOF

2. Tell the user your plan and WAIT for confirmation:
   "I've created a build plan with N steps. Here's the summary: [list]. Shall I proceed?"

3. Only proceed after user says yes/go/proceed.

### RULE 2: ONE STEP AT A TIME

For each step:
1. Read current.md to find your current step
2. Execute the ONE command for that step
3. Verify the result (check output, test file exists)
4. Update current.md: mark step [x] done, move to next
5. Report briefly: "Step N done: [what]. Moving to N+1."

NEVER run more than 2 shell commands without updating the task file.

### RULE 3: FAIL-STOP AT 2

If a step fails:
- First failure: Read error, adjust command, try once more
- Second failure: STOP IMMEDIATELY
  - Update current.md: mark step [!] FAILED
  - Tell user: "Step N failed twice: [error]. I've stopped. How should I fix this?"
- NEVER try a third time without user guidance

### RULE 4: WRITING CODE FILES

When creating code files via shell:
- Use cat with single-quoted heredoc: cat > file << 'EOF' ... EOF
- Maximum 60 lines per write command
- For larger files: split into sections, append with >>
- Always verify: shell: wc -l /path/to/file && head -3 /path/to/file

### RULE 5: DEPLOY WORKFLOW

After all code is written, deploy in this exact sequence:

Step A - Build image:
  shell: cd /opt/fleet/agents/{name}/web && docker build -t {name}-web:latest .

Step B - Verify image built:
  shell: docker images {name}-web:latest --format 'Size: {{.Size}}, Created: {{.CreatedAt}}'

Step C - Stop old container:
  shell: docker stop {name}-web 2>/dev/null; docker rm -f {name}-web 2>/dev/null; echo 'ready'

Step D - Start new container:
  shell: docker run -d --name {name}-web --network proxy-network \
    -v {name}-agent-data:/{name}-data:ro \
    --restart unless-stopped \
    --label traefik.enable=true \
    --label 'traefik.http.routers.{name}-web.rule=Host(`{name}.YOUR_DOMAIN.com`)' \
    --label traefik.http.routers.{name}-web.entrypoints=websecure \
    --label traefik.http.routers.{name}-web.tls.certresolver=letsencrypt \
    --label 'traefik.http.services.{name}-web-svc.loadbalancer.server.port=80' \
    {name}-web:latest

Step E - Verify running:
  shell: docker ps --filter name={name}-web --format '{{.Status}}'

Step F - Update task:
  Mark task Status: completed, Phase: done

### RULE 6: UPDATING DASHBOARD DATA

When you run backtests, research, or collect data — save results to your data volume so the dashboard auto-displays them:

- Backtest results: Save to /{name}-data/backtest/results/latest.json
- Market data: Save to /{name}-data/polycop-style/data/markets.json
- Wallet data: Save to /{name}-data/polycop-style/data/wallet_leaderboard.json
- Strategy configs: Save to /{name}-data/strategies/

The dashboard API reads these files automatically. No rebuild needed for data updates.

### RULE 7: ANTI-LOOP SAFETY

- If you've run 10+ shell commands and completed 0 steps: STOP and report
- If last 3 shell commands returned empty output: STOP and report
- If you're about to write a file you already wrote: STOP and check why
- NEVER call shell with an empty command
- NEVER call shell with only whitespace

### RESUMING A TASK

If /{name}-data/tasks/current.md exists with Status: in_progress:
1. Read it first
2. Find the step marked [CURRENT] or the first [ ] step
3. Tell user: "I found an in-progress task: [title]. I'm on step N. Continue?"
4. Wait for confirmation
