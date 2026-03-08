from datetime import date, timedelta
from flask import render_template, request
from . import bp
from ..models import EmailLog, Tenant


OWNER_ID = 1


@bp.route("/history")

def history():
    tenant_id = request.args.get("tenant_id", type=int)
    days = request.args.get("days", 30, type=int)

    query = (
        EmailLog.query
        .join(Tenant, EmailLog.tenant_id == Tenant.id)
        .filter(Tenant.user_id == OWNER_ID)
    )

    if tenant_id:
        query = query.filter(EmailLog.tenant_id == tenant_id)

    since = date.today() - timedelta(days=days)
    query = query.filter(EmailLog.sent_at >= since)

    logs = query.order_by(EmailLog.sent_at.desc()).all()
    tenants = Tenant.query.filter_by(user_id=OWNER_ID).all()

    return render_template(
        "emails/history.html",
        logs=logs,
        tenants=tenants,
        selected_tenant_id=tenant_id,
        days=days,
    )
