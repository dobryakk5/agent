import asyncio
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

BREVO_SMTP_HOST = os.environ.get("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
BREVO_SMTP_PORT = int(os.environ.get("BREVO_SMTP_PORT", "587"))
BREVO_SMTP_LOGIN = os.environ.get("BREVO_SMTP_LOGIN", "")
BREVO_SMTP_PASSWORD = os.environ.get("BREVO_SMTP_PASSWORD", "")
BREVO_FROM = os.environ.get("BREVO_FROM", "")
BREVO_FROM_NAME = os.environ.get("BREVO_FROM_NAME", "AI Assistant")


class BrevoConfigError(RuntimeError):
    pass


def _ensure_config() -> None:
    if not BREVO_SMTP_LOGIN:
        raise BrevoConfigError("BREVO_SMTP_LOGIN is not set")
    if not BREVO_SMTP_PASSWORD:
        raise BrevoConfigError("BREVO_SMTP_PASSWORD is not set")
    if not BREVO_FROM:
        raise BrevoConfigError("BREVO_FROM is not set")


def _from_address() -> str:
    # Brevo requires the From address to be a verified sender in your account.
    return formataddr((BREVO_FROM_NAME, BREVO_FROM)) if BREVO_FROM_NAME else BREVO_FROM


def _send_email_sync(*, to: str, subject: str, text: str, html: str | None = None) -> None:
    _ensure_config()

    msg = EmailMessage()
    msg["From"] = _from_address()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    if html:
        msg.add_alternative(html, subtype="html")

    if BREVO_SMTP_PORT == 465:
        with smtplib.SMTP_SSL(BREVO_SMTP_HOST, BREVO_SMTP_PORT, timeout=15) as smtp:
            smtp.login(BREVO_SMTP_LOGIN, BREVO_SMTP_PASSWORD)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(BREVO_SMTP_LOGIN, BREVO_SMTP_PASSWORD)
        smtp.send_message(msg)


async def send_email(*, to: str, subject: str, text: str, html: str | None = None) -> None:
    # smtplib is blocking, so run it outside the event loop.
    await asyncio.to_thread(_send_email_sync, to=to, subject=subject, text=text, html=html)


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
