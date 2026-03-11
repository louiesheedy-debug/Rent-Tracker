import click
from decimal import Decimal
from .models import db, User, Settings, Tenant, RentPeriod, Payment, PaymentAllocation


def register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        """Initialize the database and seed the owner account."""
        with app.app_context():
            db.create_all()
            user = User.query.get(1)
            if not user:
                user = User(id=1, username="owner")
                db.session.add(user)
                db.session.flush()
                db.session.add(Settings(user_id=user.id))
                db.session.commit()
                click.echo("Database initialized and owner account created.")
            else:
                click.echo("Database already initialized.")

    @app.cli.command("migrate-db")
    def migrate_db():
        """Add new columns for separate rent/late-fee tracking (safe to run multiple times)."""
        with app.app_context():
            conn = db.engine.raw_connection()
            cursor = conn.cursor()

            # rent_periods columns
            cursor.execute("PRAGMA table_info(rent_periods)")
            rp_cols = {row[1] for row in cursor.fetchall()}
            for col, ddl in [
                ("late_fee_paid", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("late_fee_status", "VARCHAR(16) NOT NULL DEFAULT 'none'"),
            ]:
                if col not in rp_cols:
                    cursor.execute(f"ALTER TABLE rent_periods ADD COLUMN {col} {ddl}")
                    click.echo(f"Added rent_periods.{col}")
                else:
                    click.echo(f"rent_periods.{col} already exists")

            # payment_allocations columns
            cursor.execute("PRAGMA table_info(payment_allocations)")
            pa_cols = {row[1] for row in cursor.fetchall()}
            for col, ddl in [
                ("rent_allocated", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("late_fee_allocated", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
            ]:
                if col not in pa_cols:
                    cursor.execute(f"ALTER TABLE payment_allocations ADD COLUMN {col} {ddl}")
                    click.echo(f"Added payment_allocations.{col}")
                else:
                    click.echo(f"payment_allocations.{col} already exists")

            conn.commit()
            conn.close()
            click.echo("Migration complete. Run 'flask reallocate-payments' to recompute allocations.")

    @app.cli.command("reallocate-payments")
    @click.option("--tenant-id", type=int, default=None, help="Tenant ID (omit for all tenants)")
    def reallocate_payments(tenant_id):
        """Re-allocate all payments for a tenant (or all tenants) using corrected logic."""
        from .payments.allocator import allocate_payment
        with app.app_context():
            if tenant_id:
                tenants = Tenant.query.filter_by(id=tenant_id).all()
            else:
                tenants = Tenant.query.filter_by(is_active=True).all()

            for tenant in tenants:
                click.echo(f"Re-allocating payments for {tenant.full_name}...")

                # 1. Clear all existing allocations and reset rent periods
                for rp in tenant.rent_periods:
                    rp.amount_paid = Decimal("0.00")
                    rp.late_fee = Decimal("0.00")
                    rp.late_fee_paid = Decimal("0.00")
                    rp.paid_on_time = None
                    rp.late_fee_status = "none"
                    rp.update_status()
                PaymentAllocation.query.filter(
                    PaymentAllocation.rent_period_id.in_(
                        [rp.id for rp in tenant.rent_periods]
                    )
                ).delete(synchronize_session="fetch")
                db.session.flush()

                # 2. Re-allocate payments in chronological order
                payments = (
                    Payment.query.filter_by(tenant_id=tenant.id)
                    .order_by(Payment.payment_date.asc())
                    .all()
                )
                for payment in payments:
                    allocate_payment(payment)
                    click.echo(f"  Allocated ${payment.amount} from {payment.payment_date}")

                click.echo(f"  Done. {len(payments)} payments re-allocated.")

            db.session.commit()
            click.echo("All done.")
