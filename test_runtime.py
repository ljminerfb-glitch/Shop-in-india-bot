"""Isolated runtime smoke tests. Uses a temporary SQLite DB and mocked Telegram objects."""
import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

TMP = tempfile.TemporaryDirectory(prefix="shopindia_runtime_")
DB_PATH = str(Path(TMP.name) / "runtime.db")
os.environ["SHOP_DB"] = DB_PATH
os.environ["ADMIN_ID"] = "999999"

import bot_v4 as v4
import central_catalog as cc


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Message:
    def __init__(self):
        self.replies = []
    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeBot:
    async def send_message(self, *args, **kwargs):
        return None


def fixture():
    c = sqlite3.connect(DB_PATH)
    c.executescript("""
    CREATE TABLE users(tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT, role TEXT, status TEXT, language TEXT);
    CREATE TABLE shops(id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, shop_name TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT, password_hash TEXT, level INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE products(id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL, title TEXT, description TEXT, price REAL, currency TEXT, active INTEGER, stock INTEGER);
    CREATE TABLE transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,buyer_shop_id INTEGER,seller_shop_id INTEGER,product_id INTEGER,quantity INTEGER,total_amount REAL,currency TEXT,status TEXT,created_at TEXT,completed_at TEXT);
    CREATE TABLE orders(id INTEGER PRIMARY KEY AUTOINCREMENT,buyer_id INTEGER,product_id INTEGER,quantity INTEGER,status TEXT,created_at TEXT);
    CREATE TABLE payment_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,shop_id INTEGER,payment_type TEXT,level INTEGER,amount REAL,currency TEXT,network TEXT,screenshot_file_id TEXT,tx_hash TEXT,status TEXT,admin_note TEXT,created_at TEXT,reviewed_at TEXT);
    CREATE TABLE withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,shop_id INTEGER,amount REAL,currency TEXT,network TEXT,wallet_address TEXT,status TEXT,admin_note TEXT,created_at TEXT,reviewed_at TEXT);
    CREATE TABLE admin_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER,owner_id INTEGER,action TEXT,details TEXT,created_at TEXT);
    CREATE TABLE level_settings(level INTEGER PRIMARY KEY,activation_usdt REAL,product_target INTEGER,profit_min REAL,profit_max REAL);
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(101,'Runtime Shop','approved','2026-01-01',1);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(101,'2026-01-01','shop_owner','approved');
    INSERT INTO level_settings VALUES(1,5,12,1.25,2.5);
    """)
    c.commit()
    c.close()
    v4.DB = DB_PATH
    v4.v3.DB = DB_PATH
    cc.setup()


def run_buy(owner, pid, qty):
    message = Message()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=owner),
        effective_message=message,
    )
    context = SimpleNamespace(args=[str(pid), str(qty)], bot=FakeBot())
    asyncio.run(cc.buy(update, context))
    return message.replies


def main():
    fixture()
    c = v4.db()
    product = c.execute("SELECT id FROM central_catalog ORDER BY id LIMIT 1").fetchone()
    pid = product["id"]
    c.execute("UPDATE central_catalog SET price_usdt=2.5, stock=5 WHERE id=?", (pid,))
    c.execute("""INSERT INTO ledger(shop_id,owner_id,entry_type,amount,currency,note,created_at)
                  VALUES(1,101,'deposit_credit',10,'USDT','test deposit',?)""", (v4.now(),))
    c.commit()
    require(cc.balance(c, 101) == 10.0, "USDT deposit balance calculation failed")
    c.execute("""INSERT INTO ledger(shop_id,owner_id,entry_type,amount,currency,note,created_at)
                  VALUES(1,101,'level_activation_wallet_credit',2.5,'USDT','test 50% level cashback',?)""", (v4.now(),))
    c.commit()
    require(cc.balance(c, 101) == 12.5, "50% level activation cashback was not included in wallet balance")
    c.close()

    replies = run_buy(101, pid, 2)
    require(any("Purchase successful" in x for x in replies), "Successful central purchase did not return success")
    c = v4.db()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == 3,
            "Central stock was not decremented correctly")
    require(c.execute("SELECT COUNT(*) FROM central_orders WHERE buyer_owner_id=101 AND quantity=2").fetchone()[0] == 1,
            "Central order was not recorded")
    require(c.execute("SELECT COUNT(*) FROM central_inventory WHERE owner_id=101 AND remaining_quantity=2").fetchone()[0] == 1,
            "Buyer inventory was not created")
    require(abs(cc.balance(c, 101) - 5.0) < 1e-8, "Wallet debit/balance after purchase is incorrect")
    c.close()

    replies = run_buy(101, pid, 3)
    require(any("Insufficient USDT wallet balance" in x for x in replies),
            "Insufficient-balance purchase was not rejected")
    c = v4.db()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == 3,
            "Stock changed after rejected insufficient-balance purchase")
    c.close()

    replies = run_buy(101, pid, 4)
    require(any("Insufficient central stock" in x for x in replies),
            "Insufficient-stock purchase was not rejected")
    c = v4.db()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == 3,
            "Stock changed after rejected insufficient-stock purchase")
    require(c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity check failed")
    c.close()

    print("PASS: isolated V4-compatible database fixture")
    print("PASS: central catalogue schema setup")
    print("PASS: USDT wallet balance calculation")
    print("PASS: successful central purchase, order, inventory and wallet debit")
    print("PASS: insufficient balance rejected without stock mutation")
    print("PASS: insufficient stock rejected without stock mutation")
    print("PASS: SQLite integrity_check=ok")
    print("NOTE: Telegram network delivery and live admin callbacks are intentionally mocked, not exercised.")


if __name__ == "__main__":
    try:
        main()
    finally:
        TMP.cleanup()
