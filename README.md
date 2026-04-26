# OpenClaw SaaS — Инструкция по развёртыванию

## Структура проекта

```
provisioner/
  Dockerfile.agent   # Docker образ с OpenClaw + Chromium
  entrypoint.sh      # Стартовый скрипт: генерирует конфиг и запускает gateway
  main.py            # FastAPI: HTTP API + раздача дашборда
  docker_manager.py  # Управление контейнерами через Docker SDK
  metrics.py         # Сбор метрик CPU/RAM/сеть в Postgres (каждые 30 сек)
  schema.sql         # Таблицы PostgreSQL
  dashboard.html     # Веб-интерфейс администратора
  .env               # Переменные окружения (заполнить!)
  README.md          # Этот файл
```

---

## Требования

- Ubuntu 24.04
- Python 3.11+
- PostgreSQL (уже установлен на VPS)
- Docker Engine

---

## Шаг 1 — Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

Проверить:
```bash
docker --version
```

---

## Шаг 2 — Установка Python зависимостей

```bash
pip install fastapi uvicorn docker asyncpg python-dotenv
```

---

## Шаг 3 — Настройка PostgreSQL

Создать базу и применить схему:

```bash
psql -U postgres -c "CREATE DATABASE openclaw_admin;"
psql -U postgres -d openclaw_admin -f schema.sql
```

---

## Шаг 4 — Заполнить .env

```
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/openclaw_admin
JWT_SECRET=change-me
ADMIN_EMAILS=admin@example.com
BREVO_SMTP_HOST=smtp-relay.brevo.com
BREVO_SMTP_PORT=587
BREVO_SMTP_LOGIN=your-brevo-login@smtp-brevo.com
BREVO_SMTP_PASSWORD=your_brevo_smtp_key_or_password
BREVO_FROM=noreply@your-domain.example
BREVO_FROM_NAME=AI Assistant
GOOGLE_OAUTH_JSON_PATH=/srv/openclaw/secrets/google-oauth.json
GOOGLE_REDIRECT_URI=https://your-domain.example/oauth/google/callback
TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxx
TELEGRAM_BOT_USERNAME=YourBotNameWithoutAt
TELEGRAM_WEBHOOK_URL=https://your-domain.example/telegram/webhook
TELEGRAM_WEBHOOK_SECRET=replace-with-random-string
```

Для отправки писем сброса пароля теперь используется `Brevo SMTP`. Адрес в `BREVO_FROM` должен быть заранее подтверждён в аккаунте Brevo.

---

## Шаг 5 — Telegram архитектура

Теперь используется **один внешний Telegram-бот на весь сервис**.

- бот принимает webhook на backend
- backend по `telegram_user_id` находит пользователя
- backend будит нужный Docker контейнер
- backend шлёт запрос в `/v1/responses` нужного OpenClaw инстанса
- ответ возвращается обратно через того же бота

Пользовательский инстанс больше **не хранит свой отдельный bot token** и не слушает Telegram напрямую.

---

## Шаг 6 — Собрать Docker образ

Из папки проекта:

```bash
docker build -f Dockerfile.agent -t openclaw-agent:latest .
```

Сборка занимает 3–5 минут (устанавливает Chromium).

---

## Шаг 7 — Запустить FastAPI сервер

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Дашборд доступен по адресу: `http://YOUR_VPS_IP:8000`

Для фонового запуска:

```bash
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > app.log 2>&1 &
```

---

## Шаг 8 — Запустить первый инстанс

Открыть `http://YOUR_VPS_IP:8000` в браузере.

Заполнить форму:
- **User ID** — любое число (например, `1`)
- **Anthropic API Key** — `sk-ant-...` (получить на console.anthropic.com)
- **Telegram Bot Token** — токен из шага 5

Нажать **▶ Запустить**.

---

## Шаг 9 — Проверить что работает

1. Написать своему боту в Telegram любое сообщение
2. Бот должен ответить — OpenClaw внутри контейнера активен

Первый запуск создаёт в volume:
- `MEMORY.md` — долгосрочная память агента
- `AGENTS.md` — инструкции для агента (личный ассистент)
- `memory/` — папка для ежедневных логов

---

## Дашборд — возможности

| Элемент | Описание |
|---|---|
| Статистика вверху | Всего / Running / Stopped / Средний CPU |
| Форма | Запуск нового инстанса |
| Таблица инстансов | Статус, имя контейнера, дата создания, кнопки управления |
| Графики метрик | CPU %, RAM MB, Net RX/TX — с отмеченными пиками |
| Автообновление | Каждые 15 секунд |

---

## Память агента

OpenClaw хранит память в Markdown файлах внутри Docker volume (`user_X_data`):

```
/workspace/
  MEMORY.md              # Долгосрочная память (предпочтения, проекты, факты)
  AGENTS.md              # Инструкции агента
  memory/
    2026-04-13.md        # Дневной лог
    2026-04-14.md
    ...
```

**Память переживает перезапуск контейнера** — volume не удаляется при stop/start.

Чтобы сказать агенту запомнить что-то — напиши в Telegram:
> "Запомни, что я предпочитаю отвечать на русском языке"

---

## Браузер

Агент имеет доступ к Chromium (headless). Примеры команд в Telegram:
- "Открой сайт example.com и скажи что там написано"
- "Найди в Google последние новости про OpenAI"
- "Зайди на мой сайт и проверь работает ли форма"

---

## API эндпоинты

| Метод | URL | Описание |
|---|---|---|
| GET | `/` | Дашборд |
| POST | `/provision` | Запустить инстанс |
| POST | `/stop/{user_id}` | Остановить контейнер |
| DELETE | `/remove/{user_id}` | Удалить контейнер + записи |
| GET | `/instances` | Список всех инстансов |
| GET | `/metrics/{user_id}` | Последние 50 замеров метрик |
| GET | `/metrics/{user_id}/latest` | Последний замер |

---

## Мониторинг ресурсов

Метрики собираются каждые **30 секунд** и хранятся в таблице `container_metrics`.

На VPS с 4GB RAM и 1 контейнером ожидаемое потребление:
- CPU: 1–5% в покое, 20–50% при активной задаче
- RAM: 300–600 MB на контейнер с OpenClaw + Chromium

---

## Возможные проблемы

**Бот не отвечает в Telegram:**
```bash
docker logs agent_user_1
```

**Контейнер не стартует:**
```bash
docker ps -a
docker inspect agent_user_1
```

**Ошибка подключения к Postgres:**
Проверить `DATABASE_URL` в `.env` и что PostgreSQL слушает localhost.
