"""
Tests for late fee, overdue status, and CSV vs manual payment parity.

Ensures lateness is determined from the payment's actual payment_date,
NOT the date of CSV import or the current date.
"""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from flask import Flask
from app.models import db, User, Settings, Tenant, RentPeriod, Payment, PaymentAllocation
from app.payments.allocator import allocate_payment, deallocate_payment, _compute_late_fee
from app.tenants.logic import compute_tenant_status, refresh_period_statuses


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    db.init_app(app)
    return app


class BaseTestCase(unittest.TestCase):
    """Shared setup: in-memory DB with one owner and one tenant."""

    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

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

    def _make_payment(self, payment_date, amount=Decimal("500.00"), source="manual"):
        p = Payment(
            tenant_id=self.tenant.id,
            amount=amount,
            payment_date=payment_date,
            source=source,
        )
        db.session.add(p)
        db.session.flush()
        return p

    def _make_fortnightly_periods(self, start_date, count):
        """Create `count` fortnightly rent periods starting from `start_date`."""
        periods = []
        current = start_date
        for _ in range(count):
            periods.append(self._make_period(current))
            current += timedelta(days=14)
        return periods


# ======================================================================
# 1. Core late fee unit tests
# ======================================================================

class TestComputeLateFee(BaseTestCase):

    def test_on_due_date(self):
        """Payment on due date => zero late fee."""
        rp = self._make_period(date(2026, 3, 8))
        self.assertEqual(_compute_late_fee(rp, date(2026, 3, 8)), Decimal("0.00"))

    def test_before_due_date(self):
        """Payment before due date => zero late fee."""
        rp = self._make_period(date(2026, 3, 8))
        self.assertEqual(_compute_late_fee(rp, date(2026, 3, 6)), Decimal("0.00"))

    def test_after_due_date(self):
        """Payment after due date => fee based on days late."""
        rp = self._make_period(date(2026, 3, 8))
        fee = _compute_late_fee(rp, date(2026, 3, 10))
        daily_rate = Decimal("500.00") / Decimal("14")
        expected = (daily_rate * 2).quantize(Decimal("0.01"))
        self.assertEqual(fee, expected)


# ======================================================================
# 2. Manual payment tests
# ======================================================================

class TestManualPayment(BaseTestCase):

    def test_manual_on_due_date_no_late_fee(self):
        """TEST 1: Manual payment on due date => no late fee."""
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 8), source="manual")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_manual_early_no_late_fee(self):
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 5), source="manual")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_manual_late_gets_fee(self):
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 11), amount=Decimal("700.00"), source="manual")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertGreater(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertFalse(rp.paid_on_time)


# ======================================================================
# 3. CSV payment tests — must behave identically to manual
# ======================================================================

class TestCsvPayment(BaseTestCase):

    def test_csv_on_due_date_no_late_fee(self):
        """TEST 2: CSV payment on due date => no late fee."""
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 8), source="csv")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_csv_before_due_date_no_late_fee(self):
        """TEST 3: CSV payment before due date => no late fee."""
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 5), source="csv")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_csv_after_due_date_gets_fee(self):
        """TEST 4: CSV payment after due date => late fee applies."""
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 11), amount=Decimal("700.00"), source="csv")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 3).quantize(Decimal("0.01"))
        self.assertEqual(Decimal(str(rp.late_fee)), expected_fee)
        self.assertFalse(rp.paid_on_time)


# ======================================================================
# 4. Multiple fortnightly CSV payments (the exact bug scenario)
# ======================================================================

class TestMultipleCsvPayments(BaseTestCase):

    def test_fortnightly_csv_payments_in_order(self):
        """
        TEST 5: Multiple fortnightly CSV payments imported in chronological
        order. Each payment should cover its corresponding period.

        Periods:  Jan 5, Jan 19, Feb 2, Feb 16, Mar 2
        Payments: Jan 5, Jan 19, Feb 2, Feb 16, Mar 2 (all on time)
        """
        period_dates = [
            date(2026, 1, 5), date(2026, 1, 19), date(2026, 2, 2),
            date(2026, 2, 16), date(2026, 3, 2),
        ]
        periods = [self._make_period(d) for d in period_dates]

        # Allocate in order (as corrected CSV import should do)
        for d in period_dates:
            p = self._make_payment(d, source="csv")
            allocate_payment(p)

        for rp in periods:
            db.session.refresh(rp)
            self.assertEqual(rp.status, "paid",
                             f"Period {rp.due_date} should be PAID")
            self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"),
                             f"Period {rp.due_date} should have no late fee")
            self.assertTrue(rp.paid_on_time,
                            f"Period {rp.due_date} should be on time")

    def test_csv_payments_sorted_before_allocation(self):
        """
        TEST 6: Even if CSV rows are in arbitrary order, the import
        path sorts them by parsed_date ASC before allocating. We verify
        that sorting first produces correct results.

        CSV rows arrive: Feb 2, Jan 5, Jan 19
        After sorting:   Jan 5, Jan 19, Feb 2
        """
        period_dates = [
            date(2026, 1, 5), date(2026, 1, 19), date(2026, 2, 2),
        ]
        periods = [self._make_period(d) for d in period_dates]

        # Simulate CSV rows in random order, then sort before allocating
        # (this is what _apply_confirmed_transactions now does)
        csv_dates = [date(2026, 2, 2), date(2026, 1, 5), date(2026, 1, 19)]
        sorted_dates = sorted(csv_dates)

        for d in sorted_dates:
            p = self._make_payment(d, source="csv")
            allocate_payment(p)

        for rp in periods:
            db.session.refresh(rp)
            self.assertEqual(rp.status, "paid",
                             f"Period {rp.due_date} should be PAID")
            self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"),
                             f"Period {rp.due_date} should have no late fee")


