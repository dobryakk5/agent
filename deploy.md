cd /var/py/agent
git pull
docker build -f Dockerfile.agent -t openclaw-agent:latest .
/var/py/agent/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8008

# После первого деплоя
# 1) применить миграцию
psql "$DATABASE_URL" -f migrate_users.sql

# 2) настроить webhook единого Telegram-бота (нужен admin token в localStorage)
# POST /telegram/webhook/setup
