import random
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from .models import OTPCode

def generate_otp():
    return "".join([str(random.randint(0, 9)) for _ in range(6)])

def send_otp_email(email, code):
    # Dev helper: write to file and print to console immediately
    try:
        with open("last_otp.txt", "w") as f:
            f.write(f"Email: {email}\nOTP Code: {code}\n")
    except Exception:
        pass

    print("\n" + "="*50)
    print(f"  VUREX OTP CODE FOR {email}: {code}")
    print("="*50 + "\n")

    subject = f"Your Vurex Verification Code: {code}"
    body = f"Hello,\n\nYour security verification code for Vurex Escrow is: {code}\n\nThis code will expire in 5 minutes.\n\nBest regards,\nThe Vurex Team"
    
    # Check if Brevo key is configured (Preferred)
    brevo_key = getattr(settings, "BREVO_API_KEY", "")
    sender_email = getattr(settings, "BREVO_SENDER_EMAIL", "noreply@vurex.io")
    if brevo_key and not brevo_key.startswith("your_") and brevo_key != "":
        try:
            import requests
            headers = {
                "api-key": brevo_key,
                "Content-Type": "application/json",
            }
            payload = {
                "sender": {"name": "Vurex Escrow", "email": sender_email},
                "to": [{"email": email}],
                "subject": subject,
                "textContent": body,
            }
            res = requests.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)
            if res.status_code in [200, 201, 202]:
                print(f"[OTP] Emailed code {code} to {email} via Brevo")
                return True
            else:
                print(f"[OTP] Brevo returned status code {res.status_code}: {res.text}")
        except Exception as e:
            print(f"[OTP] Brevo sending failed: {e}")

    # Check if SendGrid key is configured
    sendgrid_key = getattr(settings, "SENDGRID_API_KEY", "")
    if sendgrid_key and not sendgrid_key.startswith("your_") and sendgrid_key != "":
        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail
            message = Mail(
                from_email='noreply@vurex.io',  # In production, this must be a verified SendGrid sender
                to_emails=email,
                subject=subject,
                plain_text_content=body
            )
            sg = SendGridAPIClient(sendgrid_key)
            sg.send(message)
            print(f"[OTP] Emailed code {code} to {email} via SendGrid")
            return True
        except Exception as e:
            print(f"[OTP] SendGrid failed, falling back to console: {e}")
            
    # Fallback to console printing (always do this in dev or if not configured)
    print("\n" + "="*50)
    print(f"  VUREX OTP CODE FOR {email}: {code}")
    print("="*50 + "\n")
    return True

def create_and_send_otp(email):
    # Expire/delete any existing OTPs for this email first
    OTPCode.objects.filter(email=email).delete()
    
    code = generate_otp()
    expires_at = timezone.now() + timedelta(minutes=5)
    
    otp = OTPCode.objects.create(
        email=email,
        code=code,
        expires_at=expires_at
    )
    
    send_otp_email(email, code)
    return otp
