from decimal import Decimal
from datetime import date
from flask import render_template
from . import bp
from ..models import Tenant, RentPeriod
from ..tenants.logic import compute_tenant_status, extend_rent_periods
from ..utils import login_required

STATUS_ORDER = {"overdue": 0, "partial": 1, "unpaid": 2, "paid": 3}

OWNER_ID = 1


@bp.route("/")
@login_required
def index():
    tenants = Tenant.query.filter_by(user_id=OWNER_ID, is_active=True).all()

    # Extend rent periods for all tenants if needed
    for tenant in tenants:
        extend_rent_periods(tenant)

    tenant_data = []
    for tenant in tenants:
        status = compute_tenant_status(tenant)
        overdue_amount = Decimal("0.00")
        for rp in tenant.rent_periods:
            if rp.status in ("overdue", "partial"):
                overdue_amount += rp.balance()
        next_due = next(
            (rp.due_date for rp in sorted(tenant.rent_periods, key=lambda r: r.due_date)
             if rp.status != "paid"),
            None,
        )
        tenant_data.append({
            "tenant": tenant,
            "status": status,
            "overdue_amount": overdue_amount,
            "next_due": next_due,
        })

    tenant_data.sort(key=lambda x: STATUS_ORDER.get(x["status"], 99))

    overdue_count = sum(1 for t in tenant_data if t["status"] == "overdue")
    total_outstanding = sum(t["overdue_amount"] for t in tenant_data)

    return render_template(
        "dashboard/index.html",
        tenant_data=tenant_data,
        overdue_count=overdue_count,
        total_outstanding=total_outstanding,
        total_tenants=len(tenants),
    )
