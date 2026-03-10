"""
Tests for late fee and overdue status logic.

Ensures lateness is determined from the payment's actual payment_date,
NOT the date of CSV import or the current date.
"""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from flask import Flask
from app.models import db, User, Settings, Tenant, RentPeriod, Payment
from app.payments.allocator import allocate_payment, deallocate_payment, _compute_late_fee


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    db.init_app(app)
    return app


class LateFeeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Create owner + tenant
        user = User(id=1, username="owner")
        db.session.add(user)
        db.session.add(Settings(user_id=1))
        self.tenant = Tenant(
            user_id=1,
            full_name="Test Tenant",
            email="test@example.com",
            weekly_rent=Decimal("500.00"),
            payment_frequency="fortnightly",
            lease_start_date=date(2026, 1, 1),
        )
        db.session.add(self.tenant)
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_period(self, due_date, amount=Decimal("500.00")):
        rp = RentPeriod(
            tenant_id=self.tenant.id,
            period_start=due_date,
            period_end=due_date + timedelta(days=13),
            due_date=due_date,
            amount_due=amount,
            amount_paid=Decimal("0.00"),
            late_fee=Decimal("0.00"),
            status="unpaid",
        )
        db.session.add(rp)
        db.session.flush()
        return rp

    def _make_payment(self, payment_date, amount=Decimal("500.00")):
        p = Payment(
            tenant_id=self.tenant.id,
            amount=amount,
            payment_date=payment_date,
            source="csv",
        )
        db.session.add(p)
        db.session.flush()
        return p

    # ------------------------------------------------------------------
    # _compute_late_fee unit tests
    # ------------------------------------------------------------------

    def test_compute_late_fee_on_time(self):
        """Payment on due date => zero late fee."""
        rp = self._make_period(date(2026, 3, 8))
        fee = _compute_late_fee(rp, as_of_date=date(2026, 3, 8))
        self.assertEqual(fee, Decimal("0.00"))

    def test_compute_late_fee_early(self):
        """Payment before due date => zero late fee."""
        rp = self._make_period(date(2026, 3, 8))
        fee = _compute_late_fee(rp, as_of_date=date(2026, 3, 6))
        self.assertEqual(fee, Decimal("0.00"))

    def test_compute_late_fee_late(self):
        """Payment after due date => fee based on days late."""
        rp = self._make_period(date(2026, 3, 8))
        fee = _compute_late_fee(rp, as_of_date=date(2026, 3, 10))
        daily_rate = Decimal("500.00") / Decimal("14")
        expected = (daily_rate * 2).quantize(Decimal("0.01"))
        self.assertEqual(fee, expected)

    # ------------------------------------------------------------------
    # Core bug scenario: payment_date == due_date, CSV imported later
    # ------------------------------------------------------------------

    def test_on_time_payment_no_late_fee(self):
        """
        Bug scenario: due_date = 2026-03-08, payment_date = 2026-03-08,
        CSV imported on 2026-03-10.
        The payment should result in PAID status with $0 late fee.
        """
        rp = self._make_period(date(2026, 3, 8))
        payment = self._make_payment(date(2026, 3, 8))

        allocate_payment(payment)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_on_time_payment_clears_stale_display_fee(self):
        """
        If a stale display-only late fee was persisted (e.g., from viewing
        the tenant detail page), the allocator should overwrite it with
        the correct fee based on payment_date.
        """
        rp = self._make_period(date(2026, 3, 8))

        # Simulate stale display fee written by the old detail page logic
        rp.late_fee = Decimal("71.43")  # as if 2 days late
        db.session.flush()

        # Now allocate an on-time payment
        payment = self._make_payment(date(2026, 3, 8))
        allocate_payment(payment)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_late_payment_gets_fee(self):
        """Payment 3 days after due date should get a late fee."""
        rp = self._make_period(date(2026, 3, 8))
        payment = self._make_payment(date(2026, 3, 11), amount=Decimal("700.00"))

        allocate_payment(payment)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")
        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 3).quantize(Decimal("0.01"))
        self.assertEqual(Decimal(str(rp.late_fee)), expected_fee)
        self.assertFalse(rp.paid_on_time)

    def test_early_payment_no_late_fee(self):
        """Payment before due date => no late fee, on time."""
        rp = self._make_period(date(2026, 3, 8))
        payment = self._make_payment(date(2026, 3, 5))

        allocate_payment(payment)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    # ------------------------------------------------------------------
    # Deallocation resets late fee
    # ------------------------------------------------------------------

    def test_deallocate_clears_late_fee(self):
        """Deallocating a payment should reset late_fee to 0."""
        rp = self._make_period(date(2026, 3, 8))
        payment = self._make_payment(date(2026, 3, 11), amount=Decimal("700.00"))

        allocate_payment(payment)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertGreater(Decimal(str(rp.late_fee)), Decimal("0.00"))

        deallocate_payment(payment)
        db.session.refresh(rp)

        self.assertIn(rp.status, ("unpaid", "overdue"))
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertIsNone(rp.paid_on_time)

    # ------------------------------------------------------------------
    # Partial payment scenarios
    # ------------------------------------------------------------------

    def test_partial_then_full_on_time(self):
        """Two payments both on or before due_date => paid on time, no fee."""
        rp = self._make_period(date(2026, 3, 8))

        p1 = self._make_payment(date(2026, 3, 6), amount=Decimal("200.00"))
        allocate_payment(p1)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "partial")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))

        p2 = self._make_payment(date(2026, 3, 8), amount=Decimal("300.00"))
        allocate_payment(p2)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_partial_on_time_then_rest_late(self):
        """First payment on time, second payment late => late fee from second payment."""
        rp = self._make_period(date(2026, 3, 8))

        p1 = self._make_payment(date(2026, 3, 8), amount=Decimal("200.00"))
        allocate_payment(p1)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "partial")

        p2 = self._make_payment(date(2026, 3, 12), amount=Decimal("500.00"))
        allocate_payment(p2)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        # Late fee should be based on 4 days late (from second payment)
        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 4).quantize(Decimal("0.01"))
        self.assertEqual(Decimal(str(rp.late_fee)), expected_fee)
        self.assertFalse(rp.paid_on_time)

    # ------------------------------------------------------------------
    # update_status with no payment_date (display/refresh scenarios)
    # ------------------------------------------------------------------

    def test_update_status_unpaid_past_due(self):
        """Unpaid period past due date should be overdue."""
        rp = self._make_period(date(2020, 1, 1))  # far in the past
        rp.update_status()
        self.assertEqual(rp.status, "overdue")

    def test_update_status_unpaid_future(self):
        """Unpaid period with future due date should be unpaid."""
        rp = self._make_period(date(2099, 1, 1))
        rp.update_status()
        self.assertEqual(rp.status, "unpaid")


if __name__ == "__main__":
    unittest.main()
