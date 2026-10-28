import os
import sqlite3
import tempfile
import threading
import unittest

from app import create_app


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.app = create_app({"DATABASE": self.path, "TESTING": False})
        self.c = self.app.test_client()

    def tearDown(self):
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + ext)
            except FileNotFoundError:
                pass

    def account(self, name="Acme"):
        return self.c.post("/accounts", json={"name": name}).get_json()["id"]

    def tx(self, aid, type_, amount, ref, **kw):
        return self.c.post("/transactions", json={"account_id": aid, "type": type_,
                                                  "amount": amount, "reference_id": ref, **kw})

    def balance(self, aid):
        return self.c.get(f"/accounts/{aid}").get_json()["balance"]

    def sql(self, query, params=()):
        """Run a read-only query on a short-lived connection that is always closed
        (an open handle prevents deleting the temp DB file on Windows)."""
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()


class AccountTests(Base):
    def test_create_and_get(self):
        r = self.c.post("/accounts", json={"name": "  Alice "})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["name"], "Alice")
        self.assertEqual(r.get_json()["balance"], 0)

    def test_name_required(self):
        for body in ({}, {"name": ""}, {"name": "   "}, {"name": 5}):
            r = self.c.post("/accounts", json=body)
            self.assertEqual(r.status_code, 422, body)
            self.assertEqual(r.get_json()["error"]["code"], "VALIDATION_ERROR")

    def test_invalid_body(self):
        r = self.c.post("/accounts", data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "INVALID_REQUEST_BODY")

    def test_not_found_and_bad_id(self):
        self.assertEqual(self.c.get("/accounts/999").status_code, 404)
        r = self.c.get("/accounts/abc")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "INVALID_ACCOUNT_ID")


class TransactionTests(Base):
    def test_spec_example_balance(self):
        a = self.account()
        for t, amt, ref in [("credit", 100, "1"), ("credit", 50, "2"),
                            ("debit", 30, "3"), ("debit", 20, "4")]:
            self.assertEqual(self.tx(a, t, amt, ref).status_code, 201)
        self.assertEqual(self.balance(a), 100)

    def test_validation(self):
        a = self.account()
        cases = [
            ({"account_id": a, "type": "refund", "amount": 5, "reference_id": "x"}, "type"),
            ({"account_id": a, "type": "credit", "amount": 0, "reference_id": "x"}, "amount"),
            ({"account_id": a, "type": "credit", "amount": -5, "reference_id": "x"}, "amount"),
            ({"account_id": a, "type": "credit", "amount": "abc", "reference_id": "x"}, "amount"),
            ({"account_id": a, "type": "credit", "amount": 1.234, "reference_id": "x"}, "amount"),
            ({"account_id": a, "type": "credit", "amount": True, "reference_id": "x"}, "amount"),
            ({"account_id": a, "type": "credit", "amount": 5}, "reference_id"),
            ({"account_id": a, "type": "credit", "amount": 5, "reference_id": " "}, "reference_id"),
            ({"type": "credit", "amount": 5, "reference_id": "x"}, "account_id"),
            ({"account_id": "1", "type": "credit", "amount": 5, "reference_id": "x"}, "account_id"),
        ]
        for body, field in cases:
            r = self.c.post("/transactions", json=body)
            self.assertEqual(r.status_code, 422, body)
            fields = [d["field"] for d in r.get_json()["error"]["details"]]
            self.assertIn(field, fields, body)
        self.assertEqual(self.balance(a), 0)

    def test_account_must_exist(self):
        r = self.tx(999, "credit", 10, "x")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "ACCOUNT_NOT_FOUND")

    def test_insufficient_balance_does_not_change_state(self):
        a = self.account()
        self.tx(a, "credit", 50, "c1")
        r = self.tx(a, "debit", 50.01, "d1")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["code"], "INSUFFICIENT_BALANCE")
        self.assertEqual(self.balance(a), 50)
        self.assertEqual(self.c.get(f"/accounts/{a}/transactions").get_json()["pagination"]["total_records"], 1)
        self.assertEqual(self.tx(a, "debit", 50, "d2").status_code, 201)  # exact balance ok
        self.assertEqual(self.balance(a), 0)

    def test_duplicate_reference_per_account(self):
        a, b = self.account("A"), self.account("B")
        self.assertEqual(self.tx(a, "credit", 10, "ref").status_code, 201)
        r = self.tx(a, "credit", 10, "ref")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "DUPLICATE_TRANSACTION")
        self.assertEqual(self.balance(a), 10)  # not double-applied
        self.assertEqual(self.tx(b, "credit", 10, "ref").status_code, 201)  # other account OK

    def test_decimal_precision(self):
        a = self.account()
        for i in range(10):
            self.tx(a, "credit", 0.1, f"r{i}")
        self.assertEqual(self.balance(a), 1.0)

    def test_immutable_via_api(self):
        a = self.account()
        tid = self.tx(a, "credit", 10, "r").get_json()["transaction"]["id"]
        for method in ("put", "patch", "delete"):
            r = getattr(self.c, method)(f"/transactions/{tid}", json={"amount": 1})
            self.assertEqual(r.status_code, 405)
            self.assertEqual(r.get_json()["error"]["code"], "METHOD_NOT_ALLOWED")
        self.assertEqual(self.c.get(f"/transactions/{tid}").get_json()["amount"], 10)

    def test_immutable_at_db_level(self):
        a = self.account()
        self.tx(a, "credit", 10, "r")
        conn = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE transactions SET amount = 999")
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM transactions")
        finally:
            conn.close()

    def test_balance_equals_sum_of_transactions(self):
        a = self.account()
        self.tx(a, "credit", 100, "1"); self.tx(a, "debit", 30.5, "2"); self.tx(a, "debit", 999, "3")
        s = self.sql("SELECT COALESCE(SUM(CASE type WHEN 'credit' THEN amount ELSE -amount END),0) "
                     "FROM transactions WHERE account_id=?", (a,))[0][0]
        b = self.sql("SELECT balance FROM accounts WHERE id=?", (a,))[0][0]
        self.assertEqual(s, b)

    def test_concurrent_debits_never_overdraw(self):
        a = self.account()
        self.tx(a, "credit", 100, "seed")
        results = []

        def worker(i):
            c = self.app.test_client()
            results.append(c.post("/transactions", json={
                "account_id": a, "type": "debit", "amount": 10, "reference_id": f"d{i}"}).status_code)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(results.count(201), 10)
        self.assertEqual(results.count(422), 15)
        self.assertEqual(self.balance(a), 0)


