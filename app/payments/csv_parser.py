import csv
import hashlib
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation


def _parse_date(val):
    formats = ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y"]
    for fmt in formats:
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(val):
    if not val:
        return None
    cleaned = val.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _row_hash(raw_date, raw_amount, raw_description):
    data = f"{raw_date}|{raw_amount}|{raw_description}"
    return hashlib.sha256(data.encode()).hexdigest()


def parse_csv(file_content):
    """
    Parse an ANZ bank statement CSV.
    Format: date, amount, description — no headers, empty rows between entries.
    Returns list of dicts for credit (positive) transactions only.
    """
    text = file_content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))

    results = []
    for row in reader:
        # Skip empty or blank rows
        if not any(cell.strip() for cell in row):
            continue
        # Need at least 3 columns
        if len(row) < 3:
            continue

        raw_date = row[0].strip()
        raw_amount = row[1].strip()
        raw_description = row[2].strip()

        parsed_date = _parse_date(raw_date) if raw_date else None
        parsed_amount = _parse_amount(raw_amount)

        # Only include credits (positive amounts = incoming rent payments)
        if parsed_amount is None or parsed_amount <= 0:
            continue

        rh = _row_hash(raw_date, raw_amount, raw_description)

        results.append({
            "raw_date": raw_date,
            "raw_amount": raw_amount,
            "raw_reference": raw_description,  # ANZ description used for tenant matching
            "raw_description": "",
            "parsed_date": parsed_date,
            "parsed_amount": parsed_amount,
            "row_hash": rh,
        })

    return results
