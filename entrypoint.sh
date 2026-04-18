#!/bin/sh
set -e

CONFIG_DIR="/root/.openclaw"
CONFIG_FILE="$CONFIG_DIR/openclaw.json"
WORKSPACE="/workspace"
MEMORY_DIR="$WORKSPACE/memory"
AGENTS_FILE="$WORKSPACE/AGENTS.md"
MEMORY_FILE="$WORKSPACE/MEMORY.md"

mkdir -p "$CONFIG_DIR" "$MEMORY_DIR"

# Определяем env-переменную для ключа в зависимости от платформы
case "$PLATFORM" in
  openrouter)
    ENV_KEY="OPENROUTER_API_KEY"
    ;;
  openai)
    ENV_KEY="OPENAI_API_KEY"
    ;;
  anthropic|*)
    ENV_KEY="ANTHROPIC_API_KEY"
    ;;
esac

# Генерируем конфиг
cat > "$CONFIG_FILE" << CONF
{
  "env": {
    "${ENV_KEY}": "${API_KEY}"
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "${LLM_MODEL}"
      },
      "timeoutSeconds": 120
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "${TELEGRAM_BOT_TOKEN}",
      "dmPolicy": "open",
      "allowFrom": ["*"],
      "streamMode": "partial"
    }
  },
  "browser": {
    "enabled": false,
    "executablePath": "/usr/bin/chromium",
    "headless": true,
    "noSandbox": true
  }
}
CONF

echo "[entrypoint] Platform: $PLATFORM"
echo "[entrypoint] Model: $LLM_MODEL"
echo "[entrypoint] Config written to $CONFIG_FILE"

# AGENTS.md — только при первом запуске
if [ ! -f "$AGENTS_FILE" ]; then
cat > "$AGENTS_FILE" << 'AGENTS'
# Personal Assistant Instructions

You are a personal AI assistant. Your primary goal is to help the user with any tasks they request.

## Memory Rules
- Before responding to any request, ALWAYS call memory_search to check if you have relevant stored context
- After every conversation where something important is shared, write it to memory files
- Save user preferences, decisions, and important facts to MEMORY.md
- Write daily activity logs to memory/YYYY-MM-DD.md

## What to Remember
- User preferences (languages, tools, time zones, communication style)
- Ongoing projects and their status
- Decisions made and why
- Recurring tasks and schedules
- Personal context the user shares

## Browser Usage
- You have access to a real Chromium browser
- **IMPORTANT:** Before ANY browser action, always run `openclaw browser start` first
  (the browser is not running by default to save resources)
- After browser tasks are done, run `openclaw browser stop` to free resources
- Use it when the user asks to browse, search the web, fill forms, or automate web tasks
- Always confirm with the user before submitting forms or making purchases

## Communication Style
- Be concise and direct via Telegram
- Use bullet points for lists
- Ask for clarification when a request is ambiguous
AGENTS
fi

# MEMORY.md — только при первом запуске
if [ ! -f "$MEMORY_FILE" ]; then
cat > "$MEMORY_FILE" << 'MEMORY'
# Long-term Memory

## User Preferences
(will be filled as I learn about the user)

## Ongoing Projects
(will be filled as projects are discussed)

## Important Facts
(will be filled over time)
MEMORY
fi

echo "[entrypoint] Starting OpenClaw gateway..."
exec openclaw gateway start
