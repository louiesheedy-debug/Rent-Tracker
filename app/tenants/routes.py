from decimal import Decimal
from datetime import date
from flask import render_template, redirect, url_for, flash, request
from . import bp
from .forms import TenantForm
from .logic import generate_rent_periods, extend_rent_periods, compute_tenant_status
from ..models import db, Tenant, Property, Payment, RentPeriod

from flask_wtf import FlaskForm
from wtforms import DecimalField, StringField, TextAreaField
from wtforms.fields import DateField
from wtforms.validators import DataRequired, NumberRange, Optional

OWNER_ID = 1


class ManualPaymentForm(FlaskForm):
    amount = DecimalField("Amount ($)", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    payment_date = DateField("Payment Date", validators=[DataRequired()])
    reference = StringField("Reference", validators=[Optional()])
    notes = TextAreaField("Notes", validators=[Optional()])


@bp.route("/add", methods=["GET", "POST"])

def add():
    form = TenantForm()
    if form.validate_on_submit():
        # Create or reuse property
        property_obj = None
        if form.property_address.data:
            property_obj = Property(
                user_id=OWNER_ID,
                address=form.property_address.data,
                suburb=form.property_suburb.data or "",
                state=form.property_state.data or "",
                postcode=form.property_postcode.data or "",
            )
            db.session.add(property_obj)
            db.session.flush()

        tenant = Tenant(
            user_id=OWNER_ID,
            property_id=property_obj.id if property_obj else None,
            full_name=form.full_name.data.title(),
            email=form.email.data,
            weekly_rent=form.fortnightly_rent.data,
            payment_frequency="fortnightly",
            lease_start_date=form.lease_start_date.data,
            notes=form.notes.data or "",
        )
        db.session.add(tenant)
        db.session.flush()
        generate_rent_periods(tenant, months_ahead=6)
        db.session.commit()
        flash(f"Tenant '{tenant.full_name}' added successfully.", "success")
        return redirect(url_for("tenants.detail", tenant_id=tenant.id))
    return render_template("tenants/add.html", form=form)


@bp.route("/<int:tenant_id>", methods=["GET", "POST"])

def detail(tenant_id):
    tenant = Tenant.query.filter_by(id=tenant_id, user_id=OWNER_ID).first_or_404()
    extend_rent_periods(tenant)
    status = compute_tenant_status(tenant)
    db.session.commit()

    payment_form = ManualPaymentForm(prefix="pay")
    if payment_form.validate_on_submit():
        from ..payments.allocator import allocate_payment
        from ..emails.sender import send_payment_received_email
        from ..models import Settings
        payment = Payment(
            tenant_id=tenant.id,
            amount=payment_form.amount.data,
            payment_date=payment_form.payment_date.data,
            reference=payment_form.reference.data or "",
            source="manual",
            notes=payment_form.notes.data or "",
        )
        db.session.add(payment)
        db.session.flush()
        allocate_payment(payment)
        db.session.commit()
        settings = Settings.query.filter_by(user_id=OWNER_ID).first()
        if settings:
            overdue_remaining = sum(
                rp.balance() for rp in
                RentPeriod.query.filter(
                    RentPeriod.tenant_id == tenant.id,
                    RentPeriod.status.in_(["overdue", "partial"])
                ).all()
            )
            send_payment_received_email(
                settings, tenant, payment.amount, payment.payment_date,
                overdue_remaining=overdue_remaining,
            )
        flash("Manual payment recorded and allocated.", "success")
        return redirect(url_for("tenants.detail", tenant_id=tenant.id))

    rent_periods = (
        RentPeriod.query.filter_by(tenant_id=tenant.id)
        .filter(RentPeriod.status != "paid")
        .order_by(RentPeriod.due_date.asc())
        .limit(5)
        .all()
    )
    # Compute display-only late fees for unpaid/overdue periods (not persisted to DB)
    from ..payments.allocator import _compute_late_fee
    display_late_fees = {}
    for rp in rent_periods:
        if rp.status in ("unpaid", "overdue") and Decimal(str(rp.amount_paid)) == 0:
            display_late_fees[rp.id] = _compute_late_fee(rp)
    payments = (
        Payment.query.filter_by(tenant_id=tenant.id)
        .order_by(Payment.payment_date.desc())
        .all()
    )
    return render_template(
        "tenants/detail.html",
        tenant=tenant,
        status=status,
        rent_periods=rent_periods,
        payments=payments,
        payment_form=payment_form,
        today=date.today(),
        display_late_fees=display_late_fees,
    )


@bp.route("/<int:tenant_id>/edit", methods=["GET", "POST"])

def edit(tenant_id):
    tenant = Tenant.query.filter_by(id=tenant_id, user_id=OWNER_ID).first_or_404()
    form = TenantForm(obj=tenant)
    if tenant.property:
        form.property_address.data = form.property_address.data or tenant.property.address
        form.property_suburb.data = form.property_suburb.data or tenant.property.suburb
        form.property_state.data = form.property_state.data or tenant.property.state
        form.property_postcode.data = form.property_postcode.data or tenant.property.postcode

    if form.validate_on_submit():
        tenant.full_name = form.full_name.data.title()
        tenant.email = form.email.data
        tenant.weekly_rent = form.fortnightly_rent.data
        tenant.payment_frequency = "fortnightly"
        tenant.lease_start_date = form.lease_start_date.data
        tenant.notes = form.notes.data or ""

        if form.property_address.data:
            if tenant.property:
                tenant.property.address = form.property_address.data
                tenant.property.suburb = form.property_suburb.data or ""
                tenant.property.state = form.property_state.data or ""
                tenant.property.postcode = form.property_postcode.data or ""
            else:
                prop = Property(
                    user_id=OWNER_ID,
                    address=form.property_address.data,
                    suburb=form.property_suburb.data or "",
                    state=form.property_state.data or "",
                    postcode=form.property_postcode.data or "",
                )
                db.session.add(prop)
                db.session.flush()
                tenant.property_id = prop.id

        # Update unpaid/overdue rent periods with the new rent amount
        new_amount = tenant.rent_amount()
        for rp in tenant.rent_periods:
            if rp.status not in ("paid",):
                rp.amount_due = new_amount
                rp.update_status()

        db.session.commit()
        flash("Tenant updated.", "success")
        return redirect(url_for("tenants.detail", tenant_id=tenant.id))
    return render_template("tenants/edit.html", form=form, tenant=tenant)


@bp.route("/<int:tenant_id>/deactivate", methods=["GET", "POST"])

def deactivate(tenant_id):
    tenant = Tenant.query.filter_by(id=tenant_id, user_id=OWNER_ID).first_or_404()
    if request.method == "POST":
        tenant.is_active = False
        db.session.commit()
        flash(f"Tenant '{tenant.full_name}' has been deactivated.", "warning")
        return redirect(url_for("dashboard.index"))
    return render_template("tenants/deactivate.html", tenant=tenant)
