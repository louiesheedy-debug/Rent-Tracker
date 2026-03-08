import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def _send(settings, to_address, subject, body_plain, body_html):
    """Internal helper to send an email via Gmail SMTP."""
    if not settings.smtp_email or not settings.smtp_app_password:
        return False, "SMTP credentials not configured in Settings."
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_email
        msg["To"] = to_address
        msg.attach(MIMEText(body_plain, "plain"))
        msg.attach(MIMEText(body_html, "html"))
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(settings.smtp_email, settings.smtp_app_password)
            server.sendmail(settings.smtp_email, to_address, msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)


def send_payment_received_email(settings, tenant, amount, payment_date, overdue_remaining=None):
    """
    Send a payment confirmation email to a tenant.
    - If overdue_remaining > 0: thank them but flag the outstanding arrears.
    - If all clear: thank them and show when next rent is due.
    """
    property_address = tenant.property.full_address() if tenant.property else ""
    first_name = tenant.full_name.split()[0]
    still_overdue = overdue_remaining and overdue_remaining > 0

    subject = f"Payment Received - {'Outstanding Balance Remaining' if still_overdue else 'Thank You, ' + first_name + '!'}"

    # --- Plain text ---
    if still_overdue:
        status_block = (
            f"However, please note that you still have an outstanding overdue balance of ${overdue_remaining:.2f}.\n"
            f"This is from previous rent periods that have not yet been paid. Could you please arrange to clear this as soon as possible?"
        )
    else:
        status_block = "Your rent account is now fully up to date."

    plain = f"""Hi {first_name},

We have received your rent payment of ${amount:.2f} on {payment_date.strftime('%d %B %Y')}. Thank you — it is greatly appreciated.

{status_block}

{"Property: " + property_address if property_address else ""}

If you have any questions, feel free to get in touch.

Regards,
{settings.app_name}
"""

    # --- HTML ---
    if still_overdue:
        status_html = f"""
  <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:15px;margin:20px 0;">
    <strong style="color:#856404;">Outstanding Balance Remaining</strong>
    <p style="margin:8px 0 0;">You still have an overdue balance of <strong style="color:#dc3545;">${overdue_remaining:.2f}</strong> from previous rent periods that have not yet been paid. Please arrange to clear this as soon as possible.</p>
  </div>"""
    else:
        status_html = """
  <div style="background:#d1e7dd;border:1px solid #a3cfbb;border-radius:6px;padding:15px;margin:20px 0;">
    <strong style="color:#0f5132;">All Up To Date &#10003;</strong>
    <p style="margin:8px 0 0;">Your rent account is fully up to date.</p>
  </div>"""

    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;padding:20px;">
  <h2 style="color:#28a745;">Payment Received &#10003;</h2>
  <p>Hi <strong>{first_name}</strong>,</p>
  <p>We have received your rent payment — thank you, it is greatly appreciated!</p>
  <table style="border-collapse:collapse;width:100%;margin:20px 0;">
    <tr style="background:#f8f9fa;">
      <td style="padding:10px;border:1px solid #dee2e6;"><strong>Amount Received</strong></td>
      <td style="padding:10px;border:1px solid #dee2e6;">${amount:.2f}</td>
    </tr>
    <tr>
      <td style="padding:10px;border:1px solid #dee2e6;"><strong>Payment Date</strong></td>
      <td style="padding:10px;border:1px solid #dee2e6;">{payment_date.strftime('%d %B %Y')}</td>
    </tr>
    {"<tr style='background:#f8f9fa;'><td style='padding:10px;border:1px solid #dee2e6;'><strong>Property</strong></td><td style='padding:10px;border:1px solid #dee2e6;'>" + property_address + "</td></tr>" if property_address else ""}
  </table>
  {status_html}
  <p>If you have any questions, feel free to get in touch.</p>
  <p>Regards,<br><strong>{settings.app_name}</strong></p>
</body></html>
"""
    return _send(settings, tenant.email, subject, plain, html)


def send_partial_payment_email(settings, tenant, amount, payment_date, balance_remaining):
    """
    Send a partial payment email letting the tenant know they still owe a balance.
    """
    property_address = tenant.property.full_address() if tenant.property else ""
    first_name = tenant.full_name.split()[0]
    subject = f"Payment Received - Small Balance Remaining, {first_name}"

    plain = f"""Hi {first_name},

Thanks so much for your payment of ${amount:.2f} on {payment_date.strftime('%d %B %Y')} — it is really appreciated!

Just a heads up, there is still a small balance of ${balance_remaining:.2f} remaining on your rent. When you get a chance, could you please arrange to pay the outstanding amount?

{"Property: " + property_address if property_address else ""}

