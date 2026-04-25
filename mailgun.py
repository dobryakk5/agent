import os

import httpx

MAILGUN_API_KEY  = os.environ.get("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN   = os.environ.get("MAILGUN_DOMAIN", "")
MAILGUN_FROM     = os.environ.get("MAILGUN_FROM", "")
MAILGUN_API_BASE = os.environ.get("MAILGUN_API_BASE", "https://api.mailgun.net").rstrip("/")


class MailgunConfigError(RuntimeError):
    pass


def _ensure_config() -> None:
    if not MAILGUN_API_KEY:
        raise MailgunConfigError("MAILGUN_API_KEY is not set")
    if not MAILGUN_DOMAIN:
        raise MailgunConfigError("MAILGUN_DOMAIN is not set")


def _from_address() -> str:
    return MAILGUN_FROM or f"noreply@{MAILGUN_DOMAIN}"


async def send_email(*, to: str, subject: str, text: str, html: str | None = None) -> None:
    _ensure_config()
    data: dict = {
        "from":    _from_address(),
        "to":      to,
        "subject": subject,
        "text":    text,
    }
    if html:
        data["html"] = html

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{MAILGUN_API_BASE}/v3/{MAILGUN_DOMAIN}/messages",
            auth=("api", MAILGUN_API_KEY),
            data=data,
        )
        r.raise_for_status()


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    text = (
        f"Вы запросили сброс пароля.\n\n"
        f"Перейдите по ссылке для установки нового пароля:\n{reset_url}\n\n"
        f"Ссылка действительна 1 час.\n\n"
        f"Если вы не запрашивали сброс пароля — просто проигнорируйте это письмо."
    )
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;max-width:480px;margin:40px auto;color:#1a1a1a">
  <p style="font-size:13px;color:#888;letter-spacing:.1em;text-transform:uppercase">
    Сброс пароля
  </p>
  <h2 style="font-weight:400;margin:8px 0 24px">Установите новый пароль</h2>
  <p style="color:#555;line-height:1.6">
    Вы запросили сброс пароля для вашего аккаунта. Нажмите кнопку ниже, чтобы
    задать новый пароль. Ссылка действительна <strong>1 час</strong>.
  </p>
  <a href="{reset_url}"
     style="display:inline-block;margin:24px 0;padding:12px 28px;
            background:#2563eb;color:#fff;text-decoration:none;
            border-radius:6px;font-size:14px;font-weight:500">
    Сбросить пароль
  </a>
  <p style="color:#999;font-size:12px;margin-top:32px">
    Если кнопка не работает, скопируйте ссылку в браузер:<br>
    <a href="{reset_url}" style="color:#2563eb">{reset_url}</a>
  </p>
  <p style="color:#bbb;font-size:11px;margin-top:16px">
    Если вы не запрашивали сброс пароля — просто проигнорируйте это письмо.
  </p>
</body>
</html>"""
    await send_email(to=to_email, subject="Сброс пароля", text=text, html=html)
