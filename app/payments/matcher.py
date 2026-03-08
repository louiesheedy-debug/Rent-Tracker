"""
Scoring engine for matching bank transactions to tenants.
"""
import difflib
from datetime import timedelta
from decimal import Decimal


def _name_score(tenant_name, reference, description):
    """Score name match in reference/description fields."""
    combined = f"{reference} {description}".lower()
    name = tenant_name.lower()

    # Exact match
    if name in combined:
        return 40

    # Fuzzy match on full name
    ratio = difflib.SequenceMatcher(None, name, combined).ratio()
    if ratio >= 0.8:
        return 40
    if ratio >= 0.5:
        return 20

    # Try matching individual name parts
    parts = name.split()
    for part in parts:
        if len(part) > 2 and part in combined:
            return 20

    return 0


def _amount_score(tenant, parsed_amount):
    """Score amount similarity."""
    if parsed_amount is None:
        return 0
    rent = tenant.rent_amount()
    if rent == 0:
        return 0
    diff = abs(parsed_amount - rent) / rent
    if diff == 0:
        return 30
    if diff <= 0.20:
        return 15
    return 0


def _date_score(tenant, parsed_date):
    """Score date proximity to due dates."""
    if parsed_date is None:
        return 0
    for rp in tenant.rent_periods:
        delta = abs((parsed_date - rp.due_date).days)
        if delta <= 7:
            return 20
    return 0


def score_transaction(transaction_dict, tenant):
    """
    Score a parsed transaction dict against a tenant.
    Returns int 0-100.
    """
    reference = transaction_dict.get("raw_reference", "")
    description = transaction_dict.get("raw_description", "")
    parsed_amount = transaction_dict.get("parsed_amount")
    parsed_date = transaction_dict.get("parsed_date")

    score = 0
    score += _name_score(tenant.full_name, reference, description)
    score += _amount_score(tenant, parsed_amount)
    score += _date_score(tenant, parsed_date)
    return min(score, 100)


def find_best_match(transaction_dict, tenants):
    """
    Find the best matching tenant for a transaction.
    Returns (tenant, score) or (None, 0).
    """
    best_tenant = None
    best_score = 0
    for tenant in tenants:
        s = score_transaction(transaction_dict, tenant)
        if s > best_score:
            best_score = s
            best_tenant = tenant
    return best_tenant, best_score


AUTO_MATCH_THRESHOLD = 70
SUGGEST_THRESHOLD = 40
