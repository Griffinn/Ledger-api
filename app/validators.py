"""Input parsing/validation. Money is handled as Decimal -> integer cents."""
from decimal import Decimal, InvalidOperation

from .errors import BadRequest, ValidationFailed

MAX_AMOUNT = Decimal("1000000000")  # 1 billion
TX_TYPES = ("credit", "debit")
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 10


def to_cents(value):
    """Return integer cents, or raise ValueError with a human message."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("amount must be a number.")
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("amount must be a number.")
    if not d.is_finite():
        raise ValueError("amount must be a finite number.")
    if d <= 0:
        raise ValueError("amount must be greater than 0.")
    if d > MAX_AMOUNT:
        raise ValueError(f"amount must not exceed {MAX_AMOUNT}.")
    if d != d.quantize(Decimal("0.01")):
        raise ValueError("amount must have at most 2 decimal places.")
    return int((d * 100).to_integral_value())


def from_cents(cents):
    return float(Decimal(cents) / 100)


def require_json_object(request):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest("Request body must be a valid JSON object.", code="INVALID_REQUEST_BODY")
    return data


def _string(data, field, errors, required=True, max_len=255):
    v = data.get(field)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            errors.append({"field": field, "message": f"{field} is required."})
        return None
    if not isinstance(v, str):
        errors.append({"field": field, "message": f"{field} must be a string."})
        return None
    v = v.strip()
    if len(v) > max_len:
        errors.append({"field": field, "message": f"{field} must be at most {max_len} characters."})
        return None
    return v


def _id(data, field, errors):
    v = data.get(field)
    if v is None:
        errors.append({"field": field, "message": f"{field} is required."})
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        errors.append({"field": field, "message": f"{field} must be a positive integer."})
        return None
    return v


def _amount(data, errors):
    if data.get("amount") is None:
        errors.append({"field": "amount", "message": "amount is required."})
        return None
    try:
        return to_cents(data["amount"])
    except ValueError as e:
        errors.append({"field": "amount", "message": str(e), "code": "INVALID_AMOUNT"})
        return None


def _finish(errors, values):
    if errors:
        raise ValidationFailed("Request validation failed.", details=errors)
    return values


def parse_account(data):
    errors = []
    name = _string(data, "name", errors, max_len=100)
    return _finish(errors, {"name": name})


def parse_transaction(data):
    errors = []
    account_id = _id(data, "account_id", errors)
    t = data.get("type")
    if not isinstance(t, str) or t.strip().lower() not in TX_TYPES:
        errors.append({"field": "type", "code": "INVALID_TRANSACTION_TYPE",
                       "message": "type must be either 'credit' or 'debit'."})
        t = None
    else:
        t = t.strip().lower()
    amount = _amount(data, errors)
    reference_id = _string(data, "reference_id", errors, max_len=100)
    description = _string(data, "description", errors, required=False, max_len=500) or ""
    return _finish(errors, dict(account_id=account_id, type=t, amount=amount,
                                reference_id=reference_id, description=description))


def parse_transfer(data):
    errors = []
    src = _id(data, "from_account_id", errors)
    dst = _id(data, "to_account_id", errors)
    if src and dst and src == dst:
        errors.append({"field": "to_account_id",
                       "message": "to_account_id must differ from from_account_id."})
    amount = _amount(data, errors)
    reference_id = _string(data, "reference_id", errors, max_len=100)
    description = _string(data, "description", errors, required=False, max_len=500) or ""
    return _finish(errors, dict(from_account_id=src, to_account_id=dst, amount=amount,
                                reference_id=reference_id, description=description))


def parse_path_id(raw, label="account"):
    if not raw.isdigit() or int(raw) <= 0:
        raise BadRequest(f"Invalid {label} ID '{raw}': must be a positive integer.",
                         code=f"INVALID_{label.upper()}_ID")
    return int(raw)


def parse_pagination(args):
    def one(name, default, maximum=None):
        raw = args.get(name)
        if raw is None:
            return default
        if not raw.isdigit() or int(raw) < 1:
            raise BadRequest(f"'{name}' must be a positive integer.", code="INVALID_PAGINATION",
                             details=[{"field": name, "message": "must be a positive integer"}])
        v = int(raw)
        if maximum and v > maximum:
            raise BadRequest(f"'{name}' must be at most {maximum}.", code="INVALID_PAGINATION",
                             details=[{"field": name, "message": f"must be <= {maximum}"}])
        return v

    return one("page", 1), one("limit", DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
