"""
Allocate a payment to rent periods (oldest unpaid first — arrears balance model).

Two-pass allocation:
  Pass 1: Pay base rent across all periods (oldest first)
  Pass 2: Pay late fees across all periods (oldest first)

This ensures rent always takes priority over late fees, so a tenant who
pays their full rent is never marked overdue just because of a late fee.
"""
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import or_
from ..models import db, RentPeriod, PaymentAllocation, Settings


def _get_grace_days():
    """Load grace period from settings, with a safe fallback."""
    s = Settings.query.first()
    return s.grace_period_days if s else 0


def _compute_late_fee(period, as_of_date=None, grace_period_days=0):
    """
    Calculate the late fee for an overdue period based on days past the
    grace deadline.  The fee clock starts AFTER the grace window, so a
    payment within the grace period incurs no fee at all.
    Daily rate = fortnightly_rent / 14, charged per day late.
    """
    ref_date = as_of_date or date.today()
    grace_deadline = period.due_date + timedelta(days=grace_period_days)
    if ref_date <= grace_deadline:
        return Decimal("0.00")
    days_late = (ref_date - grace_deadline).days
    daily_rate = Decimal(str(period.tenant.weekly_rent)) / Decimal("14")
    return (daily_rate * days_late).quantize(Decimal("0.01"))


def allocate_payment(payment):
    """
    Distribute payment.amount across the tenant's unpaid/partial rent periods
    oldest-first.

    Two-pass allocation:
      Pass 1: Base rent (oldest first)
      Pass 2: Late fees (oldest first)

    Late fees are computed and locked in at the time of the first payment
    against a period.
    """
    tenant_id = payment.tenant_id
    remaining = Decimal(str(payment.amount))
    grace = _get_grace_days()

    # Get all periods that need payment: unpaid/partial/overdue rent OR outstanding late fees
    periods = (
        RentPeriod.query
        .filter(
            RentPeriod.tenant_id == tenant_id,
            or_(
                RentPeriod.status.in_(["unpaid", "partial", "overdue"]),
                RentPeriod.late_fee_status == "outstanding",
            ),
        )
        .order_by(RentPeriod.due_date.asc())
        .all()
    )

    # Track allocations per period: {period_id: {"rent": Decimal, "fee": Decimal}}
    alloc_map = {}

    # Lock in late fees for periods with unpaid rent
    for period in periods:
        if period.status in ("unpaid", "partial", "overdue"):
            computed_fee = _compute_late_fee(period, payment.payment_date, grace_period_days=grace)
            current_fee = Decimal(str(period.late_fee or 0))
            if Decimal(str(period.amount_paid)) == 0 and Decimal(str(period.late_fee_paid or 0)) == 0:
                # First payment against this period: always use the real fee
                # (replaces any stale display-only fee that may have been persisted)
                period.late_fee = computed_fee
            elif computed_fee > current_fee:
                # Subsequent partial payment: only increase (later payment = more days late)
                period.late_fee = computed_fee

    # Pass 1: Allocate to base rent (oldest first)
    for period in periods:
        if remaining <= 0:
            break
        rent_owed = period.rent_balance()
        if rent_owed <= 0:
            continue
        rent_alloc = min(remaining, rent_owed)
        period.amount_paid = Decimal(str(period.amount_paid)) + rent_alloc
        remaining -= rent_alloc
        alloc_map[period.id] = {"rent": rent_alloc, "fee": Decimal("0.00"), "period": period}

    # Pass 2: Allocate to late fees (oldest first)
    for period in periods:
        if remaining <= 0:
            break
        fee_owed = period.late_fee_balance()
        if fee_owed <= 0:
            continue
        fee_alloc = min(remaining, fee_owed)
        period.late_fee_paid = Decimal(str(period.late_fee_paid or 0)) + fee_alloc
        remaining -= fee_alloc
        if period.id in alloc_map:
            alloc_map[period.id]["fee"] = fee_alloc
        else:
            alloc_map[period.id] = {"rent": Decimal("0.00"), "fee": fee_alloc, "period": period}

    # Create PaymentAllocation rows and update statuses
    for pid, info in alloc_map.items():
        total = info["rent"] + info["fee"]
        if total > 0:
            alloc = PaymentAllocation(
                payment_id=payment.id,
                rent_period_id=pid,
                amount_allocated=total,
                rent_allocated=info["rent"],
                late_fee_allocated=info["fee"],
            )
            db.session.add(alloc)
        info["period"].update_status(payment_date=payment.payment_date, grace_period_days=grace)

    # If there's still remaining (overpayment), apply to next unpaid period
    if remaining > 0:
        next_period = (
            RentPeriod.query
            .filter(
                RentPeriod.tenant_id == tenant_id,
                RentPeriod.status == "unpaid",
                ~RentPeriod.id.in_(list(alloc_map.keys())) if alloc_map else True,
            )
            .order_by(RentPeriod.due_date.asc())
            .first()
        )
        if next_period:
            rent_owed = next_period.rent_balance()
            rent_alloc = min(remaining, rent_owed) if rent_owed > 0 else Decimal("0.00")
            if rent_alloc > 0:
                next_period.amount_paid = Decimal(str(next_period.amount_paid)) + rent_alloc
                remaining -= rent_alloc
                alloc = PaymentAllocation(
                    payment_id=payment.id,
                    rent_period_id=next_period.id,
                    amount_allocated=rent_alloc,
                    rent_allocated=rent_alloc,
                    late_fee_allocated=Decimal("0.00"),
                )
                db.session.add(alloc)
                next_period.update_status(payment_date=payment.payment_date, grace_period_days=grace)

    # Catch-up sweep: forgive late fees on periods where tenant has caught up
    _catchup_sweep(tenant_id, grace)

    db.session.commit()


