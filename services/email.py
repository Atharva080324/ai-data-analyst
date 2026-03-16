"""
services/email.py — Resend email service for OTP delivery
"""
 
import os
import resend
from dotenv import load_dotenv
 
load_dotenv()
 
resend.api_key = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL     = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
APP_NAME       = "AI Data Analyst"
 
 
def send_otp_email(
    to_email:  str,
    otp_code:  str,
    purpose:   str,
    user_name: str = "User",
) -> bool:
    """
    Send OTP email via Resend.
    Returns True on success, False on failure — never raises.
    """
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
  .h{{background:#6366f1;padding:32px;text-align:center}}
  .h h1{{color:#fff;margin:0;font-size:22px}}
  .b{{padding:32px}}
  .b p{{color:#444;line-height:1.6}}
  .otp{{background:#f0f0ff;border:2px dashed #6366f1;border-radius:8px;text-align:center;padding:24px;margin:24px 0}}
  .otp-code{{font-size:42px;font-weight:700;color:#6366f1;letter-spacing:12px;font-family:monospace}}
  .note{{font-size:13px;color:#888;text-align:center;margin-top:16px}}
  .f{{background:#f8f8f8;padding:16px;text-align:center;font-size:12px;color:#aaa}}
</style>
</head>
<body>
<div class="c">
  <div class="h"><h1>AI Data Analyst</h1></div>
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
        resend.Emails.send({
            "from":    FROM_EMAIL,
            "to":      [to_email],
            "subject": subject,
            "html":    html,
        })
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send to {to_email}: {e}")
        return False