If you have any questions, feel free to reach out.

Regards,
{settings.app_name}
"""

    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;padding:20px;">
  <h2 style="color:#fd7e14;">Payment Received &#10003;</h2>
  <p>Hi <strong>{first_name}</strong>,</p>
  <p>Thanks so much for your payment — it is really appreciated!</p>
  <table style="border-collapse:collapse;width:100%;margin:20px 0;">
    <tr style="background:#f8f9fa;">
      <td style="padding:10px;border:1px solid #dee2e6;"><strong>Amount Received</strong></td>
      <td style="padding:10px;border:1px solid #dee2e6;">${amount:.2f}</td>
    </tr>
    <tr>
      <td style="padding:10px;border:1px solid #dee2e6;"><strong>Payment Date</strong></td>
      <td style="padding:10px;border:1px solid #dee2e6;">{payment_date.strftime('%d %B %Y')}</td>
    </tr>
    <tr style="background:#fff3cd;">
      <td style="padding:10px;border:1px solid #dee2e6;"><strong>Balance Remaining</strong></td>
      <td style="padding:10px;border:1px solid #dee2e6;color:#fd7e14;"><strong>${balance_remaining:.2f}</strong></td>
    </tr>
    {"<tr style='background:#f8f9fa;'><td style='padding:10px;border:1px solid #dee2e6;'><strong>Property</strong></td><td style='padding:10px;border:1px solid #dee2e6;'>" + property_address + "</td></tr>" if property_address else ""}
  </table>
  <p>When you get a chance, could you please arrange to pay the outstanding balance of <strong>${balance_remaining:.2f}</strong>? No rush, but we'd appreciate it when possible!</p>
  <p>If you have any questions, feel free to reach out.</p>
  <p>Regards,<br><strong>{settings.app_name}</strong></p>
</body></html>
"""
    return _send(settings, tenant.email, subject, plain, html)


def send_reminder_email(settings, tenant, days_overdue, total_overdue):
    """
    Send an overdue rent reminder email to a tenant.
    """
    property_address = tenant.property.full_address() if tenant.property else ""
    first_name = tenant.full_name.split()[0]
    subject = f"Overdue Rent Notice - {days_overdue} Day{'s' if days_overdue != 1 else ''} Outstanding"

    plain = f"""Hi {first_name},

This is a reminder that your rent payment is currently overdue. Please see the details below.

  Amount Outstanding:  ${total_overdue:.2f}
  Days Overdue:        {days_overdue} day{'s' if days_overdue != 1 else ''}
  {"Property:             " + property_address if property_address else ""}

We kindly ask that you arrange payment as soon as possible to avoid further arrears building up. If you are experiencing any difficulties, please don't hesitate to reach out so we can work something out.

Regards,
{settings.app_name}
"""

    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;padding:20px;">
  <h2 style="color:#dc3545;">Overdue Rent Notice</h2>
  <p>Hi <strong>{first_name}</strong>,</p>
  <p>This is a reminder that your rent payment is currently overdue. Please see the details below.</p>
  <table style="border-collapse:collapse;width:100%;margin:20px 0;">
    <tr style="background:#fff3cd;">
      <td style="padding:10px;border:1px solid #dee2e6;"><strong>Amount Outstanding</strong></td>
      <td style="padding:10px;border:1px solid #dee2e6;color:#dc3545;"><strong>${total_overdue:.2f}</strong></td>
    </tr>
    <tr>
      <td style="padding:10px;border:1px solid #dee2e6;"><strong>Days Overdue</strong></td>
      <td style="padding:10px;border:1px solid #dee2e6;">{days_overdue} day{'s' if days_overdue != 1 else ''}</td>
    </tr>
    {"<tr style='background:#f8f9fa;'><td style='padding:10px;border:1px solid #dee2e6;'><strong>Property</strong></td><td style='padding:10px;border:1px solid #dee2e6;'>" + property_address + "</td></tr>" if property_address else ""}
  </table>
  <p>We kindly ask that you arrange payment as soon as possible to avoid further arrears building up.</p>
  <p>If you are experiencing any difficulties, please don't hesitate to reach out so we can work something out.</p>
  <p>Regards,<br><strong>{settings.app_name}</strong></p>
</body></html>
"""
    return _send(settings, tenant.email, subject, plain, html)


def send_test_email(settings, to_address):
    """Send a test email to verify SMTP config."""
    if not settings.smtp_email or not settings.smtp_app_password:
        return False, "SMTP credentials not configured."
    plain = "This is a test email from your Rent Tracker application."
    html = "<p>This is a test email from your <strong>Rent Tracker</strong> application.</p>"
    return _send(settings, to_address, "Rent Tracker - Test Email", plain, html)