def _catchup_sweep(tenant_id, grace_period_days=0):
    """Forgive late fees only for one-off late payments, not chronic lateness.

    A late fee is forgiven only when the NEXT period was paid on time,
    proving it was a one-off (e.g. bank delay). If the tenant keeps
    paying late period after period, the fees stick.
    """
    periods = (
        RentPeriod.query
        .filter(RentPeriod.tenant_id == tenant_id)
        .order_by(RentPeriod.due_date.asc())
        .all()
    )
    today = date.today()
    for i, period in enumerate(periods):
        if period.due_date > today:
            break
        if period.rent_balance() > 0:
            continue  # rent not fully paid, skip
        if Decimal(str(period.late_fee or 0)) <= 0:
            continue  # no late fee to forgive

        # Check if the next period exists and was paid on time
        next_period = periods[i + 1] if i + 1 < len(periods) else None
        if (next_period
                and next_period.rent_balance() <= 0
                and next_period.paid_on_time is True):
            period.late_fee = Decimal("0.00")
            period.late_fee_paid = Decimal("0.00")
            period.late_fee_status = "none"
            period.update_status(grace_period_days=grace_period_days)


def deallocate_payment(payment):
    """Remove all allocations for a payment and reverse amounts_paid."""
    grace = _get_grace_days()
    for alloc in payment.allocations:
        rp = alloc.rent_period
        rent_portion = Decimal(str(alloc.rent_allocated or 0))
        fee_portion = Decimal(str(alloc.late_fee_allocated or 0))

        # If the allocation predates the split fields, fall back to old logic
        if rent_portion == 0 and fee_portion == 0 and Decimal(str(alloc.amount_allocated)) > 0:
            rent_portion = Decimal(str(alloc.amount_allocated))

        rp.amount_paid = max(Decimal("0.00"), Decimal(str(rp.amount_paid)) - rent_portion)
        rp.late_fee_paid = max(Decimal("0.00"), Decimal(str(rp.late_fee_paid or 0)) - fee_portion)

        # If no payments remain against this period, clear the locked-in late fee
        if Decimal(str(rp.amount_paid)) == 0 and Decimal(str(rp.late_fee_paid or 0)) == 0:
            rp.late_fee = Decimal("0.00")
        rp.update_status(grace_period_days=grace)
        db.session.delete(alloc)
    db.session.commit()
