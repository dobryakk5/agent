#!/bin/sh
set -eu

CONFIG_DIR="/root/.openclaw"
CONFIG_FILE="$CONFIG_DIR/openclaw.json"

WORKSPACE="/workspace"
MEMORY_DIR="$WORKSPACE/memory"
AGENTS_FILE="$WORKSPACE/AGENTS.md"
MEMORY_FILE="$WORKSPACE/MEMORY.md"

mkdir -p "$CONFIG_DIR" "$WORKSPACE" "$MEMORY_DIR" "/run/secrets/user" "/run/secrets/app"

: "${PLATFORM:=openrouter}"
: "${API_KEY:?API_KEY is required}"
: "${LLM_MODEL:?LLM_MODEL is required}"
: "${GATEWAY_AUTH_TOKEN:?GATEWAY_AUTH_TOKEN is required}"
: "${GOOGLE_OAUTH_JSON_PATH:=/run/secrets/app/google-oauth.json}"
: "${GOOGLE_TOKENS_JSON_PATH:=/run/secrets/user/google-tokens.json}"

case "$PLATFORM" in
  openrouter) ENV_KEY="OPENROUTER_API_KEY" ;;
  openai)     ENV_KEY="OPENAI_API_KEY"     ;;
  anthropic)  ENV_KEY="ANTHROPIC_API_KEY"  ;;
  *)
    echo "[entrypoint] Unsupported PLATFORM: $PLATFORM" >&2
    exit 1
    ;;
esac

export "${ENV_KEY}=${API_KEY}"

# Generate openclaw.json entirely in Python so that API_KEY, LLM_MODEL,
# GATEWAY_AUTH_TOKEN and other values cannot inject into the JSON structure
# regardless of what characters they contain.
# Google Workspace plugin patch is done in the same pass.
python3 - <<'PY'
import json, os

platform           = os.environ["PLATFORM"]
api_key            = os.environ["API_KEY"]
llm_model          = os.environ["LLM_MODEL"]
gateway_auth_token = os.environ["GATEWAY_AUTH_TOKEN"]
workspace          = os.environ.get("WORKSPACE", "/workspace")
google_oauth_path  = os.environ.get("GOOGLE_OAUTH_JSON_PATH", "/run/secrets/app/google-oauth.json")
google_tokens_path = os.environ.get("GOOGLE_TOKENS_JSON_PATH", "/run/secrets/user/google-tokens.json")
config_file        = "/root/.openclaw/openclaw.json"

env_key_map = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "anthropic":  "ANTHROPIC_API_KEY",
}
env_key = env_key_map[platform]  # already validated by shell case above

cfg = {
    "gateway": {
        "mode": "local",
        "bind": "lan",
        "port": 18789,
        "auth": {
            "mode": "token",
            "token": gateway_auth_token,
        },
        "http": {
            "endpoints": {
                "responses": {
                    "enabled": True,
                    "files":  {"allowUrl": False},
                    "images": {"allowUrl": False},
                },
                "chatCompletions": {"enabled": False},
            }
        },
    },
    "env": {env_key: api_key},
    "agents": {
        "defaults": {
            "workspace": workspace,
            "model": {"primary": llm_model},
            "timeoutSeconds": 120,
        }
    },
    "channels": {"telegram": {"enabled": False}},
    "browser": {
        "enabled": True,
        "executablePath": "/usr/bin/chromium",
        "headless": True,
        "noSandbox": True,
    },

    # Do not set plugins.allow here. A restrictive plugins.allow list blocks
    # bundled plugins unless every required plugin is explicitly listed.
    # In particular, browser.enabled=true is not enough if plugins.allow
    # excludes the bundled browser plugin.
    "plugins": {
        "entries": {
            "browser": {"enabled": True},
        }
    },
    "tools": {
        "alsoAllow": ["browser"],
    },
    "skills": {
        "entries": {
            "browser-automation": {"enabled": True},
        }
    },
}

if os.path.isfile(google_oauth_path):
    plugin_name = "openclaw-google-workspace"
    cfg["tools"].setdefault("alsoAllow", []).append(plugin_name)
    cfg["plugins"]["entries"][plugin_name] = {
        "enabled": True,
        "config": {
            "credentialsPath": google_oauth_path,
            "tokenPath": google_tokens_path,
        },
    }

with open(config_file, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
PY

echo "[entrypoint] Platform: $PLATFORM"
echo "[entrypoint] Model: $LLM_MODEL"
echo "[entrypoint] Workspace: $WORKSPACE"
echo "[entrypoint] Provider env key: $ENV_KEY"
echo "[entrypoint] HTTP Responses enabled: yes"
echo "[entrypoint] Native Telegram channel: disabled"
echo "[entrypoint] Google OAuth config mounted: $( [ -f "$GOOGLE_OAUTH_JSON_PATH" ] && echo yes || echo no )"
echo "[entrypoint] Browser plugin: enabled"
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

BROWSER_POLICY_MARKER="## Browser Automation Policy"
if ! grep -q "$BROWSER_POLICY_MARKER" "$AGENTS_FILE" 2>/dev/null; then
cat >> "$AGENTS_FILE" <<'EOF'

## Browser Automation Policy
- The browser-automation skill and browser tool are available in this environment.
- If the user explicitly asks to open, view, check, inspect, parse, or analyze a public website or URL, treat that request as consent to start and use the browser runtime. Do not ask for a second confirmation before ordinary navigation, reading, screenshots, or extraction.
- Ask for confirmation before logging in, submitting forms, making purchases, sending messages, uploading/downloading files, changing account settings, or performing destructive/private actions.
- When browser work is requested, use the browser tool directly. Do not answer with internal SKILL.md paths, skill diagnostics, or configuration instructions unless the browser tool is genuinely unavailable after trying.
EOF
fi

# ── Yandex 360 (yax) ─────────────────────────────────────────────────────────
YAX_TOKEN_SRC="/run/secrets/user/yax-token.json"
YAX_DIR="/root/.openclaw/yax"
if [ -f "$YAX_TOKEN_SRC" ]; then
    mkdir -p "$YAX_DIR"
    cp "$YAX_TOKEN_SRC" "$YAX_DIR/token.json"
    chmod 600 "$YAX_DIR/token.json"
    # config.json нужен yax.js для автообновления токена
    printf '{"client_id":"%s","client_secret":"%s"}' \
        "${YANDEX_CLIENT_ID:-}" "${YANDEX_CLIENT_SECRET:-}" > "$YAX_DIR/config.json"
    echo "[entrypoint] Yandex 360: токен смонтирован"
else
    echo "[entrypoint] Yandex 360: не подключён"
fi

exec openclaw gateway
