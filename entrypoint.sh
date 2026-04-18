#!/bin/sh
set -eu

CONFIG_DIR="/root/.openclaw"
CONFIG_FILE="$CONFIG_DIR/openclaw.json"

# Один и тот же workspace должен:
# 1) быть volume в docker_manager.py
# 2) быть прописан в конфиге OpenClaw
WORKSPACE="/workspace"
MEMORY_DIR="$WORKSPACE/memory"
AGENTS_FILE="$WORKSPACE/AGENTS.md"
MEMORY_FILE="$WORKSPACE/MEMORY.md"

mkdir -p "$CONFIG_DIR" "$WORKSPACE" "$MEMORY_DIR"

# Валидация обязательных переменных
: "${PLATFORM:=anthropic}"
: "${API_KEY:?API_KEY is required}"
: "${LLM_MODEL:?LLM_MODEL is required}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"

# Определяем env-переменную для ключа провайдера
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

# Экспортируем ключ в env процесса
export "${ENV_KEY}=${API_KEY}"

# Генерируем конфиг
cat > "$CONFIG_FILE" <<CONF
{
  "gateway": {
    "mode": "local"
  },
  "env": {
    "${ENV_KEY}": "${API_KEY}"
  },
  "agents": {
    "defaults": {
      "workspace": "${WORKSPACE}",
      "model": {
        "primary": "${LLM_MODEL}"
      },
      "timeoutSeconds": 120
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "dmPolicy": "open",
      "allowFrom": ["*"],
      "streaming": "partial",
      "accounts": {
        "default": {
          "botToken": "${TELEGRAM_BOT_TOKEN}"
        }
      }
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
echo "[entrypoint] Workspace: $WORKSPACE"
echo "[entrypoint] Config written to $CONFIG_FILE"

# AGENTS.md — только при первом запуске
if [ ! -f "$AGENTS_FILE" ]; then
cat > "$AGENTS_FILE" <<'AGENTS'
# Personal Assistant Instructions

You are a personal AI assistant. Your primary goal is to help the user with any tasks they request.

## Memory Rules
- Before responding to any request, always check whether there is relevant stored context.
- After every conversation where something important is shared, write it to memory files.
- Save user preferences, decisions, and important facts to MEMORY.md.
- Write daily activity logs to memory/YYYY-MM-DD.md.

## What to Remember
- User preferences (languages, tools, time zones, communication style)
- Ongoing projects and their status
- Decisions made and why
- Recurring tasks and schedules
- Personal context the user shares

## Browser Usage
- You have access to a real Chromium browser.
- Before any browser action, start the browser runtime if it is not running.
- After browser tasks are done, stop it to free resources.
- Use it when the user asks to browse, search the web, fill forms, or automate web tasks.
- Always confirm with the user before submitting forms or making purchases.

## Communication Style
- Be concise and direct in Telegram.
- Use bullet points for lists.
- Ask for clarification when a request is ambiguous.
AGENTS
fi

# MEMORY.md — только при первом запуске
if [ ! -f "$MEMORY_FILE" ]; then
cat > "$MEMORY_FILE" <<'MEMORY'
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

exec openclaw gateway