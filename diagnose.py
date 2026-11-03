#!/usr/bin/env python3
"""Read-only diagnostic report for Shop-in-india-bot central catalogue.
Uses SHOP_DB when set; otherwise uses bot_v4.DB. Never writes to the database.
"""
from __future__ import annotations
import ast
import importlib
import os
import sqlite3
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHECKS = []
ERRORS = 0
WARNINGS = 0

def report(status, name, detail=""):
    global ERRORS, WARNINGS
    if status == "FAIL":
        ERRORS += 1
    elif status == "WARN":
        WARNINGS += 1
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

def check_file_syntax(filename):
    path = ROOT / filename
    if not path.is_file():
        report("FAIL", f"{filename} exists", "file missing")
        return
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        report("PASS", f"{filename} syntax")
    except Exception as exc:
        report("FAIL", f"{filename} syntax", repr(exc))

def main():
    print("=" * 68)
    print("SHOP IN INDIA — READ-ONLY DIAGNOSTIC")
    print("No database rows are inserted, updated, or deleted.")
    print("=" * 68)

    for filename in ("bot_v3.py", "bot_v4.py", "bot_v5.py", "central_catalog.py", "audit_dashboard.py"):
        check_file_syntax(filename)

    try:
        importlib.import_module("telegram")
        report("PASS", "python-telegram-bot import")
    except Exception as exc:
        report("FAIL", "python-telegram-bot import", repr(exc))

    try:
        import bot_v4
        import central_catalog
        import audit_dashboard
        report("PASS", "bot_v4, central_catalog and audit_dashboard imports")
    except Exception as exc:
        report("FAIL", "application imports", repr(exc))
        print(traceback.format_exc())
        return finish()

    db_path = Path(os.getenv("SHOP_DB") or bot_v4.DB).expanduser()
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    print(f"Database selected: {db_path}")
    if not db_path.is_file():
        report("FAIL", "database file", "not found")
        return finish()

    try:
        uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        report("PASS" if integrity == "ok" else "FAIL", "SQLite integrity", integrity)

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"shops", "ledger", "central_catalog", "central_orders", "admin_audit"}
        missing = sorted(required - tables)
        report("PASS" if not missing else "FAIL", "required database tables",
               "all present" if not missing else "missing: " + ", ".join(missing))

        if not missing:
            products = conn.execute("SELECT * FROM central_catalog ORDER BY id").fetchall()
            report("PASS" if len(products) == 12 else "FAIL", "central product count",
                   f"found {len(products)}; expected 12")
            keys = [r["product_key"] for r in products]
            report("PASS" if len(keys) == len(set(keys)) else "FAIL", "product keys unique")
            missing_images = []
            for p in products:
                image = p["image_path"]
                if not image or not (ROOT / image).is_file():
                    missing_images.append(f"#{p['id']} {p['title']} ({image})")
            report("PASS" if not missing_images else "FAIL", "product image files",
                   "all present" if not missing_images else "; ".join(missing_images))

            ledger_cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger)")}
            ledger_needed = {"shop_id", "owner_id", "entry_type", "amount", "currency", "created_at"}
            report("PASS" if ledger_needed <= ledger_cols else "FAIL", "ledger schema",
                   "required USDT ledger columns present" if ledger_needed <= ledger_cols else
                   "missing: " + ", ".join(sorted(ledger_needed - ledger_cols)))

            order_cols = {r[1] for r in conn.execute("PRAGMA table_info(central_orders)")}
            order_needed = {"catalog_id", "buyer_shop_id", "buyer_owner_id", "quantity",
                            "unit_price_usdt", "total_usdt", "status", "created_at"}
            report("PASS" if order_needed <= order_cols else "FAIL", "central order schema",
                   "required columns present" if order_needed <= order_cols else
                   "missing: " + ", ".join(sorted(order_needed - order_cols)))

            shops = conn.execute("SELECT id,owner_id,status,level FROM shops ORDER BY id").fetchall()
            eligible = [dict(s) for s in shops if s["status"] == "approved" and int(s["level"]) == 1]
            report("PASS" if eligible else "WARN", "approved Level 1 test shop",
                   f"{len(eligible)} found" if eligible else "none found; purchase flow cannot be live-tested safely")
            order_count = conn.execute("SELECT COUNT(*) FROM central_orders").fetchone()[0]
            print(f"Existing central orders (read-only): {order_count}")

            usdt_rows = conn.execute(
                "SELECT owner_id,entry_type,amount,currency FROM ledger WHERE currency='USDT' ORDER BY id"
            ).fetchall()
            print(f"Existing USDT ledger entries (read-only): {len(usdt_rows)}")
        conn.close()
    except Exception as exc:
        report("FAIL", "database inspection", repr(exc))
        print(traceback.format_exc())

    # Static checks: ensure the critical safeguards exist in the current source.
    try:
        source = (ROOT / "central_catalog.py").read_text(encoding="utf-8")
        safeguards = {
            "Level 1 + approved purchase gate": 'shop["status"]!="approved"' in source and 'int(shop["level"])!=1' in source,
            "USDT-only purchase debit": "'central_purchase_debit'" in source and "'USDT'" in source,
            "shared stock decrement": "stock=stock-?" in source,
            "refund stock restoration": "stock=stock+?" in source,
            "refund credit entry": "'central_purchase_refund_credit'" in source,
            "one-time paid-status guard": 'order["status"]!="paid"' in source,
            "transaction rollback protection": "c.rollback()" in source,
        }
        for name, ok in safeguards.items():
            report("PASS" if ok else "FAIL", name)
    except Exception as exc:
        report("FAIL", "static safeguard scan", repr(exc))

    # Check that the central callback and text-state handlers are wired in bot_v5.
    try:
        v5 = (ROOT / "bot_v5.py").read_text(encoding="utf-8")
        report("PASS" if "central_catalog" in v5 else "FAIL", "central catalogue integration in bot_v5")
        report("PASS" if 'pattern=r"^cc:' in v5 or "cc:" in v5 else "FAIL",
               "central callback references in bot_v5")
        report("PASS" if "central_admin_edit" in v5 else "FAIL",
               "admin price/stock edit state in bot_v5")
        report("PASS" if "import audit_dashboard as audit" in v5 and "FULL AUDIT LOG" in v5 else "FAIL",
               "full audit dashboard integration in bot_v5")
        audit_source = (ROOT / "audit_dashboard.py").read_text(encoding="utf-8")
        audit_sources = ("admin_audit", "shop_audit", "product_audit", "level_history", "payment_history", "withdrawals", "ledger", "v4_orders", "central_orders")
        report("PASS" if all(x in audit_source for x in audit_sources) else "FAIL",
               "unified audit data sources")
        report("PASS" if "audit_callback" in v5 and "audit:(all|payments|levels|wallet|orders|products)" in v5 else "FAIL",
               "audit filters and pagination callbacks")
    except Exception as exc:
        report("FAIL", "bot_v5 integration scan", repr(exc))

    return finish()

def finish():
    print("-" * 68)
    print(f"RESULT: {ERRORS} FAIL | {WARNINGS} WARN")
    if ERRORS:
        print("DIAGNOSIS: Errors found. Send the complete output to ChatGPT.")
        return 1
    if WARNINGS:
        print("DIAGNOSIS: Static/database checks passed with warnings; missing test data may prevent a full purchase test.")
        return 0
    print("DIAGNOSIS: All included read-only checks passed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
