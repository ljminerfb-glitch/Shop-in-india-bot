"""Safe V4 migration smoke test. Never edits the user's live database.

Run: python test_v4.py
Optional: python test_v4.py --db shop_in_india.db
"""
import argparse, os, shutil, sqlite3, subprocess, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def make_fixture(path):
    c=sqlite3.connect(path)
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
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(101,'Fixture Shop','approved','2026-01-01',1);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(101,'2026-01-01','shop_owner','approved');
    INSERT INTO products(shop_id,title,description,price,currency,active,stock) VALUES(1,'Fixture Product','Test',100,'INR',1,4);
    INSERT INTO level_settings VALUES(1,5,12,1.25,2.5);
    """)
    c.commit(); c.close()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default=None,help="Existing database to copy for test; original is never modified")
    args=p.parse_args()
    with tempfile.TemporaryDirectory(prefix="shopindia_v4_test_") as td:
        temp=Path(td)/"test.db"
        if args.db:
            shutil.copy2(args.db,temp)
        else:
            make_fixture(temp)
        before=sqlite3.connect(temp)
        before_counts={t:before.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                       for t in ("shops","users","products") if before.execute(
                           "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone()}
        before.close()
        env=os.environ.copy(); env["SHOP_DB"]=str(temp)
        subprocess.run([sys.executable,str(ROOT/"migrate_v4.py")],cwd=ROOT,env=env,check=True)
        c=sqlite3.connect(temp)
        integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
        required=("product_audit","shop_audit","level_history","payment_history","platform_settings","ledger","v4_orders")
        missing=[t for t in required if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone()]
        after_counts={t:c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before_counts}
        levels={r[0]:r[1:3] for r in c.execute("SELECT level,activation_usdt,product_target FROM level_settings")}
        c.close()
        assert integrity=="ok",integrity
        assert not missing,missing
        assert before_counts==after_counts,(before_counts,after_counts)
        assert levels.get(3)==(150.0,80),levels
        backups=list(Path(td).glob("test.db.pre_v4_*.bak"))
        assert backups,"Timestamped backup was not created"
        print("PASS: integrity_check=ok")
        print("PASS: required V4 tables exist")
        print("PASS: existing shop/user/product row counts unchanged")
        print("PASS: L3 target is 80")
        print("PASS: timestamped backup exists")
        print("NOTE: live Telegram/API flows still require manual Telegram test.")
if __name__=="__main__": main()