# ======================================================================
# 5. Historical paid periods must not become overdue
# ======================================================================

class TestHistoricalPeriods(BaseTestCase):

    def test_paid_period_stays_paid_after_recompute(self):
        """
        TEST 7: A period paid on time must NOT become overdue when
        compute_tenant_status() runs later (e.g. weeks after the due date).
        """
        rp = self._make_period(date(2020, 6, 1))  # far in the past
        p = self._make_payment(date(2020, 6, 1), source="csv")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")

        # Simulate what happens on every page load
        compute_tenant_status(self.tenant)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid",
                         "Paid period must not flip to overdue on recompute")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))

    def test_paid_period_stays_paid_after_refresh(self):
        """refresh_period_statuses must not clobber paid periods."""
        rp = self._make_period(date(2020, 6, 1))
        p = self._make_payment(date(2020, 6, 1), source="csv")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")

        refresh_period_statuses(self.tenant)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")


# ======================================================================
# 6. CSV and manual payments mixed
# ======================================================================

class TestMixedPayments(BaseTestCase):

    def test_csv_and_manual_same_logic(self):
        """
        TEST 8: CSV and manual payments mixed together produce the
        same allocation result.
        """
        rp1 = self._make_period(date(2026, 1, 5))
        rp2 = self._make_period(date(2026, 1, 19))

        p1 = self._make_payment(date(2026, 1, 5), source="csv")
        allocate_payment(p1)
        p2 = self._make_payment(date(2026, 1, 19), source="manual")
        allocate_payment(p2)

        db.session.refresh(rp1)
        db.session.refresh(rp2)

        for rp in [rp1, rp2]:
            self.assertEqual(rp.status, "paid")
            self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
            self.assertTrue(rp.paid_on_time)


# ======================================================================
# 7. Idempotency — re-running recompute gives same results
# ======================================================================

class TestIdempotency(BaseTestCase):

    def test_recompute_is_deterministic(self):
        """
        TEST 9: Re-running compute_tenant_status / refresh multiple
        times must not change results.
        """
        periods = self._make_fortnightly_periods(date(2026, 1, 5), 3)

        for rp in periods:
            p = self._make_payment(rp.due_date, source="csv")
            allocate_payment(p)

        # Run recompute multiple times
        for _ in range(5):
            compute_tenant_status(self.tenant)
            refresh_period_statuses(self.tenant)

        for rp in periods:
            db.session.refresh(rp)
            self.assertEqual(rp.status, "paid")
            self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))


# ======================================================================
# 8. No duplicate allocations
# ======================================================================

class TestNoDuplicateAllocations(BaseTestCase):

    def test_no_duplicate_allocations(self):
        """
        TEST 10: Allocating the same payment twice should not create
        duplicate allocation rows (the second call has nothing left
        to allocate because the period is already paid).
        """
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 8))
        allocate_payment(p)

        alloc_count_before = PaymentAllocation.query.count()

        # Second allocation call — period is already paid, nothing to do
        allocate_payment(p)

        alloc_count_after = PaymentAllocation.query.count()
        self.assertEqual(alloc_count_before, alloc_count_after,
                         "Duplicate allocate_payment call must not create extra rows")

    def test_deallocate_then_reallocate(self):
        """Deallocate + reallocate produces same result, no duplicates."""
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 8))

        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")

        deallocate_payment(p)
        db.session.refresh(rp)
        self.assertIn(rp.status, ("unpaid", "overdue"))
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))

        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))

        alloc_count = PaymentAllocation.query.filter_by(payment_id=p.id).count()
        self.assertEqual(alloc_count, 1, "Should have exactly one allocation")


# ======================================================================
# 9. Stale display fee + CSV import scenario (the original bug)
# ======================================================================

class TestStaleDisplayFee(BaseTestCase):

    def test_stale_fee_overwritten_by_on_time_payment(self):
        """
        The original bug: viewing the tenant detail page wrote a
        display-only late fee to the DB. Then an on-time CSV payment
        couldn't clear it because the allocator only increased fees.
        """
        rp = self._make_period(date(2026, 3, 8))

        # Simulate stale display fee
        rp.late_fee = Decimal("71.43")
        db.session.flush()

        p = self._make_payment(date(2026, 3, 8), source="csv")
        allocate_payment(p)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)


# ======================================================================
# 10. Partial payment + deallocation edge cases
# ======================================================================

class TestPartialAndDeallocation(BaseTestCase):

    def test_partial_then_full_on_time(self):
        rp = self._make_period(date(2026, 3, 8))
        p1 = self._make_payment(date(2026, 3, 6), amount=Decimal("200.00"))
        allocate_payment(p1)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "partial")

        p2 = self._make_payment(date(2026, 3, 8), amount=Decimal("300.00"))
        allocate_payment(p2)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertTrue(rp.paid_on_time)

    def test_deallocate_clears_late_fee_and_paid_on_time(self):
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 11), amount=Decimal("700.00"))
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertGreater(Decimal(str(rp.late_fee)), Decimal("0.00"))

        deallocate_payment(p)
        db.session.refresh(rp)
        self.assertIn(rp.status, ("unpaid", "overdue"))
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertIsNone(rp.paid_on_time)

    def test_unpaid_past_due_is_overdue(self):
        rp = self._make_period(date(2020, 1, 1))
        rp.update_status()
        self.assertEqual(rp.status, "overdue")

    def test_unpaid_future_is_unpaid(self):
        rp = self._make_period(date(2099, 1, 1))
        rp.update_status()
        self.assertEqual(rp.status, "unpaid")


if __name__ == "__main__":
    unittest.main()
