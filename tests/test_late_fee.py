"""
Tests for late fee, overdue status, and CSV vs manual payment parity.

Ensures lateness is determined from the payment's actual payment_date,
NOT the date of CSV import or the current date.

Also verifies that rent and late fees are tracked separately:
- Paying full rent with an outstanding late fee => rent status PAID, late fee OUTSTANDING
- Late fees do not distort rent status or next-due-date progression
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
            late_fee_paid=Decimal("0.00"),
            status="unpaid",
            late_fee_status="none",
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
        """
        period_dates = [
            date(2026, 1, 5), date(2026, 1, 19), date(2026, 2, 2),
            date(2026, 2, 16), date(2026, 3, 2),
        ]
        periods = [self._make_period(d) for d in period_dates]

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
        path sorts them by parsed_date ASC before allocating.
        """
        period_dates = [
            date(2026, 1, 5), date(2026, 1, 19), date(2026, 2, 2),
        ]
        periods = [self._make_period(d) for d in period_dates]

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
        compute_tenant_status() runs later.
        """
        rp = self._make_period(date(2020, 6, 1))
        p = self._make_payment(date(2020, 6, 1), source="csv")
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")

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
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 8))
        allocate_payment(p)

        alloc_count_before = PaymentAllocation.query.count()
        allocate_payment(p)
        alloc_count_after = PaymentAllocation.query.count()
        self.assertEqual(alloc_count_before, alloc_count_after,
                         "Duplicate allocate_payment call must not create extra rows")

    def test_deallocate_then_reallocate(self):
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
        rp = self._make_period(date(2026, 3, 8))
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


# ======================================================================
# 11. SEPARATE RENT AND LATE FEE TRACKING (the core bug fix)
# ======================================================================

