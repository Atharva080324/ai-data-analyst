"""
services/email.py — Gmail SMTP email service for OTP delivery
Uses Python's built-in smtplib — no third-party email SDK needed.
"""

import os
import ssl
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", 587))
SMTP_USER     = os.getenv("SMTP_USER", "")        # your Gmail address
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")     # Gmail App Password (16 chars)
FROM_EMAIL    = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)
APP_NAME      = "AI Data Analyst"


def send_otp_email(
    to_email:  str,
    otp_code:  str,
    purpose:   str,
    user_name: str = "User",
) -> bool:
    """
    Send OTP email via Gmail SMTP.
    Returns True on success, False on failure — never raises.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        print("[EMAIL ERROR] SMTP_USER or SMTP_PASSWORD not set in .env")
        return False

    if purpose == "verify_email":
        subject = f"Verify your {APP_NAME} account"
        heading = "Verify your email address"
        note    = "This OTP expires in 10 minutes."
        body    = f"Welcome to {APP_NAME}! Use the OTP below to verify your email."
    else:
        subject = f"Reset your {APP_NAME} password"
        heading = "Reset your password"
        note    = "This OTP expires in 10 minutes. If you didn't request this, ignore this email."
        body    = f"Use the OTP below to reset your {APP_NAME} password."

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body{{font-family:Arial,sans-serif;background:#f4f4f4;margin:0;padding:20px}}
  .c{{max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
  .h{{background:linear-gradient(135deg,#5b5ef4,#7c6af7);padding:32px;text-align:center}}
  .h h1{{color:#fff;margin:0;font-size:22px}}
  .b{{padding:32px}}
  .b p{{color:#444;line-height:1.6}}
  .otp{{background:#f0f0ff;border:2px dashed #5b5ef4;border-radius:8px;text-align:center;padding:24px;margin:24px 0}}
  .otp-code{{font-size:42px;font-weight:700;color:#5b5ef4;letter-spacing:12px;font-family:monospace}}
  .note{{font-size:13px;color:#888;text-align:center;margin-top:16px}}
  .f{{background:#f8f8f8;padding:16px;text-align:center;font-size:12px;color:#aaa}}
</style>
</head>
<body>
<div class="c">
  <div class="h"><h1>{APP_NAME}</h1></div>
  <div class="b">
    <h2>{heading}</h2>
    <p>Hi {user_name},</p>
    <p>{body}</p>
    <div class="otp"><div class="otp-code">{otp_code}</div></div>
    <p class="note">⏱ {note}</p>
    <p class="note">Never share this OTP with anyone.</p>
  </div>
  <div class="f">© {APP_NAME} — Automated message, do not reply.</div>
</div>
</body>
</html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{APP_NAME} <{FROM_EMAIL}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

        print(f"[EMAIL] OTP sent to {to_email}")
        return True

    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send to {to_email}: {e}")
        return False