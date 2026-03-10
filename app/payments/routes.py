from datetime import date
from decimal import Decimal
from flask import render_template, redirect, url_for, flash, request, abort
from . import bp
from .forms import CsvUploadForm
from .csv_parser import parse_csv
from .matcher import find_best_match, AUTO_MATCH_THRESHOLD, SUGGEST_THRESHOLD
from .allocator import allocate_payment, deallocate_payment
from ..models import db, CsvImport, BankTransaction, Tenant, Payment


OWNER_ID = 1


@bp.route("/import/upload", methods=["GET", "POST"])

def upload():
    form = CsvUploadForm()
    if form.validate_on_submit():
        file = form.file.data
        content = file.read()
        rows = parse_csv(content)

        if not rows:
            flash("No valid credit transactions found in the CSV file.", "warning")
            return redirect(url_for("payments.upload"))

        csv_import = CsvImport(
            user_id=OWNER_ID,
            filename=file.filename,
            row_count=len(rows),
            status="pending",
        )
        db.session.add(csv_import)
        db.session.flush()

        tenants = Tenant.query.filter_by(user_id=OWNER_ID, is_active=True).all()

        auto_count = 0
        unmatched_count = 0

        for row in rows:
            # Duplicate detection
            existing = BankTransaction.query.filter_by(row_hash=row["row_hash"]).first()
            if existing:
                txn = BankTransaction(
                    csv_import_id=csv_import.id,
                    raw_date=row["raw_date"],
                    raw_amount=row["raw_amount"],
                    raw_reference=row["raw_reference"],
                    raw_description=row["raw_description"],
                    parsed_date=row["parsed_date"],
                    parsed_amount=row["parsed_amount"],
                    row_hash=row["row_hash"],
                    match_status="duplicate",
                    match_confidence=0,
                )
                db.session.add(txn)
                continue

            best_tenant, score = find_best_match(row, tenants)

            if score >= AUTO_MATCH_THRESHOLD:
                status = "auto_matched"
                auto_count += 1
            elif score >= SUGGEST_THRESHOLD:
                status = "suggested"
            else:
                status = "unmatched"
                unmatched_count += 1

            txn = BankTransaction(
                csv_import_id=csv_import.id,
                raw_date=row["raw_date"],
                raw_amount=row["raw_amount"],
                raw_reference=row["raw_reference"],
                raw_description=row["raw_description"],
                parsed_date=row["parsed_date"],
                parsed_amount=row["parsed_amount"],
                row_hash=row["row_hash"],
                match_status=status,
                match_confidence=score,
                matched_tenant_id=best_tenant.id if best_tenant else None,
            )
            db.session.add(txn)

        csv_import.matched_count = auto_count
        csv_import.unmatched_count = unmatched_count
        db.session.commit()
        flash(f"CSV imported: {len(rows)} transactions found.", "success")
        return redirect(url_for("payments.review", import_id=csv_import.id))

    imports = (
        CsvImport.query.filter_by(user_id=OWNER_ID)
        .order_by(CsvImport.imported_at.desc())
        .limit(10)
        .all()
    )
    return render_template("payments/upload.html", form=form, imports=imports)


@bp.route("/import/<int:import_id>/review", methods=["GET", "POST"])

def review(import_id):
    csv_import = CsvImport.query.filter_by(
        id=import_id, user_id=OWNER_ID
    ).first_or_404()

    tenants = Tenant.query.filter_by(user_id=OWNER_ID, is_active=True).all()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "confirm_all":
            _apply_confirmed_transactions(csv_import, tenants)
            flash("All confirmed transactions applied.", "success")
            return redirect(url_for("payments.review", import_id=import_id))

        elif action == "update_match":
            txn_id = int(request.form.get("txn_id"))
            txn = BankTransaction.query.get_or_404(txn_id)
            new_tenant_id = request.form.get("tenant_id")
            new_status = request.form.get("match_status")

            if new_status == "ignored":
                txn.match_status = "ignored"
                txn.matched_tenant_id = None
            elif new_tenant_id:
                txn.matched_tenant_id = int(new_tenant_id)
                txn.match_status = new_status or "manual_matched"
            db.session.commit()
            flash("Transaction updated.", "success")
            return redirect(url_for("payments.review", import_id=import_id))

    transactions = (
        BankTransaction.query.filter_by(csv_import_id=csv_import.id)
        .order_by(BankTransaction.parsed_date.asc())
        .all()
    )

    auto_matched = [t for t in transactions if t.match_status == "auto_matched"]
    suggested = [t for t in transactions if t.match_status == "suggested"]
    unmatched = [t for t in transactions if t.match_status == "unmatched"]
    applied = [t for t in transactions if t.match_status == "manual_matched"]
    duplicates = [t for t in transactions if t.match_status == "duplicate"]
    ignored = [t for t in transactions if t.match_status == "ignored"]

    return render_template(
        "payments/review.html",
        csv_import=csv_import,
        auto_matched=auto_matched,
        suggested=suggested,
        unmatched=unmatched,
        applied=applied,
        duplicates=duplicates,
        ignored=ignored,
        tenants=tenants,
    )


def _apply_confirmed_transactions(csv_import, tenants):
    """Create Payment records and allocate for all auto/manual matched transactions."""
    from ..emails.sender import send_payment_received_email
    from ..models import Settings

    txns = (
        BankTransaction.query.filter(
            BankTransaction.csv_import_id == csv_import.id,
            BankTransaction.match_status.in_(["auto_matched", "manual_matched"]),
            BankTransaction.matched_payment_id.is_(None),
            BankTransaction.matched_tenant_id.isnot(None),
        )
        .order_by(BankTransaction.parsed_date.asc())
        .all()
    )

    tenant_map = {t.id: t for t in tenants}
    settings = Settings.query.filter_by(user_id=OWNER_ID).first()

    for txn in txns:
        tenant = tenant_map.get(txn.matched_tenant_id)
        if not tenant:
            continue
        payment = Payment(
            tenant_id=tenant.id,
            amount=txn.parsed_amount,
            payment_date=txn.parsed_date or date.today(),
            reference=txn.raw_reference or txn.raw_description,
            source="csv",
            csv_import_id=csv_import.id,
        )
        db.session.add(payment)
        db.session.flush()
        allocate_payment(payment)
        txn.matched_payment_id = payment.id
        txn.match_status = "manual_matched"
        if settings:
            send_payment_received_email(settings, tenant, payment.amount, payment.payment_date)

    csv_import.status = "complete"
    db.session.commit()


@bp.route("/payment/<int:payment_id>/delete", methods=["POST"])

def delete_payment(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    tenant = payment.tenant
    deallocate_payment(payment)
    db.session.delete(payment)
    db.session.commit()
    flash("Payment deleted and allocations reversed.", "warning")
    return redirect(url_for("tenants.detail", tenant_id=tenant.id))
