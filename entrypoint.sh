#!/bin/sh
set -eu

CONFIG_DIR="/root/.openclaw"
CONFIG_FILE="$CONFIG_DIR/openclaw.json"

WORKSPACE="/workspace"
MEMORY_DIR="$WORKSPACE/memory"
AGENTS_FILE="$WORKSPACE/AGENTS.md"
MEMORY_FILE="$WORKSPACE/MEMORY.md"

mkdir -p "$CONFIG_DIR" "$WORKSPACE" "$MEMORY_DIR"

: "${PLATFORM:=anthropic}"
: "${API_KEY:?API_KEY is required}"
: "${LLM_MODEL:?LLM_MODEL is required}"

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

export "${ENV_KEY}=${API_KEY}"

TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

if [ -n "$TG_TOKEN" ]; then
  TELEGRAM_BLOCK=$(cat <<EOF
  "channels": {
    "telegram": {
      "enabled": true,
      "dmPolicy": "open",
      "allowFrom": ["*"],
      "streaming": "partial",
      "accounts": {
        "default": {
          "botToken": "${TG_TOKEN}"
        }
      }
    }
  },
EOF
)
else
  TELEGRAM_BLOCK=$(cat <<EOF
  "channels": {
    "telegram": {
      "enabled": false
    }
  },
EOF
)
fi

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
${TELEGRAM_BLOCK}
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
echo "[entrypoint] Telegram enabled: $( [ -n "$TG_TOKEN" ] && echo yes || echo no )"
echo "[entrypoint] Config written to $CONFIG_FILE"

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
- User preferences
- Ongoing projects and their status
- Decisions made and why
- Recurring tasks and schedules
- Important personal context

## Browser Usage
- Before browser actions, start the browser runtime if needed.
- Stop it when done to save resources.
- Confirm before submitting forms or purchases.

## Communication Style
- Be concise and direct in Telegram.
- Use bullet points for lists.
- Ask for clarification when needed.
AGENTS
fi

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

exec openclaw gateway