class PaginationTests(Base):
    def setUp(self):
        super().setUp()
        self.a = self.account()
        for i in range(25):
            self.tx(self.a, "credit", 1, f"r{i}")

    def get(self, qs=""):
        return self.c.get(f"/accounts/{self.a}/transactions{qs}")

    def test_defaults_and_metadata(self):
        j = self.get().get_json()
        self.assertEqual(j["pagination"], {"current_page": 1, "page_size": 10,
                                           "total_records": 25, "total_pages": 3})
        self.assertEqual(len(j["data"]), 10)

    def test_latest_first(self):
        j = self.get("?limit=100").get_json()
        refs = [t["reference_id"] for t in j["data"]]
        self.assertEqual(refs, [f"r{i}" for i in range(24, -1, -1)])
        stamps = [t["created_at"] for t in j["data"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_last_and_out_of_range_page(self):
        self.assertEqual(len(self.get("?page=3&limit=10").get_json()["data"]), 5)
        j = self.get("?page=99").get_json()
        self.assertEqual(j["data"], [])
        self.assertEqual(j["pagination"]["total_pages"], 3)

    def test_invalid_params(self):
        for qs in ("?page=0", "?page=-1", "?page=x", "?limit=0", "?limit=101", "?limit=1.5"):
            r = self.get(qs)
            self.assertEqual(r.status_code, 400, qs)
            self.assertEqual(r.get_json()["error"]["code"], "INVALID_PAGINATION")

    def test_unknown_account(self):
        self.assertEqual(self.c.get("/accounts/999/transactions").status_code, 404)


class TransferTests(Base):
    def transfer(self, a, b, amt, ref="t1"):
        return self.c.post("/transfers", json={"from_account_id": a, "to_account_id": b,
                                               "amount": amt, "reference_id": ref})

    def test_success(self):
        a, b = self.account("A"), self.account("B")
        self.tx(a, "credit", 100, "s1"); self.tx(b, "credit", 50, "s2")
        r = self.transfer(a, b, 30)
        self.assertEqual(r.status_code, 201)
        self.assertEqual((self.balance(a), self.balance(b)), (70, 80))
        j = r.get_json()
        self.assertEqual(j["debit_transaction"]["type"], "debit")
        self.assertEqual(j["credit_transaction"]["transfer_id"], j["id"])

    def test_insufficient_rolls_back(self):
        a, b = self.account("A"), self.account("B")
        self.tx(a, "credit", 10, "s1")
        r = self.transfer(a, b, 30)
        self.assertEqual(r.status_code, 422)
        self.assertEqual((self.balance(a), self.balance(b)), (10, 0))
        self.assertEqual(self.sql("SELECT COUNT(*) FROM transfers")[0][0], 0)

    def test_missing_destination_rolls_back(self):
        a = self.account()
        self.tx(a, "credit", 10, "s1")
        r = self.transfer(a, 999, 5)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.balance(a), 10)

    def test_partial_failure_rolls_back_debit(self):
        """Credit leg fails (duplicate ref on destination) after the debit leg succeeded."""
        a, b = self.account("A"), self.account("B")
        self.tx(a, "credit", 100, "s1"); self.tx(b, "credit", 1, "taken")
        r = self.transfer(a, b, 30, ref="taken")  # dest already has 'taken'; source does not
        self.assertEqual(r.status_code, 409)
        self.assertEqual((self.balance(a), self.balance(b)), (100, 1))
        self.assertEqual(self.sql("SELECT COUNT(*) FROM transactions WHERE account_id=?", (a,))[0][0], 1)

    def test_validation(self):
        a = self.account()
        r = self.transfer(a, a, 5)
        self.assertEqual(r.status_code, 422)
        r = self.c.post("/transfers", json={})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
