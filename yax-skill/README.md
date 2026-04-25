# yax v2.0 — Yandex 360 для OpenClaw

Улучшенная версия скилла [smvlx/openclaw-ru-skills](https://github.com/smvlx/openclaw-ru-skills).

## Что добавлено по сравнению с оригиналом

| Возможность | Оригинал | v2.0 |
|---|---|---|
| Диск: info, list, upload, mkdir | ✅ | ✅ |
| Диск: download, delete, search, share | ❌ | ✅ |
| Календарь: list, events, create | ✅ | ✅ |
| Календарь: delete | ❌ | ✅ |
| Почта (IMAP/SMTP) | ❌ | ✅ |
| Telemost | ❌/? | ✅ |
| Автообновление токена | ❌ | ✅ |
| Совместимость с новым OpenClaw | ❌ | ✅ (ручная установка) |

## Быстрый старт

### 1. Клонировать

```bash
git clone <этот-репо> ~/.openclaw/skills/yax
```

### 2. Установить

```bash
cd ~/.openclaw/skills/yax
bash scripts/install.sh
source ~/.bashrc   # или ~/.zshrc
```

### 3. Создать OAuth-приложение

1. Зайди на https://oauth.yandex.ru/client/new
2. Название: любое (например "OpenClaw yax")
3. Платформа: **Веб-сервисы**
4. Redirect URI: `https://oauth.yandex.ru/verification_code`
5. Scopes — выбери нужные:

| Scope | Для чего |
|---|---|
| `cloud_api:disk.app_folder` | Диск: папка приложения |
| `cloud_api:disk.info` | Диск: информация |
| `cloud_api:disk.read` | Диск: чтение |
| `cloud_api:disk.write` | Диск: запись |
| `calendar:all` | Календарь: всё |
| `mail:imap_full` | Почта: чтение (IMAP) |
| `mail:smtp` | Почта: отправка (SMTP) |
| `telemost-api:conferences.create` | Telemost: создание |

6. Сохрани Client ID

### 4. Авторизоваться

```bash
yax auth --client-id ВАШ_CLIENT_ID
```

Откроется ссылка в браузере → авторизуйся → скопируй код → вставь в терминал.

Токен сохраняется в `~/.openclaw/yax/token.json` и **обновляется автоматически**.

## Использование

```bash
# Диск
yax disk info
yax disk list /Документы
yax disk upload ./file.pdf /Документы/file.pdf
yax disk download /Документы/file.pdf ./file.pdf
yax disk mkdir /Новая\ папка
yax disk delete /Старый\ файл.txt
yax disk search договор
yax disk share /Публичное/doc.pdf

# Календарь
yax calendar list
yax calendar events          # 7 дней
yax calendar events 30       # 30 дней
yax calendar create "Стендап" "2026-05-01" "09:30:00" "10:00:00" "Ежедневный стендап" "Europe/Moscow"
yax calendar delete yax-1234567890@yandex

# Почта (работает локально, на Railway порты могут быть заблокированы)
yax mail inbox
yax mail inbox 25
yax mail read 42
yax mail send boss@company.ru "Отчёт готов" "Прикладываю отчёт за апрель"

# Telemost
yax telemost create
yax telemost create "Встреча с клиентом"
```

## Ограничения

- **Почта на Railway**: порты 993/465 заблокированы, IMAP/SMTP не работает. Локально — ок.
- **Поиск по диску**: Yandex Disk REST API не поддерживает full-text поиск, поиск идёт по именам файлов.
- **Telemost API**: документация закрытая, endpoint может измениться.

## Отладка

```bash
YAX_DEBUG=1 yax disk info
```

## Зависимости

- `node` >= 18
- `nodemailer` — отправка почты
- `imap` — чтение почты
- `mailparser` — парсинг писем

Для Диска и Календаря внешних зависимостей нет — только нативный `https`.
