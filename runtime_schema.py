"""Idempotent, non-destructive runtime schema bootstrap for the V5 bot.

All changes are additive: existing rows and tables are preserved. The helper is
safe to run on every worker start and avoids requiring a manual migration step.
"""
import os
import sqlite3


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn, table, definitions):
    existing = _columns(conn, table)
    for name, definition in definitions:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ensure_schema(db_path=None):
    path = db_path or os.getenv("SHOP_DB", "shop_in_india.db")
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")

        # Core V3 tables are created by bot_v3.setup(); extend them safely.
        if "shops" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            _add_columns(conn, "shops", [
                ("password_hash", "TEXT"),
                ("level", "INTEGER NOT NULL DEFAULT 0"),
            ])
        if "products" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            _add_columns(conn, "products", [
                ("stock", "INTEGER NOT NULL DEFAULT 1"),
                ("level_required", "INTEGER NOT NULL DEFAULT 1"),
                ("created_at", "TEXT"),
                ("updated_at", "TEXT"),
            ])

        conn.execute("""CREATE TABLE IF NOT EXISTS owner_wallets (
            owner_id INTEGER PRIMARY KEY, network TEXT NOT NULL, address TEXT NOT NULL,
            updated_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0)""")
        _add_columns(conn, "owner_wallets", [("verified", "INTEGER NOT NULL DEFAULT 0")])

        conn.execute("""CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, shop_id INTEGER NOT NULL,
            payment_type TEXT NOT NULL, level INTEGER, amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USDT', network TEXT NOT NULL,
            screenshot_file_id TEXT, tx_hash TEXT, transaction_hash TEXT,
            status TEXT NOT NULL DEFAULT 'pending', admin_note TEXT,
            created_at TEXT NOT NULL, reviewed_at TEXT)""")
        _add_columns(conn, "payment_requests", [
            ("transaction_hash", "TEXT"), ("tx_hash", "TEXT"),
            ("screenshot_file_id", "TEXT"), ("admin_note", "TEXT"),
            ("reviewed_at", "TEXT"), ("level", "INTEGER"),
        ])

        conn.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, shop_id INTEGER NOT NULL,
            amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'USDT', network TEXT NOT NULL,
            wallet_address TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT, created_at TEXT NOT NULL, reviewed_at TEXT,
            amount_inr REAL, exchange_rate REAL)""")
        _add_columns(conn, "withdrawals", [
            ("amount_inr", "REAL"), ("exchange_rate", "REAL"),
            ("admin_note", "TEXT"), ("reviewed_at", "TEXT"),
        ])

        conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, buyer_shop_id INTEGER NOT NULL,
            seller_shop_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            total_amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'USDT',
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, completed_at TEXT,
            unit_price REAL, seller_amount REAL, platform_fee REAL,
            buyer_owner_id INTEGER, seller_owner_id INTEGER, updated_at TEXT, admin_note TEXT)""")
        _add_columns(conn, "transactions", [
            ("unit_price", "REAL"), ("seller_amount", "REAL"), ("platform_fee", "REAL"),
            ("buyer_owner_id", "INTEGER"), ("seller_owner_id", "INTEGER"),
            ("updated_at", "TEXT"), ("admin_note", "TEXT"),
        ])

        conn.execute("""CREATE TABLE IF NOT EXISTS admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
            owner_id INTEGER, action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS booster_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL, amount REAL NOT NULL DEFAULT 10,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS level_settings (
            level INTEGER PRIMARY KEY, activation_usdt REAL NOT NULL,
            product_target INTEGER NOT NULL, profit_min REAL NOT NULL, profit_max REAL NOT NULL)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS product_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            shop_id INTEGER NOT NULL, owner_id INTEGER, actor_id INTEGER NOT NULL,
            action TEXT NOT NULL, old_value TEXT, new_value TEXT, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS shop_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL,
            owner_id INTEGER, actor_id INTEGER NOT NULL, action TEXT NOT NULL,
            old_value TEXT, new_value TEXT, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS level_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL, old_level INTEGER, new_level INTEGER NOT NULL,
            reason TEXT, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS payment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
            shop_id INTEGER NOT NULL, payment_type TEXT NOT NULL, amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USDT', status TEXT NOT NULL DEFAULT 'pending',
            reference TEXT, admin_note TEXT, actor_id INTEGER, created_at TEXT NOT NULL,
            reviewed_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS platform_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL,
            updated_by INTEGER, updated_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id INTEGER,
            shop_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, entry_type TEXT NOT NULL,
            amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'INR',
            note TEXT, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS v4_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            buyer_shop_id INTEGER NOT NULL, seller_shop_id INTEGER NOT NULL,
            buyer_owner_id INTEGER NOT NULL, seller_owner_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL, unit_price REAL NOT NULL, total_amount REAL NOT NULL,
            platform_fee REAL NOT NULL DEFAULT 0, seller_amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'INR', status TEXT NOT NULL DEFAULT 'pending_payment',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, admin_note TEXT)""")

        defaults = [
            (1, 5, 12, 1.25, 2.50), (2, 20, 45, 3.50, 6.50),
            (3, 150, 80, 9.80, 17.40), (4, 150, 200, 15.00, 25.00),
            (5, 400, 0, 20.00, 35.00),
        ]
        conn.executemany("""INSERT OR IGNORE INTO level_settings
            (level,activation_usdt,product_target,profit_min,profit_max)
            VALUES(?,?,?,?,?)""", defaults)
        conn.execute("INSERT OR IGNORE INTO platform_settings(setting_key,setting_value) VALUES('platform_fee_percent','5')")
        conn.execute("INSERT OR IGNORE INTO platform_settings(setting_key,setting_value) VALUES('usdt_inr_rate','110')")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_product_audit_product ON product_audit(product_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shop_audit_shop ON shop_audit(shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_level_history_shop ON level_history(shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v4_orders_buyer ON v4_orders(buyer_shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v4_orders_seller ON v4_orders(seller_shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_owner ON ledger(owner_id,id DESC)")
        conn.commit()
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {check}")
        return check
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
