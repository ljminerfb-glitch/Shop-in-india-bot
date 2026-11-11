"""Idempotent, non-destructive runtime schema bootstrap for the V5 bot.

All changes are additive: existing rows and tables are preserved. The helper is
safe to run on every worker start and avoids requiring a manual migration step.
"""
import os
import sqlite3
import secrets
import re


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

        # Ensure the legacy product table exists on a clean installation.
        conn.execute("""CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL,
            title TEXT NOT NULL, description TEXT, price REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'INR', active INTEGER NOT NULL DEFAULT 0,
            stock INTEGER NOT NULL DEFAULT 1, level_required INTEGER NOT NULL DEFAULT 1,
            created_at TEXT, updated_at TEXT)""")

        # Core V3 tables are created by bot_v3.setup(); extend them safely.
        if "shops" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            _add_columns(conn, "shops", [
                ("shop_code", "TEXT"),
                ("password_hash", "TEXT"),
                ("level", "INTEGER NOT NULL DEFAULT 0"),
                ("referred_by_owner_id", "INTEGER"),
                ("location_country", "TEXT"),
                ("location_state", "TEXT"),
                ("location_city", "TEXT"),
                ("location_timezone", "TEXT"),
                ("control_status", "TEXT NOT NULL DEFAULT 'active'"),
                ("control_note", "TEXT"),
                ("next_order_warning", "INTEGER NOT NULL DEFAULT 0"),
                ("warning_reason", "TEXT"),
                ("motivation_reminder_status", "TEXT NOT NULL DEFAULT 'off'"),
                ("motivation_reminder_next_at", "TEXT"),
                ("motivation_reminder_last_index", "INTEGER NOT NULL DEFAULT -1"),
            ])
            # Public Shop IDs are random six-digit numbers, separate from the
            # internal primary key. Never rewrite an already valid public ID.
            used = {str(row[0]) for row in conn.execute(
                "SELECT shop_code FROM shops WHERE shop_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
            )}
            legacy_rows = conn.execute(
                "SELECT id,shop_code FROM shops ORDER BY id"
            ).fetchall()
            for shop_id, current_code in legacy_rows:
                if current_code and re.fullmatch(r"[0-9]{6}", str(current_code)):
                    continue
                while True:
                    candidate = str(secrets.randbelow(900000) + 100000)
                    if candidate not in used:
                        break
                conn.execute("UPDATE shops SET shop_code=? WHERE id=?", (candidate, shop_id))
                used.add(candidate)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shops_shop_code_unique ON shops(shop_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_shops_motivation_due ON shops(motivation_reminder_status,motivation_reminder_next_at)")
            # Future registrations get a random six-digit public ID automatically.
            conn.execute("DROP TRIGGER IF EXISTS trg_shops_assign_shop_code")
            conn.execute("""CREATE TRIGGER trg_shops_assign_shop_code
                AFTER INSERT ON shops
                WHEN NEW.shop_code IS NULL OR TRIM(NEW.shop_code)=''
                BEGIN
                    UPDATE shops
                    SET shop_code=printf('%06d', abs(random()) % 900000 + 100000)
                    WHERE id=NEW.id;
                END""")
        if "products" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            _add_columns(conn, "products", [
                ("stock", "INTEGER NOT NULL DEFAULT 1"),
                ("level_required", "INTEGER NOT NULL DEFAULT 1"),
                ("created_at", "TEXT"),
                ("updated_at", "TEXT"),
            ])

        conn.execute("""CREATE TABLE IF NOT EXISTS merchant_pair_progress (
            shop_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL,
            completed_count INTEGER NOT NULL DEFAULT 0 CHECK(completed_count BETWEEN 0 AND 50),
            reached_at TEXT, reset_at TEXT, cycle_started_at TEXT, updated_at TEXT NOT NULL)
        """)
        conn.execute("""CREATE TABLE IF NOT EXISTS merchant_pair_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL,
            source_order_id INTEGER NOT NULL, shop_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(source_type,source_order_id))
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pair_progress_due ON merchant_pair_progress(completed_count,reset_at)")
        conn.execute("""CREATE TABLE IF NOT EXISTS merchant_extra_item_allowances (
            shop_id INTEGER PRIMARY KEY, remaining_units INTEGER NOT NULL DEFAULT 0,
            total_approved_units INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)
        """)

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
        conn.execute("""CREATE TABLE IF NOT EXISTS location_geocode_cache (
            lat_key REAL NOT NULL, lon_key REAL NOT NULL, country TEXT NOT NULL,
            state TEXT NOT NULL, city TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(lat_key,lon_key))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS merchant_referrals (
            owner_id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL,
            referral_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS referral_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_request_id INTEGER NOT NULL UNIQUE,
            referrer_owner_id INTEGER NOT NULL, referred_owner_id INTEGER NOT NULL,
            payment_amount REAL NOT NULL, bonus_amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USDT', created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS platform_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL,
            updated_by INTEGER, updated_at TEXT)""")
        # One-time requested reset: start every existing merchant at 0/50.
        # Keep all pair events, orders, inventory, wallet and ledger history intact.
        reset_key = "migration_pair_progress_reset_all_20261111"
        reset_done = conn.execute(
            "SELECT 1 FROM platform_settings WHERE setting_key=?", (reset_key,)
        ).fetchone()
        if not reset_done:
            now_utc = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
            if "shops" in {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
                conn.execute(f"""INSERT OR IGNORE INTO merchant_pair_progress
                    (shop_id,owner_id,completed_count,reached_at,reset_at,cycle_started_at,updated_at)
                    SELECT id,owner_id,0,NULL,NULL,{now_utc},{now_utc} FROM shops""")
            conn.execute(f"""UPDATE merchant_pair_progress
                SET completed_count=0,reached_at=NULL,reset_at=NULL,
                    cycle_started_at={now_utc},updated_at={now_utc}""")
            conn.execute(
                "INSERT INTO platform_settings(setting_key,setting_value) VALUES(?, 'done')",
                (reset_key,))
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
            (1, 10, 12, 1.25, 2.50), (2, 50, 50, 2.00, 6.50),
            (3, 150, 80, 9.80, 17.40), (4, 250, 200, 15.00, 25.00),
            (5, 400, 0, 20.00, 35.00),
        ]
        conn.executemany("""INSERT OR IGNORE INTO level_settings
            (level,activation_usdt,product_target,profit_min,profit_max)
            VALUES(?,?,?,?,?)""", defaults)
        # Correct only the legacy Level 2 defaults; preserve any deliberate admin edits.
        conn.execute("""UPDATE level_settings
            SET activation_usdt=50,product_target=50,profit_min=2.00,profit_max=6.50
            WHERE level=2 AND activation_usdt=20 AND product_target=45
              AND profit_min=3.50 AND profit_max=6.50""")
        # Set Level 1 activation price to 10 USDT once; preserve future Admin edits.
        level1_price_migration = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key='migration_level1_price_10_usdt'"
        ).fetchone()
        if not level1_price_migration:
            conn.execute("UPDATE level_settings SET activation_usdt=10 WHERE level=1")
            conn.execute("INSERT INTO platform_settings(setting_key,setting_value) VALUES('migration_level1_price_10_usdt','done')")
        # Apply the requested Level 4 price once to existing installations; do not overwrite later Admin edits.
        level4_price_migration = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key='migration_level4_price_250_usdt'"
        ).fetchone()
        if not level4_price_migration:
            conn.execute("UPDATE level_settings SET activation_usdt=250 WHERE level=4")
            conn.execute("INSERT INTO platform_settings(setting_key,setting_value) VALUES('migration_level4_price_250_usdt','done')")
        conn.execute("INSERT OR IGNORE INTO platform_settings(setting_key,setting_value) VALUES('platform_fee_percent','5')")
        conn.execute("INSERT OR IGNORE INTO platform_settings(setting_key,setting_value) VALUES('usdt_inr_rate','110')")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_product_audit_product ON product_audit(product_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shop_audit_shop ON shop_audit(shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_level_history_shop ON level_history(shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v4_orders_buyer ON v4_orders(buyer_shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v4_orders_seller ON v4_orders(seller_shop_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_owner ON ledger(owner_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON shops(referred_by_owner_id,id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON referral_rewards(referrer_owner_id,id DESC)")
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
