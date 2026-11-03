import sqlite3

DB = "shop_in_india.db"
con = sqlite3.connect(DB)
cur = con.cursor()

def add_column(table, column, definition):
    columns = [r[1] for r in cur.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()]
    if column not in columns:
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )

# Existing tables: safe additions
add_column("shops", "password_hash", "TEXT")
add_column("shops", "level", "INTEGER NOT NULL DEFAULT 0")

# Owner withdrawal wallet profile
cur.execute("""
CREATE TABLE IF NOT EXISTS owner_wallets (
    owner_id INTEGER PRIMARY KEY,
    network TEXT NOT NULL,
    address TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")

# Payment requests: level activation / deposits / booster
cur.execute("""
CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    shop_id INTEGER NOT NULL,
    payment_type TEXT NOT NULL,
    level INTEGER,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USDT',
    network TEXT NOT NULL,
    screenshot_file_id TEXT,
    tx_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
)
""")

# Withdrawal requests
cur.execute("""
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    shop_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USDT',
    network TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
)
""")

# Verified marketplace activity
cur.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_shop_id INTEGER NOT NULL,
    seller_shop_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    total_amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USDT',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT
)
""")

# Admin audit trail
cur.execute("""
CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    owner_id INTEGER,
    action TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
)
""")

# Booster requests
cur.execute("""
CREATE TABLE IF NOT EXISTS booster_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    amount REAL NOT NULL DEFAULT 10,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
)
""")

# Level settings (Admin can configure later)
cur.execute("""
CREATE TABLE IF NOT EXISTS level_settings (
    level INTEGER PRIMARY KEY,
    activation_usdt REAL NOT NULL,
    product_target INTEGER NOT NULL,
    profit_min REAL NOT NULL,
    profit_max REAL NOT NULL
)
""")

levels = [
    (1, 5, 12, 1.25, 2.50),
    (2, 20, 45, 3.50, 6.50),
    (3, 150, 80, 9.80, 17.40),
    (4, 150, 200, 15.00, 25.00),
    (5, 400, 0, 20.00, 35.00)
]

cur.executemany("""
INSERT OR IGNORE INTO level_settings
(level, activation_usdt, product_target, profit_min, profit_max)
VALUES (?, ?, ?, ?, ?)
""", levels)

con.commit()

# Check database integrity
check = cur.execute("PRAGMA integrity_check").fetchone()[0]
print("Database upgrade:", check)
print("New tables and columns are ready.")

con.close()
