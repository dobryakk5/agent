---
name: yax
description: Yandex 360 CLI: Яндекс Диск (загрузка, скачивание, поиск, публичные ссылки), Яндекс Календарь (события, создание встреч), Яндекс Почта (входящие, чтение, отправка), Telemost (создание видеоконференций). Триггеры: диск, яндекс диск, яндекс почта, яндекс календарь, телемост, yax, yandex disk, yandex mail.
version: 2.1.0
user-invocable: true
metadata: {"openclaw": {"requires": {"bins": ["node"]}, "install": [{"kind": "node", "package": "imapflow", "bins": ["node"]}, {"kind": "node", "package": "nodemailer", "bins": ["node"]}]}}
---

# yax — Yandex 360

Скилл предоставляет доступ к Яндекс Диску, Календарю, Почте и Telemost через CLI.

## Важно: авторизация

Авторизация выполняется **один раз вручную** (не через агента), потому что требует браузера:

```bash
node {baseDir}/src/yax.js auth --client-id ВАШ_CLIENT_ID
```

Токен сохраняется в `~/.openclaw/yax/token.json` и обновляется автоматически.

Если токена нет — yax сообщит об этом и не сможет работать.

## Установка зависимостей

```bash
cd {baseDir} && npm install
```

## Запуск команд

Всегда используй полный путь `node {baseDir}/src/yax.js <команда>` — не просто `yax`, если скилл не добавлен в PATH.

## Яндекс Диск

```bash
node {baseDir}/src/yax.js disk info
node {baseDir}/src/yax.js disk list /
node {baseDir}/src/yax.js disk list /Документы
node {baseDir}/src/yax.js disk upload ./file.pdf /Документы/file.pdf
node {baseDir}/src/yax.js disk download /Фото/photo.jpg ./photo.jpg
node {baseDir}/src/yax.js disk mkdir /Проекты/2026
node {baseDir}/src/yax.js disk delete /Старое/file.txt
node {baseDir}/src/yax.js disk search договор
node {baseDir}/src/yax.js disk share /Публичное/doc.pdf
```

## Яндекс Календарь

```bash
node {baseDir}/src/yax.js calendar list
node {baseDir}/src/yax.js calendar events
node {baseDir}/src/yax.js calendar events 30
node {baseDir}/src/yax.js calendar create "Встреча" "2026-05-01" "10:00:00" "11:00:00" "Описание" "Europe/Moscow"
node {baseDir}/src/yax.js calendar delete yax-1234567890-abc@yandex
```

При создании событий uid выводится в консоль — сохрани его для последующего удаления.

## Яндекс Почта

Работает только локально (порты 993/465 часто блокируются в облаке).

```bash
node {baseDir}/src/yax.js mail inbox
node {baseDir}/src/yax.js mail inbox 25
node {baseDir}/src/yax.js mail read 42
node {baseDir}/src/yax.js mail send vasya@yandex.ru "Привет" "Как дела?"
```

## Telemost

```bash
node {baseDir}/src/yax.js telemost create
node {baseDir}/src/yax.js telemost create "Встреча с командой"
```

## Отладка

```bash
YAX_DEBUG=1 node {baseDir}/src/yax.js disk info
```

## Сценарии для агента

Когда пользователь просит:
- "Загрузи файл на диск" → `disk upload`
- "Какие встречи на этой неделе?" → `calendar events 7`
- "Создай встречу на завтра в 15:00" → `calendar create ...`
- "Отправь письмо Ване" → `mail send ...`
- "Создай созвон на 16:00" → `telemost create` + `calendar create` со ссылкой

Всегда проверяй вывод команды на наличие ошибок перед тем, как сообщать пользователю об успехе.
