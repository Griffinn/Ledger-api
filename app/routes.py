from flask import Blueprint, jsonify, request

from . import services
from .db import get_db
from .validators import (parse_account, parse_pagination, parse_path_id, parse_transaction,
                         parse_transfer, require_json_object)

bp = Blueprint("api", __name__)


def paginated(items, total, page, limit):
    return jsonify({
        "data": items,
        "pagination": {
            "current_page": page,
            "page_size": limit,
            "total_records": total,
            "total_pages": (total + limit - 1) // limit,
        },
    })


@bp.get("/health")
def health():
    return jsonify({"status": "ok"})


# ---- accounts ----
@bp.post("/accounts")
def create_account():
    v = parse_account(require_json_object(request))
    return jsonify(services.create_account(get_db(), v["name"])), 201


@bp.get("/accounts")
def list_accounts():
    page, limit = parse_pagination(request.args)
    items, total = services.list_accounts(get_db(), page, limit)
    return paginated(items, total, page, limit)


@bp.get("/accounts/<account_id>")
def get_account(account_id):
    return jsonify(services.get_account(get_db(), parse_path_id(account_id)))


@bp.get("/accounts/<account_id>/transactions")
def list_account_transactions(account_id):
    aid = parse_path_id(account_id)
    page, limit = parse_pagination(request.args)
    items, total = services.list_transactions(get_db(), aid, page, limit)
    return paginated(items, total, page, limit)


# ---- transactions (create + read only; PUT/PATCH/DELETE -> 405) ----
@bp.post("/transactions")
def create_transaction():
    v = parse_transaction(require_json_object(request))
    tx, account = services.create_transaction(
        get_db(), v["account_id"], v["type"], v["amount"], v["description"], v["reference_id"])
    return jsonify({"transaction": tx, "account": account}), 201


@bp.get("/transactions/<tx_id>")
def get_transaction(tx_id):
    return jsonify(services.get_transaction(get_db(), parse_path_id(tx_id, "transaction")))


# ---- transfers (bonus) ----
@bp.post("/transfers")
def create_transfer():
    v = parse_transfer(require_json_object(request))
    result = services.create_transfer(get_db(), v["from_account_id"], v["to_account_id"],
                                      v["amount"], v["reference_id"], v["description"])
    return jsonify(result), 201
