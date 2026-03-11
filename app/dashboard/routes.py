from collections import defaultdict
from decimal import Decimal
from datetime import date
from flask import render_template
from sqlalchemy import extract, func
from . import bp
from ..models import db, Tenant, RentPeriod, Payment
from ..tenants.logic import compute_tenant_status, extend_rent_periods


STATUS_ORDER = {"overdue": 0, "partial": 1, "unpaid": 2, "paid": 3}

OWNER_ID = 1

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _build_rent_collected():
    """
    Query all payments and build:
      - current_year_months: list of {month_name, total} for each month of the current year
      - current_year_total: grand total for the current year so far
      - past_years: list of {year, months: [{month_name, total}], total} for each prior year
    """
    today = date.today()
    current_year = today.year

    # Query monthly totals grouped by year and month
    rows = (
        db.session.query(
            extract("year", Payment.payment_date).label("yr"),
            extract("month", Payment.payment_date).label("mo"),
            func.sum(Payment.amount).label("total"),
        )
        .group_by("yr", "mo")
        .order_by("yr", "mo")
        .all()
    )

    # Organise into {year: {month: total}}
    year_month_totals = defaultdict(lambda: defaultdict(Decimal))
    for row in rows:
        yr = int(row.yr)
        mo = int(row.mo)
        year_month_totals[yr][mo] = Decimal(str(row.total))

    # Current year breakdown
    current_year_months = []
    for m in range(1, 13):
        current_year_months.append({
            "month_name": MONTH_NAMES[m],
            "total": year_month_totals[current_year].get(m, Decimal("0.00")),
        })
    current_year_total = sum(item["total"] for item in current_year_months)

    # Past years (descending)
    past_years = []
    for yr in sorted(year_month_totals.keys(), reverse=True):
        if yr == current_year:
            continue
        months = []
        for m in range(1, 13):
            total = year_month_totals[yr].get(m, Decimal("0.00"))
            if total > 0:
                months.append({"month_name": MONTH_NAMES[m], "total": total})
        yr_total = sum(item["total"] for item in months)
        if yr_total > 0:
            past_years.append({"year": yr, "months": months, "total": yr_total})

    return current_year_months, current_year_total, current_year, past_years


@bp.route("/")
def index():
    tenants = Tenant.query.filter_by(user_id=OWNER_ID, is_active=True).all()

    # Extend rent periods for all tenants if needed
    for tenant in tenants:
        extend_rent_periods(tenant)

    tenant_data = []
    for tenant in tenants:
        status = compute_tenant_status(tenant)
        overdue_amount = Decimal("0.00")
        late_fees_outstanding = Decimal("0.00")
        for rp in tenant.rent_periods:
            if rp.status in ("overdue", "partial"):
                overdue_amount += rp.rent_balance()
            if rp.late_fee_status == "outstanding":
                late_fees_outstanding += rp.late_fee_balance()
        next_due = next(
            (rp.due_date for rp in sorted(tenant.rent_periods, key=lambda r: r.due_date)
             if rp.status != "paid"),
            None,
        )
        tenant_data.append({
            "tenant": tenant,
            "status": status,
            "overdue_amount": overdue_amount,
            "late_fees_outstanding": late_fees_outstanding,
            "next_due": next_due,
        })

    tenant_data.sort(key=lambda x: STATUS_ORDER.get(x["status"], 99))

    overdue_count = sum(1 for t in tenant_data if t["status"] == "overdue")
    total_outstanding = sum(t["overdue_amount"] for t in tenant_data)

    current_year_months, current_year_total, current_year, past_years = _build_rent_collected()

    return render_template(
        "dashboard/index.html",
        tenant_data=tenant_data,
        overdue_count=overdue_count,
        total_outstanding=total_outstanding,
        total_tenants=len(tenants),
        current_year_months=current_year_months,
        current_year_total=current_year_total,
        current_year=current_year,
        past_years=past_years,
    )
