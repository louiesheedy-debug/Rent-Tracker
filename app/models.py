from datetime import datetime, date, timedelta
from decimal import Decimal
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    settings = db.relationship("Settings", back_populates="user", uselist=False)
    tenants = db.relationship("Tenant", back_populates="user")
    properties = db.relationship("Property", back_populates="user")
    csv_imports = db.relationship("CsvImport", back_populates="user")


class Settings(db.Model):
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    timezone = db.Column(db.String(64), default="Australia/Brisbane")
    smtp_email = db.Column(db.String(128), default="")
    smtp_app_password = db.Column(db.String(256), default="")
    app_name = db.Column(db.String(128), default="Rent Tracker")
    reminder_hour = db.Column(db.Integer, default=9)
    grace_period_days = db.Column(db.Integer, default=2, server_default="2", nullable=False)

    user = db.relationship("User", back_populates="settings")


class Property(db.Model):
    __tablename__ = "properties"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    address = db.Column(db.String(256), nullable=False)
    suburb = db.Column(db.String(128), default="")
    state = db.Column(db.String(64), default="")
    postcode = db.Column(db.String(16), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="properties")
    tenants = db.relationship("Tenant", back_populates="property")

    def full_address(self):
        parts = [self.address]
        if self.suburb:
            parts.append(self.suburb)
        if self.state:
            parts.append(self.state)
        if self.postcode:
            parts.append(self.postcode)
        return ", ".join(parts)


class Tenant(db.Model):
    __tablename__ = "tenants"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id"), nullable=True)
    full_name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(128), nullable=False)
    weekly_rent = db.Column(db.Numeric(10, 2), nullable=False)
    payment_frequency = db.Column(db.String(16), nullable=False, default="fortnightly")
    lease_start_date = db.Column(db.Date, nullable=False)
    rent_due_day = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text, default="")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", back_populates="tenants")
    property = db.relationship("Property", back_populates="tenants")
    rent_periods = db.relationship("RentPeriod", back_populates="tenant", order_by="RentPeriod.due_date")
    payments = db.relationship("Payment", back_populates="tenant")
    email_logs = db.relationship("EmailLog", back_populates="tenant")

    def rent_amount(self):
        """Return the fortnightly rent amount."""
        return Decimal(str(self.weekly_rent))


class RentPeriod(db.Model):
    __tablename__ = "rent_periods"
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    amount_due = db.Column(db.Numeric(10, 2), nullable=False)
    amount_paid = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    late_fee = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    late_fee_paid = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    status = db.Column(db.String(16), default="unpaid")  # unpaid/partial/paid/overdue
    late_fee_status = db.Column(db.String(16), default="none")  # none/outstanding/paid
    paid_on_time = db.Column(db.Boolean, nullable=True)  # None=not fully paid, True=on time, False=late
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tenant = db.relationship("Tenant", back_populates="rent_periods")
    allocations = db.relationship("PaymentAllocation", back_populates="rent_period")

    def rent_balance(self):
        """Outstanding rent only (excludes late fees)."""
        return Decimal(str(self.amount_due)) - Decimal(str(self.amount_paid))

    def late_fee_balance(self):
        """Outstanding late fee only."""
        return Decimal(str(self.late_fee or 0)) - Decimal(str(self.late_fee_paid or 0))

    def balance(self):
        """Total outstanding (rent + late fee)."""
        return self.rent_balance() + self.late_fee_balance()

    def update_status(self, payment_date=None, grace_period_days=0):
        # Rent status based solely on whether base rent is paid
        grace_deadline = self.due_date + timedelta(days=grace_period_days)
        rent_bal = self.rent_balance()
        if rent_bal <= 0:
            self.status = "paid"
            if payment_date is not None:
                self.paid_on_time = (payment_date <= grace_deadline)
        elif Decimal(str(self.amount_paid)) == 0:
            self.paid_on_time = None
            if grace_deadline < date.today():
                self.status = "overdue"
            else:
                self.status = "unpaid"
        else:
            self.status = "partial"

        # Late fee status tracked independently
        fee = Decimal(str(self.late_fee or 0))
        if fee <= 0:
            self.late_fee_status = "none"
        elif self.late_fee_balance() <= 0:
            self.late_fee_status = "paid"
        else:
            self.late_fee_status = "outstanding"


class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    reference = db.Column(db.String(256), default="")
    source = db.Column(db.String(16), default="manual")  # csv/manual
    csv_import_id = db.Column(db.Integer, db.ForeignKey("csv_imports.id"), nullable=True)
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tenant = db.relationship("Tenant", back_populates="payments")
    csv_import = db.relationship("CsvImport", back_populates="payments")
    allocations = db.relationship("PaymentAllocation", back_populates="payment", cascade="all, delete-orphan")


class PaymentAllocation(db.Model):
    __tablename__ = "payment_allocations"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"), nullable=False)
    rent_period_id = db.Column(db.Integer, db.ForeignKey("rent_periods.id"), nullable=False)
    amount_allocated = db.Column(db.Numeric(10, 2), nullable=False)
    rent_allocated = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), server_default="0", nullable=False)
    late_fee_allocated = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), server_default="0", nullable=False)

    payment = db.relationship("Payment", back_populates="allocations")
    rent_period = db.relationship("RentPeriod", back_populates="allocations")


class CsvImport(db.Model):
    __tablename__ = "csv_imports"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
    row_count = db.Column(db.Integer, default=0)
    matched_count = db.Column(db.Integer, default=0)
    unmatched_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(16), default="pending")  # pending/complete

    user = db.relationship("User", back_populates="csv_imports")
    transactions = db.relationship("BankTransaction", back_populates="csv_import")
    payments = db.relationship("Payment", back_populates="csv_import")


class BankTransaction(db.Model):
    __tablename__ = "bank_transactions"
    id = db.Column(db.Integer, primary_key=True)
    csv_import_id = db.Column(db.Integer, db.ForeignKey("csv_imports.id"), nullable=False)
    raw_date = db.Column(db.String(64), default="")
    raw_amount = db.Column(db.String(64), default="")
    raw_reference = db.Column(db.String(512), default="")
    raw_description = db.Column(db.String(512), default="")
    parsed_date = db.Column(db.Date, nullable=True)
    parsed_amount = db.Column(db.Numeric(10, 2), nullable=True)
    row_hash = db.Column(db.String(64), nullable=False)
    match_status = db.Column(db.String(16), default="unmatched")
    # auto_matched/manual_matched/unmatched/ignored/duplicate
    match_confidence = db.Column(db.Integer, default=0)
    matched_tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=True)
    matched_payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"), nullable=True)

    csv_import = db.relationship("CsvImport", back_populates="transactions")
    matched_tenant = db.relationship("Tenant")
    matched_payment = db.relationship("Payment")


class EmailLog(db.Model):
    __tablename__ = "email_logs"
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    rent_period_id = db.Column(db.Integer, db.ForeignKey("rent_periods.id"), nullable=True)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    days_overdue = db.Column(db.Integer, default=0)
    amount_overdue = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    status = db.Column(db.String(16), default="sent")  # sent/failed
    error_message = db.Column(db.Text, nullable=True)

    tenant = db.relationship("Tenant", back_populates="email_logs")
    rent_period = db.relationship("RentPeriod")
