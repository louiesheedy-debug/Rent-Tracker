from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from ..models import db, RentPeriod


def generate_rent_periods(tenant, months_ahead=6):
    """Generate rent periods from lease_start_date forward for months_ahead months."""
    end_date = tenant.lease_start_date + relativedelta(months=months_ahead)
    amount = tenant.rent_amount()

    # Find latest existing period
    existing = (
        RentPeriod.query.filter_by(tenant_id=tenant.id)
        .order_by(RentPeriod.period_start.desc())
        .first()
    )

    if existing:
        current_start = existing.period_end + timedelta(days=1)
    else:
        current_start = tenant.lease_start_date

    new_periods = []
    while current_start < end_date:
        period_start = current_start
        period_end = period_start + timedelta(days=13)

        due_date = period_start  # due at start of period

        rp = RentPeriod(
            tenant_id=tenant.id,
            period_start=period_start,
            period_end=period_end,
            due_date=due_date,
            amount_due=amount,
            amount_paid=Decimal("0.00"),
            status="unpaid",
        )
        rp.update_status()
        new_periods.append(rp)
        db.session.add(rp)

        current_start = period_end + timedelta(days=1)

    db.session.commit()
    return new_periods


def extend_rent_periods(tenant, months_ahead=6):
    """Extend periods if the latest period is less than months_ahead away."""
    latest = (
        RentPeriod.query.filter_by(tenant_id=tenant.id)
        .order_by(RentPeriod.period_end.desc())
        .first()
    )
    from dateutil.relativedelta import relativedelta
    cutoff = date.today() + relativedelta(months=2)
    if latest is None or latest.period_end < cutoff:
        generate_rent_periods(tenant, months_ahead)


def compute_tenant_status(tenant):
    """Return worst status across all rent periods up to and including today."""
    today = date.today()
    statuses = []
    for rp in tenant.rent_periods:
        if rp.period_start > today:
            continue
        rp.update_status()
        statuses.append(rp.status)

    order = ["overdue", "partial", "unpaid", "paid"]
    for s in order:
        if s in statuses:
            return s
    return "unpaid"


def refresh_period_statuses(tenant):
    """Recompute status for all periods of a tenant."""
    for rp in tenant.rent_periods:
        rp.update_status()
    db.session.commit()
