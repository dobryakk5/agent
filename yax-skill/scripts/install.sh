#!/usr/bin/env bash
# Установка yax skill для OpenClaw
set -e

SKILL_DIR="$HOME/.openclaw/skills/yax"

echo "📦 Устанавливаем yax в $SKILL_DIR"

# Если запускается из репо — копируем туда
if [ "$(pwd)" != "$SKILL_DIR" ]; then
  mkdir -p "$SKILL_DIR"
  cp -r . "$SKILL_DIR/"
  cd "$SKILL_DIR"
fi

# Устанавливаем зависимости
npm install --omit=dev

# Делаем исполняемым
chmod +x src/yax.js

# Добавляем в PATH если ещё нет
SHELL_RC="$HOME/.bashrc"
[ -n "$ZSH_VERSION" ] && SHELL_RC="$HOME/.zshrc"

PATH_LINE='export PATH="$HOME/.openclaw/skills/yax/src:$PATH"'
if ! grep -q "openclaw/skills/yax" "$SHELL_RC" 2>/dev/null; then
  echo "" >> "$SHELL_RC"
  echo "# yax — Yandex 360 CLI" >> "$SHELL_RC"
  echo "$PATH_LINE" >> "$SHELL_RC"
  echo "✅ PATH добавлен в $SHELL_RC"
fi

echo ""
echo "✅ Установка завершена!"
echo ""
echo "Перезапустите shell или выполните:"
echo "  source $SHELL_RC"
echo ""
echo "Затем авторизуйтесь:"
echo "  yax auth --client-id ВАШ_CLIENT_ID"