class TestSeparateRentAndLateFee(BaseTestCase):
    """Tests verifying that rent and late fees are tracked independently."""

    def test_full_rent_paid_on_time_no_late_fee(self):
        """Scenario 1: Tenant pays full rent on time => PAID, no late fee."""
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 8), amount=Decimal("500.00"))
        allocate_payment(p)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")
        self.assertEqual(rp.late_fee_status, "none")
        self.assertEqual(rp.rent_balance(), Decimal("0.00"))
        self.assertEqual(rp.late_fee_balance(), Decimal("0.00"))
        self.assertEqual(rp.balance(), Decimal("0.00"))

    def test_full_rent_paid_late_fee_outstanding(self):
        """
        Scenario 2: THE CORE BUG.
        Tenant pays full rent 3 days late. Late fee is applied.
        Tenant pays only the rent amount ($500), not the late fee.
        => Rent status: PAID
        => Late fee status: OUTSTANDING
        => Period must NOT be overdue.
        """
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 11), amount=Decimal("500.00"))
        allocate_payment(p)
        db.session.refresh(rp)

        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 3).quantize(Decimal("0.01"))

        # Rent is fully paid
        self.assertEqual(rp.status, "paid")
        self.assertEqual(rp.rent_balance(), Decimal("0.00"))
        self.assertEqual(Decimal(str(rp.amount_paid)), Decimal("500.00"))

        # Late fee is outstanding
        self.assertEqual(rp.late_fee_status, "outstanding")
        self.assertEqual(rp.late_fee_balance(), expected_fee)
        self.assertEqual(Decimal(str(rp.late_fee_paid)), Decimal("0.00"))

        # Total balance is just the late fee
        self.assertEqual(rp.balance(), expected_fee)

    def test_partial_rent_only(self):
        """Scenario 3: Tenant pays part of rent => status PARTIAL."""
        rp = self._make_period(date(2026, 3, 8))
        p = self._make_payment(date(2026, 3, 8), amount=Decimal("200.00"))
        allocate_payment(p)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "partial")
        self.assertEqual(rp.rent_balance(), Decimal("300.00"))
        self.assertEqual(rp.late_fee_status, "none")

    def test_rent_plus_late_fee_fully_paid(self):
        """Scenario 4: Tenant pays rent + late fee in full => everything PAID."""
        rp = self._make_period(date(2026, 3, 8))
        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 3).quantize(Decimal("0.01"))
        total = Decimal("500.00") + expected_fee

        p = self._make_payment(date(2026, 3, 11), amount=total)
        allocate_payment(p)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")
        self.assertEqual(rp.late_fee_status, "paid")
        self.assertEqual(rp.rent_balance(), Decimal("0.00"))
        self.assertEqual(rp.late_fee_balance(), Decimal("0.00"))
        self.assertEqual(rp.balance(), Decimal("0.00"))

    def test_old_unpaid_late_fee_does_not_affect_current_rent(self):
        """
        Scenario 5: Old period has unpaid late fee. Current period rent is
        fully paid. Current rent period must NOT be shown as overdue.
        """
        # Old period: paid late, late fee outstanding
        old_rp = self._make_period(date(2026, 1, 5))
        p1 = self._make_payment(date(2026, 1, 8), amount=Decimal("500.00"))
        allocate_payment(p1)
        db.session.refresh(old_rp)
        self.assertEqual(old_rp.status, "paid")
        self.assertEqual(old_rp.late_fee_status, "outstanding")

        # Current period: paid on time
        current_rp = self._make_period(date(2026, 3, 8))
        p2 = self._make_payment(date(2026, 3, 8), amount=Decimal("500.00"))
        allocate_payment(p2)
        db.session.refresh(current_rp)

        # Current period should be fully paid — not affected by old late fee
        self.assertEqual(current_rp.status, "paid")
        self.assertEqual(current_rp.late_fee_status, "none")
        self.assertEqual(current_rp.rent_balance(), Decimal("0.00"))

        # Old period still has outstanding late fee but rent is paid
        db.session.refresh(old_rp)
        self.assertEqual(old_rp.status, "paid")
        self.assertEqual(old_rp.late_fee_status, "outstanding")

    def test_next_due_advances_with_late_fee_outstanding(self):
        """
        Scenario 6: Next due date advances correctly even when a late fee
        remains unpaid from an old period.
        """
        rp1 = self._make_period(date(2026, 1, 5))
        rp2 = self._make_period(date(2026, 1, 19))
        rp3 = self._make_period(date(2026, 2, 2))

        # Pay rp1 late (late fee created), pay rp2 on time
        p1 = self._make_payment(date(2026, 1, 8), amount=Decimal("500.00"))
        allocate_payment(p1)
        p2 = self._make_payment(date(2026, 1, 19), amount=Decimal("500.00"))
        allocate_payment(p2)

        db.session.refresh(rp1)
        db.session.refresh(rp2)
        db.session.refresh(rp3)

        self.assertEqual(rp1.status, "paid")
        self.assertEqual(rp1.late_fee_status, "outstanding")  # late fee unpaid
        self.assertEqual(rp2.status, "paid")
        self.assertIn(rp3.status, ("unpaid", "overdue"))  # next due period

        # "Next due" should be rp3, not rp1 (which still has a late fee)
        today = date.today()
        next_due = next(
            (rp.due_date for rp in sorted(self.tenant.rent_periods, key=lambda r: r.due_date)
             if rp.status != "paid"),
            None,
        )
        self.assertEqual(next_due, date(2026, 2, 2),
                         "Next due should advance past paid periods even with outstanding late fees")

    def test_payment_covers_rent_then_late_fee(self):
        """Payment larger than rent is allocated: rent first, then late fee."""
        rp = self._make_period(date(2026, 3, 8))
        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 3).quantize(Decimal("0.01"))

        # Pay rent + half the late fee
        half_fee = (expected_fee / 2).quantize(Decimal("0.01"))
        p = self._make_payment(date(2026, 3, 11), amount=Decimal("500.00") + half_fee)
        allocate_payment(p)
        db.session.refresh(rp)

        self.assertEqual(rp.status, "paid")  # Rent is fully paid
        self.assertEqual(rp.late_fee_status, "outstanding")  # Fee partially paid
        self.assertEqual(rp.rent_balance(), Decimal("0.00"))
        self.assertGreater(rp.late_fee_balance(), Decimal("0.00"))

        # Verify the allocation split
        alloc = PaymentAllocation.query.filter_by(payment_id=p.id).first()
        self.assertEqual(Decimal(str(alloc.rent_allocated)), Decimal("500.00"))
        self.assertEqual(Decimal(str(alloc.late_fee_allocated)), half_fee)

    def test_late_fee_paid_separately(self):
        """First payment covers rent. Second payment covers the late fee."""
        rp = self._make_period(date(2026, 3, 8))
        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 3).quantize(Decimal("0.01"))

        # First payment: just the rent
        p1 = self._make_payment(date(2026, 3, 11), amount=Decimal("500.00"))
        allocate_payment(p1)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(rp.late_fee_status, "outstanding")

        # Second payment: the late fee
        p2 = self._make_payment(date(2026, 3, 15), amount=expected_fee)
        allocate_payment(p2)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(rp.late_fee_status, "paid")
        self.assertEqual(rp.balance(), Decimal("0.00"))

    def test_deallocate_reverses_rent_and_fee_separately(self):
        """Deallocation correctly reverses both rent and late fee portions."""
        rp = self._make_period(date(2026, 3, 8))
        daily_rate = Decimal("500.00") / Decimal("14")
        expected_fee = (daily_rate * 3).quantize(Decimal("0.01"))
        total = Decimal("500.00") + expected_fee

        p = self._make_payment(date(2026, 3, 11), amount=total)
        allocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(rp.status, "paid")
        self.assertEqual(rp.late_fee_status, "paid")

        deallocate_payment(p)
        db.session.refresh(rp)
        self.assertEqual(Decimal(str(rp.amount_paid)), Decimal("0.00"))
        self.assertEqual(Decimal(str(rp.late_fee_paid)), Decimal("0.00"))
        self.assertEqual(Decimal(str(rp.late_fee)), Decimal("0.00"))
        self.assertIn(rp.status, ("unpaid", "overdue"))
        self.assertEqual(rp.late_fee_status, "none")

    def test_tenant_status_not_overdue_with_only_late_fee_outstanding(self):
        """
        compute_tenant_status should not return 'overdue' when all rent
        is paid but a late fee is outstanding.
        """
        rp = self._make_period(date(2026, 1, 5))
        p = self._make_payment(date(2026, 1, 8), amount=Decimal("500.00"))
        allocate_payment(p)
        db.session.refresh(rp)

        status = compute_tenant_status(self.tenant)
        self.assertEqual(status, "paid",
                         "Tenant status should be PAID when only late fee is outstanding")

    def test_balance_methods(self):
        """Verify rent_balance, late_fee_balance, and balance are correct."""
        rp = self._make_period(date(2026, 3, 8), amount=Decimal("610.00"))
        rp.late_fee = Decimal("43.57")
        rp.amount_paid = Decimal("610.00")
        rp.late_fee_paid = Decimal("0.00")

        self.assertEqual(rp.rent_balance(), Decimal("0.00"))
        self.assertEqual(rp.late_fee_balance(), Decimal("43.57"))
        self.assertEqual(rp.balance(), Decimal("43.57"))

        rp.update_status()
        self.assertEqual(rp.status, "paid")  # Rent is paid
        self.assertEqual(rp.late_fee_status, "outstanding")  # Fee outstanding


if __name__ == "__main__":
    unittest.main()
