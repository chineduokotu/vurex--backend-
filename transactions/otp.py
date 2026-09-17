"""Legacy email OTP authentication is retired; use identity phone challenges."""

def create_and_send_otp(*args, **kwargs):
    raise RuntimeError("Legacy email OTP authentication is disabled.")
