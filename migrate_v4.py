"""Non-destructive V4 schema migration. Makes a timestamped SQLite backup first.

Run: python migrate_v4.py
Never deletes legacy tables or rows.
"""
import os
import sqlite3
from datetime import datetime

DB = os.getenv("SHOP_DB", "shop_in_india.db")

def cols(cur, table):
    return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}

def add(cur, table, name, definition):
    if name not in cols(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

def main():
    if not os.path.exists(DB):
        raise SystemExit(f"Database not found: {DB}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{DB}.pre_v4_{stamp}.bak"
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(backup)
    src.backup(dst)
    dst.close()

    try:
        cur = src.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        # Extend existing marketplace tables safely.
        if "products" in {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            add(cur, "products", "updated_at", "TEXT")
            add(cur, "products", "created_at", "TEXT")
            add(cur, "products", "level_required", "INTEGER NOT NULL DEFAULT 1")
        if "transactions" in {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            for n, d in [
                ("unit_price", "REAL"), ("seller_amount", "REAL"),
                ("platform_fee", "REAL"), ("buyer_owner_id", "INTEGER"),
                ("seller_owner_id", "INTEGER"), ("updated_at", "TEXT"),
                ("admin_note", "TEXT")
            ]:
                add(cur, "transactions", n, d)

        cur.executescript("""
        CREATE TABLE IF NOT EXISTS product_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
          shop_id INTEGER NOT NULL, owner_id INTEGER, actor_id INTEGER NOT NULL,
          action TEXT NOT NULL, old_value TEXT, new_value TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL,
          owner_id INTEGER, actor_id INTEGER NOT NULL, action TEXT NOT NULL,
          old_value TEXT, new_value TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS level_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL,
          owner_id INTEGER NOT NULL, old_level INTEGER, new_level INTEGER NOT NULL,
          reason TEXT, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
          shop_id INTEGER NOT NULL, payment_type TEXT NOT NULL,
          amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'USDT',
          status TEXT NOT NULL DEFAULT 'pending', reference TEXT,
          admin_note TEXT, actor_id INTEGER, created_at TEXT NOT NULL,
          reviewed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS platform_settings (
          setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL,
          updated_by INTEGER, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id INTEGER,
          shop_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
          entry_type TEXT NOT NULL, amount REAL NOT NULL,
          currency TEXT NOT NULL DEFAULT 'INR', note TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS v4_orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
          buyer_shop_id INTEGER NOT NULL, seller_shop_id INTEGER NOT NULL,
          buyer_owner_id INTEGER NOT NULL, seller_owner_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL, unit_price REAL NOT NULL,
          total_amount REAL NOT NULL, platform_fee REAL NOT NULL DEFAULT 0,
          seller_amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'INR',
          status TEXT NOT NULL DEFAULT 'pending_payment',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          completed_at TEXT, admin_note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_product_audit_product ON product_audit(product_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_shop_audit_shop ON shop_audit(shop_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_level_history_shop ON level_history(shop_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_v4_orders_buyer ON v4_orders(buyer_shop_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_v4_orders_seller ON v4_orders(seller_shop_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_ledger_owner ON ledger(owner_id, id DESC);
        """)
        # Preserve existing values; INSERT OR IGNORE means admin edits survive reruns.
        levels = [
          (1, 5, 12, 1.25, 2.50),
          (2, 20, 45, 3.50, 6.50),
          (3, 150, 80, 9.80, 17.40),
          (4, 150, 200, 15.00, 25.00),
          (5, 400, 0, 20.00, 35.00),
        ]
        cur.executemany("""
          INSERT OR IGNORE INTO level_settings
          (level, activation_usdt, product_target, profit_min, profit_max)
          VALUES (?, ?, ?, ?, ?)
        """, levels)
        cur.execute("INSERT OR IGNORE INTO platform_settings(setting_key,setting_value) VALUES('platform_fee_percent','5')")
        src.commit()
        check = cur.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"Integrity check failed: {check}")
        print(f"V4 migration successful. Backup: {backup}")
        print(f"Integrity: {check}")
    except Exception:
        src.rollback()
        raise
    finally:
        src.close()

if __name__ == "__main__":
    main()
