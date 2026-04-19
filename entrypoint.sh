#!/bin/sh
set -eu

CONFIG_DIR="/root/.openclaw"
CONFIG_FILE="$CONFIG_DIR/openclaw.json"

WORKSPACE="/workspace"
MEMORY_DIR="$WORKSPACE/memory"
AGENTS_FILE="$WORKSPACE/AGENTS.md"
MEMORY_FILE="$WORKSPACE/MEMORY.md"

mkdir -p "$CONFIG_DIR" "$WORKSPACE" "$MEMORY_DIR"

: "${PLATFORM:=openrouter}"
: "${API_KEY:?API_KEY is required}"
: "${LLM_MODEL:?LLM_MODEL is required}"

case "$PLATFORM" in
  openrouter)
    ENV_KEY="OPENROUTER_API_KEY"
    ;;
  openai)
    ENV_KEY="OPENAI_API_KEY"
    ;;
  anthropic)
    ENV_KEY="ANTHROPIC_API_KEY"
    ;;
  *)
    echo "[entrypoint] Unsupported PLATFORM: $PLATFORM" >&2
    exit 1
    ;;
esac

# Важно: ключ нужен и в конфиге OpenClaw, и в окружении самого процесса
export "${ENV_KEY}=${API_KEY}"

TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

if [ -n "$TG_TOKEN" ]; then
  TELEGRAM_BLOCK=$(cat <<EOF
  "channels": {
    "telegram": {
      "enabled": true,
      "dmPolicy": "open",
      "allowFrom": ["*"],
      "streaming": "off",
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

cat > "$CONFIG_FILE" <<EOF
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
EOF

echo "[entrypoint] Platform: $PLATFORM"
echo "[entrypoint] Model: $LLM_MODEL"
echo "[entrypoint] Workspace: $WORKSPACE"
echo "[entrypoint] Provider env key: $ENV_KEY"
echo "[entrypoint] Telegram enabled: $( [ -n "$TG_TOKEN" ] && echo yes || echo no )"
echo "[entrypoint] Config written to $CONFIG_FILE"

if [ ! -f "$AGENTS_FILE" ]; then
cat > "$AGENTS_FILE" <<'EOF'
# Personal Assistant Instructions

You are a personal AI assistant. Your primary goal is to help the user with any tasks they request.

## Memory Rules
- Before responding, check whether there is relevant stored context.
- After important conversations, write useful facts to memory files.
- Save long-term facts to MEMORY.md.
- Save daily notes to memory/YYYY-MM-DD.md.

## What to Remember
- User preferences
- Ongoing projects and status
- Decisions and rationale
- Repeating tasks
- Useful personal context

## Browser Usage
- Start browser runtime only when needed.
- Stop it after use to save resources.
- Confirm before submitting forms or purchases.

## Communication Style
- Be concise and direct in Telegram.
- Use bullet points for lists.
- Ask clarifying questions when needed.
EOF
fi

if [ ! -f "$MEMORY_FILE" ]; then
cat > "$MEMORY_FILE" <<'EOF'
# Long-term Memory

## User Preferences
(will be filled as I learn about the user)

## Ongoing Projects
(will be filled as projects are discussed)

## Important Facts
(will be filled over time)
EOF
fi

exec openclaw gateway