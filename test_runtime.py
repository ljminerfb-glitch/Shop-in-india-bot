"""Isolated runtime + mocked Telegram handler end-to-end tests."""
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
import bot_v5 as v5


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Message:
    def __init__(self, text=None, chat_id=0):
        self.text = text
        self.chat_id = chat_id
        self.message_id = 100
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, "kwargs": kwargs})
        return self


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, *args, **kwargs):
        self.sent.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(message_id=101, chat_id=args[0] if args else kwargs.get("chat_id", 0))


class FakeQuery:
    def __init__(self, user_id, data, message):
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.message = message
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append({"args": args, "kwargs": kwargs})


def context():
    return SimpleNamespace(user_data={}, bot=FakeBot(), args=[])


def callback_update(user_id, data):
    message = Message(chat_id=user_id)
    query = FakeQuery(user_id, data, message)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=user_id),
        message=message,
    )
    return update, message, query


def message_update(user_id, text):
    message = Message(text=text, chat_id=user_id)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=user_id),
        message=message,
    )
    return update, message


def callback_data(reply):
    markup = reply["kwargs"].get("reply_markup")
    if not markup:
        return []
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]


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
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(101,'Seller L1','approved','2026-01-01',1);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(101,'2026-01-01','shop_owner','approved');
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(202,'Buyer L1','approved','2026-01-01',1);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(202,'2026-01-01','shop_owner','approved');
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(303,'Buyer L2','approved','2026-01-01',2);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(303,'2026-01-01','shop_owner','approved');
    INSERT INTO level_settings VALUES(1,5,12,1.25,2.5);
    INSERT INTO level_settings VALUES(2,10,24,1.5,3.0);
    """)
    c.commit()
    c.close()
    v4.DB = DB_PATH
    v4.v3.DB = DB_PATH
    v5.v3.DB = DB_PATH
    v5.ADMIN_ID = 999999
    cc.setup()


def add_deposit(owner_id, shop_id, amount):
    c = v4.db()
    c.execute("""INSERT INTO ledger(shop_id,owner_id,entry_type,amount,currency,note,created_at)
                 VALUES(?,?,'deposit_credit',?,'USDT','isolated test deposit',?)""",
              (shop_id, owner_id, amount, v4.now()))
    c.commit()
    c.close()


def run_central_buy(owner, pid, qty):
    message = Message(chat_id=owner)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=owner),
        effective_message=message,
    )
    ctx = context()
    ctx.args = [str(pid), str(qty)]
    asyncio.run(cc.buy(update, ctx))
    return message.replies


def test_central_purchase_and_auto_listing():
    add_deposit(101, 1, 10)
    c = v4.db()
    pid = c.execute("SELECT id FROM central_catalog ORDER BY id LIMIT 1").fetchone()["id"]
    c.execute("UPDATE central_catalog SET price_usdt=2.5, stock=5 WHERE id=?", (pid,))
    c.execute("""INSERT INTO ledger(shop_id,owner_id,entry_type,amount,currency,note,created_at)
                 VALUES(1,101,'level_activation_wallet_credit',2.5,'USDT','test activation cashback',?)""",
              (v4.now(),))
    c.commit()
    require(cc.balance(c, 101) == 12.5, "USDT wallet balance calculation failed")
    c.close()

    replies = run_central_buy(101, pid, 2)
    require(any("Purchase successful" in x["text"] for x in replies), "Central purchase did not succeed")
    c = v4.db()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == 3,
            "Central shared stock was not reduced")
    order = c.execute("SELECT id FROM central_orders WHERE buyer_owner_id=101 AND quantity=2").fetchone()
    require(order is not None, "Central purchase order missing")
    inv = c.execute("SELECT * FROM central_inventory WHERE owner_id=101 AND remaining_quantity=2").fetchone()
    require(inv is not None, "Buyer inventory missing")
    listing = c.execute("SELECT * FROM resale_listings WHERE owner_id=101 AND status='active'").fetchone()
    require(listing is not None, "Level 1 purchase did not auto-create resale listing")
    require(abs(float(listing["ask_price_usdt"]) - 3.3) < 1e-8,
            "Resale asking price is not cost + 0.8 USDT")
    require(int(listing["remaining_quantity"]) == 2, "Listing quantity does not match purchased inventory")
    require(abs(cc.balance(c, 101) - 7.5) < 1e-8, "Central purchase wallet debit is wrong")
    require(c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity check failed")
    listing_id, inventory_id = int(listing["id"]), int(inv["id"])
    c.close()
    print("PASS: Central purchase, USDT debit, inventory and automatic +0.8 listing")
    return pid, listing_id, inventory_id


def test_list_stock_profit_screen():
    update, message, query = callback_update(101, "rs:listmenu")
    asyncio.run(cc.resale_callback(update, context()))
    require(len(message.replies) >= 1, "List My Stock screen did not render")
    screen = message.replies[0]["text"]
    require("Your cost: 2.5 USDT / item" in screen, "List My Stock does not show unit cost")
    require("Resale price: 3.3 USDT / item" in screen, "List My Stock does not show resale price")
    require("Expected profit: +0.8 USDT / sold item" in screen,
            "List My Stock does not show expected profit per item")
    require("Listed stock potential: <b>+1.6 USDT</b>" in screen,
            "List My Stock does not show total potential profit for active listings")
    require("Free stock potential: <b>+0 USDT</b>" in screen,
            "List My Stock does not show profit available from unlisted stock")
    require("Profit is credited after a sale is completed" in screen,
            "Expected profit is not clearly distinguished from realized profit")
    require(message.replies[0]["kwargs"].get("parse_mode") == "HTML",
            "List My Stock is not using Telegram HTML formatting")
    print("PASS: List My Stock shows cost, price, per-item and total expected profit")


def test_seller_market_screen(listing_id):
    update, message, query = callback_update(202, "m:sellermarket")
    asyncio.run(v5.menu_callback(update, context()))
    require(len(message.replies) >= 2, "Seller Marketplace did not render both resale and legacy screens")
    first = message.replies[0]
    require("OTHER LEVEL 1 MERCHANT LISTINGS" in first["text"], "Other Level 1 resale section missing")
    require("Price: <b>3.3 USDT / item</b>" in first["text"], "Seller Marketplace price is wrong")
    require(first["kwargs"].get("parse_mode") == "HTML", "Seller Marketplace is not using Telegram HTML")
    buttons = callback_data(first)
    require("rs:buy:%d:1" % listing_id in buttons, "Seller Marketplace Buy 1 button missing")
    require("m:dashboard" in buttons, "Seller Marketplace back-to-dashboard button missing")
    print("PASS: V5 Seller Marketplace displays other merchant listing, price, Buy and Back buttons")

    # Seller must also see their own listing in a separate management/info section.
    own_update, own_message, own_query = callback_update(101, "m:sellermarket")
    asyncio.run(v5.menu_callback(own_update, context()))
    own_screen = own_message.replies[0]["text"]
    require("MY ACTIVE USDT LISTINGS" in own_screen, "Seller's own USDT listing section missing")
    require("#%d Oxidised Jhumka Earrings" % listing_id in own_screen, "Seller cannot see their own listed product")
    require("Your available stock: <b>2</b>" in own_screen, "Seller's own remaining listing quantity missing")
    require("Asking price: <b>3.3 USDT / item</b>" in own_screen, "Seller's own asking price missing")
    legacy_screen = own_message.replies[1]["text"]
    require("Your USDT listings appear above" in legacy_screen,
            "Legacy INR section does not explain where USDT stock appears")
    print("PASS: Seller sees own active USDT listing, quantity and asking price")


def test_resale_buyer_guards_and_sale(listing_id, inventory_id):
    # Level 2 buyer is blocked server-side.
    add_deposit(303, 3, 20)
    before_l2 = cc.balance(v4.db(), 303)
    update, message, query = callback_update(303, "rs:buy:%d:1" % listing_id)
    asyncio.run(cc.resale_callback(update, context()))
    require(query.answers[-1]["kwargs"].get("show_alert") is True, "Level 2 buyer was not blocked")
    require("Approved Level 1" in query.answers[-1]["args"][0], "Level 2 rejection message unclear")

    # A Level 1 buyer completes the real callback handler.
    add_deposit(202, 2, 20)
    update, message, query = callback_update(202, "rs:buy:%d:1" % listing_id)
    asyncio.run(cc.resale_callback(update, context()))
    require(query.answers[-1]["args"][0] == "Purchased", "Level 1 resale purchase callback failed")
    require(any("PURCHASE CONFIRMED" in x["text"] and "Total paid: <b>3.3 USDT</b>" in x["text"] for x in message.replies),
            "Buyer success confirmation missing")
    require(message.replies[-1]["kwargs"].get("parse_mode") == "HTML",
            "Resale purchase confirmation is not using Telegram HTML")

    c = v4.db()
    listing = c.execute("SELECT * FROM resale_listings WHERE id=?", (listing_id,)).fetchone()
    seller_inv = c.execute("SELECT remaining_quantity FROM central_inventory WHERE id=?", (inventory_id,)).fetchone()[0]
    require(listing["status"] == "active" and listing["remaining_quantity"] == 1,
            "Partial sale did not leave one active listing item")
    require(seller_inv == 1, "Seller inventory quantity did not decrease")
    require(abs(cc.balance(c, 202) - 16.7) < 1e-8, "Buyer wallet debit is wrong")
    require(abs(cc.balance(c, 101) - 10.8) < 1e-8, "Seller sale proceeds were not credited")
    profit = c.execute("""SELECT amount FROM ledger WHERE owner_id=101 AND entry_type='resale_profit_credit'
                          ORDER BY id DESC LIMIT 1""").fetchone()
    require(profit is not None and abs(float(profit["amount"]) - 0.8) < 1e-8,
            "Seller profit ledger did not record 0.8 USDT for one item")
    require(c.execute("""SELECT COUNT(*) FROM central_orders
                         WHERE buyer_owner_id=202 AND quantity=1 AND total_usdt=3.3""").fetchone()[0] == 1,
            "Resale central order was not recorded")
    # Verify an unapproved/non-L1 seller cannot sell from an old active listing.
    c.execute("UPDATE shops SET level=2 WHERE id=1")
    c.commit()
    c.close()
    update, message, query = callback_update(202, "rs:buy:%d:1" % listing_id)
    asyncio.run(cc.resale_callback(update, context()))
    require(query.answers[-1]["kwargs"].get("show_alert") is True,
            "Non-Level 1 seller listing was not blocked")
    require("Only approved Level 1 seller" in query.answers[-1]["args"][0],
            "Non-Level 1 seller rejection message unclear")
    c = v4.db()
    require(c.execute("SELECT remaining_quantity FROM resale_listings WHERE id=?", (listing_id,)).fetchone()[0] == 1,
            "Listing changed after invalid seller rejection")
    c.execute("UPDATE shops SET level=1 WHERE id=1")
    c.commit()
    c.close()
    require(abs(cc.balance(v4.db(), 303) - before_l2) < 1e-8,
            "Level 2 buyer wallet changed after rejection")
    print("PASS: Level 2 buyer denied; Level 1 buyer purchase, seller proceeds and 0.8 profit ledger")
    print("PASS: Non-Level 1 seller listing blocked without stock change")


def test_admin_resale_flow(listing_id, inventory_id):
    admin_ctx = context()
    update, message, query = callback_update(999999, "adm:resale")
    asyncio.run(v5.admin_panel_callback(update, admin_ctx))
    review_buttons = [cb for r in message.replies for cb in callback_data(r)]
    require("rs:review:%d" % listing_id in review_buttons, "Admin Resale Market review button missing")

    update, review_message, review_query = callback_update(999999, "rs:review:%d" % listing_id)
    asyncio.run(cc.resale_callback(update, admin_ctx))
    require(admin_ctx.user_data.get("admin_resale_offer", {}).get("listing_id") == listing_id,
            "Admin review callback did not select listing")
    prompt = review_message.replies[-1]["text"]
    require("Buy all at asking price: <code>3.3 1</code>" in prompt,
            "Admin review prompt does not provide exact valid price and quantity")
    require("Maximum quantity: 1" in prompt,
            "Admin review prompt does not state current available quantity")
    require(review_message.replies[-1]["kwargs"].get("parse_mode") == "HTML",
            "Admin review prompt is not using Telegram HTML")

    # Verify common input mistakes get an actionable reply without changing stock.
    bad_update, bad_message = message_update(999999, "3.5")
    asyncio.run(v5.text_handler(bad_update, admin_ctx))
    require("Format: PRICE QUANTITY" in bad_message.replies[-1]["text"],
            "Single-number admin offer did not explain required format")
    too_many_update, too_many_message = message_update(999999, "3.5 10")
    asyncio.run(v5.text_handler(too_many_update, admin_ctx))
    require("Listing no longer available in that quantity" in too_many_message.replies[-1]["text"],
            "Admin offer above remaining stock was not rejected clearly")

    offer_update, offer_message = message_update(999999, "3.5 1")
    asyncio.run(v5.text_handler(offer_update, admin_ctx))
    require(any("ADMIN PURCHASE COMPLETE" in x["text"] for x in offer_message.replies),
            "Admin offer text handler did not complete")
    seller_notifications = [x for x in admin_ctx.bot.sent
                            if x["args"] and x["args"][0] == 101]
    notification_text = seller_notifications[-1]["args"][1] if seller_notifications and len(seller_notifications[-1]["args"]) > 1 else ""
    require("Customer purchased your listing" in notification_text,
            "Seller notification did not use Customer wording")
    require("Admin purchased your listing" not in notification_text,
            "Seller notification still exposes Admin wording")
    c = v4.db()
    listing = c.execute("SELECT status,remaining_quantity FROM resale_listings WHERE id=?", (listing_id,)).fetchone()
    require(listing["status"] == "sold" and listing["remaining_quantity"] == 0,
            "Admin purchase did not close fully sold listing")
    order = c.execute("SELECT * FROM admin_resale_orders WHERE listing_id=?", (listing_id,)).fetchone()
    require(order is not None and order["quantity"] == 1 and abs(order["total_usdt"] - 3.5) < 1e-8,
            "Admin resale order was not recorded")
    profit = c.execute("""SELECT amount FROM ledger WHERE owner_id=101 AND entry_type='resale_profit_credit'
                          ORDER BY id DESC LIMIT 1""").fetchone()
    require(profit is not None and abs(float(profit["amount"]) - 1.0) < 1e-8,
            "Admin resale seller profit calculation was wrong")
    require(c.execute("SELECT remaining_quantity FROM central_inventory WHERE id=?", (inventory_id,)).fetchone()[0] == 0,
            "Seller inventory was not reconciled after admin purchase")
    require(c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity check failed after resale flow")
    c.close()
    require("admin_resale_offer" not in admin_ctx.user_data, "Admin offer state was not cleared")
    print("PASS: Admin Resale Market, Review callback, offer message, seller credit and inventory reconciliation")


def test_failed_central_purchases(pid):
    c = v4.db()
    c.execute("""INSERT INTO ledger(shop_id,owner_id,entry_type,amount,currency,note,created_at)
                 VALUES(1,101,'resale_purchase_debit',20,'USDT','isolated test spend',?)""", (v4.now(),))
    c.commit()
    c.close()
    replies = run_central_buy(101, pid, 1)
    require(any("Insufficient USDT wallet balance" in x["text"] for x in replies),
            "Insufficient wallet purchase was not rejected")
    c = v4.db()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == 3,
            "Shared stock changed after rejected wallet purchase")
    c.close()
    replies = run_central_buy(101, pid, 4)
    require(any("Insufficient central stock" in x["text"] for x in replies),
            "Insufficient central stock purchase was not rejected")
    c = v4.db()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == 3,
            "Shared stock changed after rejected stock purchase")
    require(c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity check failed")
    c.close()
    print("PASS: Insufficient wallet and stock rejected without mutations")


def main():
    fixture()
    pid, listing_id, inventory_id = test_central_purchase_and_auto_listing()
    test_list_stock_profit_screen()
    test_seller_market_screen(listing_id)
    test_resale_buyer_guards_and_sale(listing_id, inventory_id)
    test_admin_resale_flow(listing_id, inventory_id)
    test_failed_central_purchases(pid)
    print("PASS: All isolated Telegram-handler end-to-end tests completed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        TMP.cleanup()
