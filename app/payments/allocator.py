"""
Allocate a payment to rent periods (oldest unpaid first — arrears balance model).
"""
from datetime import date
from decimal import Decimal
from ..models import db, RentPeriod, PaymentAllocation


def _compute_late_fee(period, as_of_date=None):
    """
    Calculate the late fee for an overdue period based on days past the due date.
    Daily rate = fortnightly_rent / 14, charged per day overdue.
    as_of_date defaults to today (for display), but should be set to the
    payment date when allocating payments.
    """
    ref_date = as_of_date or date.today()
    if ref_date <= period.due_date:
        return Decimal("0.00")
    days_late = (ref_date - period.due_date).days
    daily_rate = Decimal(str(period.tenant.weekly_rent)) / Decimal("14")
    return (daily_rate * days_late).quantize(Decimal("0.01"))


def allocate_payment(payment):
    """
    Distribute payment.amount across the tenant's unpaid/partial rent periods
    oldest-first. Creates PaymentAllocation rows and updates RentPeriod.amount_paid
    and status. Late fees are computed and locked in at the time of payment.
    """
    tenant_id = payment.tenant_id
    remaining = Decimal(str(payment.amount))

    # Get all unpaid/partial periods ordered by due_date ASC (oldest first)
    periods = (
        RentPeriod.query
        .filter(
            RentPeriod.tenant_id == tenant_id,
            RentPeriod.status.in_(["unpaid", "partial", "overdue"]),
        )
        .order_by(RentPeriod.due_date.asc())
        .all()
    )

    for period in periods:
        if remaining <= 0:
            break
        # Lock in the late fee based on how late the payment actually is
        computed_fee = _compute_late_fee(period, payment.payment_date)
        # Only update if no prior payment has already locked in a fee,
        # or if this payment's fee is higher (partial payment scenario)
        current_fee = Decimal(str(period.late_fee or 0))
        if computed_fee > current_fee:
            period.late_fee = computed_fee
        balance = period.balance()
        if balance <= 0:
            continue
        allocated = min(remaining, balance)
        alloc = PaymentAllocation(
            payment_id=payment.id,
            rent_period_id=period.id,
            amount_allocated=allocated,
        )
        db.session.add(alloc)
        period.amount_paid = Decimal(str(period.amount_paid)) + allocated
        period.update_status()
        remaining -= allocated

    # If there's still remaining (overpayment), apply to next unpaid period
    if remaining > 0:
        next_period = (
            RentPeriod.query
            .filter(
                RentPeriod.tenant_id == tenant_id,
                RentPeriod.status == "unpaid",
            )
            .order_by(RentPeriod.due_date.asc())
            .first()
        )
        if next_period:
            alloc = PaymentAllocation(
                payment_id=payment.id,
                rent_period_id=next_period.id,
                amount_allocated=remaining,
            )
            db.session.add(alloc)
            next_period.amount_paid = Decimal(str(next_period.amount_paid)) + remaining
            next_period.update_status()

    db.session.commit()


def deallocate_payment(payment):
    """Remove all allocations for a payment and reverse amounts_paid."""
    for alloc in payment.allocations:
        rp = alloc.rent_period
        rp.amount_paid = max(Decimal("0.00"), Decimal(str(rp.amount_paid)) - Decimal(str(alloc.amount_allocated)))
        rp.update_status()
        db.session.delete(alloc)
    db.session.commit()
