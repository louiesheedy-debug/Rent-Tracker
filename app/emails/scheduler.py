import logging
from datetime import date, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

_scheduler = None

# Don't email until this many days past due — gives time for bank
# transfers to clear so we're not emailing about a payment that's
# already on its way.
MIN_DAYS_BEFORE_REMINDER = 3

# After the first reminder, only send follow-ups once per week
# (not every single day).
REMINDER_INTERVAL_DAYS = 7


def send_overdue_reminders(app):
    """Daily job: send email reminders to confirmed-overdue tenants."""
    with app.app_context():
        from ..models import db, Tenant, RentPeriod, EmailLog, Settings, User
        from .sender import send_reminder_email

        users = User.query.all()
        for user in users:
            settings = user.settings
            if not settings:
                continue

            tenants = Tenant.query.filter_by(user_id=user.id, is_active=True).all()
            today = date.today()

            for tenant in tenants:
                overdue_periods = [
                    rp for rp in tenant.rent_periods
                    if rp.due_date < today and rp.status in ("unpaid", "partial", "overdue")
                ]
                if not overdue_periods:
                    continue

                earliest = min(overdue_periods, key=lambda rp: rp.due_date)
                days_overdue = (today - earliest.due_date).days

                # Don't send until we're sure it's genuinely overdue,
                # not just a bank delay that'll clear in a day or two
                if days_overdue < MIN_DAYS_BEFORE_REMINDER:
                    continue

                # Only send follow-up reminders once per week, not daily
                last_sent = (
                    EmailLog.query.filter(
                        EmailLog.tenant_id == tenant.id,
                        EmailLog.status == "sent",
                    )
                    .order_by(EmailLog.sent_at.desc())
                    .first()
                )
                if last_sent:
                    days_since_last = (today - last_sent.sent_at.date()).days
                    if days_since_last < REMINDER_INTERVAL_DAYS:
                        continue

                total_overdue = sum(
                    Decimal(str(rp.amount_due)) - Decimal(str(rp.amount_paid))
                    for rp in overdue_periods
                )

                success, error = send_reminder_email(settings, tenant, days_overdue, total_overdue)

                log = EmailLog(
                    tenant_id=tenant.id,
                    rent_period_id=earliest.id,
                    days_overdue=days_overdue,
                    amount_overdue=total_overdue,
                    status="sent" if success else "failed",
                    error_message=error,
                )
                db.session.add(log)
                db.session.commit()

                if success:
                    logger.info(f"Sent reminder to {tenant.email} ({days_overdue} days overdue)")
                else:
                    logger.warning(f"Failed to send reminder to {tenant.email}: {error}")


def start_scheduler(app):
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        import pytz

        _scheduler = BackgroundScheduler()

        def _get_reminder_hour():
            with app.app_context():
                from ..models import User
                user = User.query.first()
                if user and user.settings:
                    return user.settings.reminder_hour or 9
                return 9

        def _get_timezone():
            with app.app_context():
                from ..models import User
                user = User.query.first()
                if user and user.settings and user.settings.timezone:
                    return user.settings.timezone
                return "Australia/Brisbane"

        tz = pytz.timezone(_get_timezone())
        hour = _get_reminder_hour()

        _scheduler.add_job(
            func=send_overdue_reminders,
            args=[app],
            trigger="cron",
            hour=hour,
            minute=0,
            timezone=tz,
            id="daily_reminders",
            replace_existing=True,
        )
        _scheduler.start()
        logger.info(f"Scheduler started — daily reminders at {hour}:00 {tz}")
    except Exception as e:
        logger.warning(f"Could not start scheduler: {e}")
