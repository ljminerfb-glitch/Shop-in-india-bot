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
import runtime_schema
import bot_v5 as v5


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Message:
    def __init__(self, text=None, chat_id=0):
        self.text = text
        self.caption = None
        self.photo = None
        self.chat_id = chat_id
        self.message_id = 100
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, "kwargs": kwargs})
        return self

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.edits.append({"text": text, "kwargs": kwargs})
        return self

    async def edit_caption(self, caption, **kwargs):
        self.caption = caption
        self.edits.append({"caption": caption, "kwargs": kwargs})
        return self


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, *args, **kwargs):
        self.sent.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(message_id=101, chat_id=args[0] if args else kwargs.get("chat_id", 0))

    async def send_photo(self, *args, **kwargs):
        self.sent.append({"args": args, "kwargs": kwargs, "photo": True})
        return SimpleNamespace(message_id=102, chat_id=args[0] if args else kwargs.get("chat_id", 0))

    async def get_me(self):
        return SimpleNamespace(username="test_shop_bot")


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


def product_card_ids(sent):
    ids=[]
    for item in sent:
        raw=item["kwargs"].get("caption")
        if raw is None and len(item.get("args",()))>1 and isinstance(item["args"][1],str):
            raw=item["args"][1]
        if isinstance(raw,str) and raw.startswith("<b>#"):
            try:
                ids.append(int(raw.split("#",1)[1].split(None,1)[0]))
            except (ValueError,IndexError):
                pass
    return ids


def sent_text(item):
    if "text" in item["kwargs"]:
        return item["kwargs"]["text"]
    args=item.get("args",())
    return args[1] if len(args)>1 and isinstance(args[1],str) else ""


def sent_callback_data(item):
    markup=item["kwargs"].get("reply_markup")
    if not markup:
        return []
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button,"callback_data",None)
    ]


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
    CREATE TABLE payment_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,shop_id INTEGER,payment_type TEXT,level INTEGER,amount REAL,currency TEXT,network TEXT,screenshot_file_id TEXT,tx_hash TEXT,transaction_hash TEXT,status TEXT,admin_note TEXT,created_at TEXT,reviewed_at TEXT);
    CREATE TABLE payment_history(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,shop_id INTEGER,payment_type TEXT,amount REAL,currency TEXT,status TEXT,reference TEXT,admin_note TEXT,actor_id INTEGER,created_at TEXT,reviewed_at TEXT);
    CREATE TABLE withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,shop_id INTEGER,amount REAL,currency TEXT,network TEXT,wallet_address TEXT,status TEXT,admin_note TEXT,created_at TEXT,reviewed_at TEXT);
    CREATE TABLE owner_wallets(owner_id INTEGER PRIMARY KEY,network TEXT NOT NULL,address TEXT NOT NULL,updated_at TEXT NOT NULL,verified INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE admin_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER,owner_id INTEGER,action TEXT,details TEXT,created_at TEXT);
    CREATE TABLE level_settings(level INTEGER PRIMARY KEY,activation_usdt REAL,product_target INTEGER,profit_min REAL,profit_max REAL);
    CREATE TABLE platform_settings(setting_key TEXT PRIMARY KEY,setting_value TEXT NOT NULL,updated_by INTEGER,updated_at TEXT);
    INSERT INTO platform_settings(setting_key,setting_value) VALUES('usdt_inr_rate','110');
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(101,'Seller L1','approved','2026-01-01',1);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(101,'2026-01-01','shop_owner','approved');
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(202,'Buyer L1','approved','2026-01-01',1);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(202,'2026-01-01','shop_owner','approved');
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(303,'Seller L2','approved','2026-01-01',2);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(303,'2026-01-01','shop_owner','approved');
    INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(404,'Buyer L2','approved','2026-01-01',2);
    INSERT INTO users(tg_id,created_at,role,status) VALUES(404,'2026-01-01','shop_owner','approved');
    INSERT INTO level_settings VALUES(1,5,12,1.25,2.5);
    INSERT INTO level_settings VALUES(2,50,50,2.0,6.5);
    """)
    c.commit()
    c.close()
    v4.DB = DB_PATH
    v4.v3.DB = DB_PATH
    v5.v3.DB = DB_PATH
    v5.ADMIN_ID = 999999
    runtime_schema.ensure_schema(DB_PATH)
    v5.ensure_all_referral_codes()
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


def test_level1_catalogue_quantity_buttons():
    c=v4.db()
    row=c.execute("SELECT id FROM central_catalog ORDER BY id LIMIT 1").fetchone()
    pid=int(row["id"])
    c.execute("UPDATE central_catalog SET price_usdt=2.5,stock=12,active=1,level_required=1 WHERE id=?",(pid,))
    c.commit()
    c.close()
    shop=v4.shop_for(101)
    update,_,_=callback_update(101,"m:catalogue")
    ctx=context()
    asyncio.run(cc.show_owner_level_products(update,ctx,shop))
    expected_ids=[int(r[0]) for r in v4.db().execute(
        "SELECT id FROM central_catalog WHERE active=1 AND level_required=1 ORDER BY id").fetchall()]
    require(product_card_ids(ctx.bot.sent)==expected_ids[:5],
            f"First Level 1 page should show exactly the first five products: {product_card_ids(ctx.bot.sent)!r}")
    nav=next(x for x in ctx.bot.sent if "ccpage:1:1" in sent_callback_data(x))
    require("PAGE 1/3" in sent_text(nav),
            "First catalogue page indicator or Next 5 button is missing")
    before=len(ctx.bot.sent)
    next_update,_,_=callback_update(101,"ccpage:1:1")
    asyncio.run(cc.owner_catalog_page_callback(next_update,ctx))
    next_page_sent=ctx.bot.sent[before:]
    require(product_card_ids(next_page_sent)==expected_ids[5:10],
            "Next 5 did not show products 6 through 10")
    next_nav=next(x for x in next_page_sent if any(cb.startswith("ccpage:") for cb in sent_callback_data(x)))
    next_nav_buttons=sent_callback_data(next_nav)
    require("ccpage:1:0" in next_nav_buttons and "ccpage:1:2" in next_nav_buttons,
            "Middle page must provide Previous 5 and Next 5 buttons")
    before=len(ctx.bot.sent)
    last_update,_,_=callback_update(101,"ccpage:1:2")
    asyncio.run(cc.owner_catalog_page_callback(last_update,ctx))
    last_page_sent=ctx.bot.sent[before:]
    require(product_card_ids(last_page_sent)==expected_ids[10:],
            "Final catalogue page should show only the remaining products")
    last_nav=next(x for x in last_page_sent if any(cb.startswith("ccpage:") for cb in sent_callback_data(x)))
    require("ccpage:1:3" not in sent_callback_data(last_nav) and
            "ccpage:1:1" in sent_callback_data(last_nav),
            "Final page must hide Next 5 and keep Previous 5")
    # Re-open page one for the per-product quantity-button assertions below.
    ctx=context()
    asyncio.run(cc.show_owner_level_products(update,ctx,shop))
    item=next(x for x in ctx.bot.sent if x["kwargs"].get("reply_markup") and
              any(getattr(b,"callback_data",None)==f"ccbuy:{pid}:1"
                  for row in x["kwargs"]["reply_markup"].inline_keyboard for b in row))
    markup=item["kwargs"]["reply_markup"]
    callbacks=[b.callback_data for row in markup.inline_keyboard
               for b in row if getattr(b,"callback_data",None)]
    visible_labels=[b.text for row in markup.inline_keyboard
                    for b in row if getattr(b,"callback_data",None)]
    expected=[f"ccbuy:{pid}:1"]
    require(callbacks[:1]==expected,"Level 1 catalogue must show only the quantity-1 purchase option")
    require(visible_labels[:1]==["1"],
            f"Level 1 quantity button label is not 1: {visible_labels[:1]!r}")
    require(f"ccbuy:{pid}:2" not in callbacks,
            "Level 1 catalogue must not offer purchase quantities above one")
    require("m:products" in callbacks and "m:dashboard" in callbacks,
            "Catalogue quantity buttons lost their Back/Dashboard navigation")
    c=v4.db()
    c.execute("UPDATE central_catalog SET stock=3 WHERE id=?",(pid,))
    c.commit()
    c.close()
    ctx=context()
    asyncio.run(cc.show_owner_level_products(update,ctx,shop))
    item=next(x for x in ctx.bot.sent if x["kwargs"].get("reply_markup") and
              any(getattr(b,"callback_data",None)==f"ccbuy:{pid}:1"
                  for row in x["kwargs"]["reply_markup"].inline_keyboard for b in row))
    callbacks=[b.callback_data for row in item["kwargs"]["reply_markup"].inline_keyboard
               for b in row if getattr(b,"callback_data",None)]
    require(callbacks[:1]==[f"ccbuy:{pid}:1"] and f"ccbuy:{pid}:2" not in callbacks,
            "Level 1 catalogue did not enforce the single-item purchase option")
    print("PASS: Level 1 catalogue shows only Buy ×1")


def test_admin_catalogue_pagination():
    update,_,_=callback_update(999999,"ccadminlevel:1")
    ctx=context()
    asyncio.run(cc.admin_level_callback(update,ctx))
    ids=[int(r[0]) for r in v4.db().execute(
        "SELECT id FROM central_catalog WHERE level_required=1 ORDER BY id").fetchall()]
    require(product_card_ids(ctx.bot.sent)==ids[:5],
            "Admin Level 1 page must show only the first five products")
    nav=next(x for x in ctx.bot.sent if "ccadminpage:1" in sent_callback_data(x))
    require("PAGE 1/3" in sent_text(nav),
            "Admin catalogue page indicator or Next 5 button is missing")
    before=len(ctx.bot.sent)
    next_update,_,_=callback_update(999999,"ccadminpage:1")
    asyncio.run(cc.admin_catalog_page_callback(next_update,ctx))
    page_two=ctx.bot.sent[before:]
    require(product_card_ids(page_two)==ids[5:10],
            "Admin Next 5 did not show products 6 through 10")
    nav_two=next(x for x in page_two if any(cb.startswith("ccadminpage:") for cb in sent_callback_data(x)))
    buttons=sent_callback_data(nav_two)
    require("ccadminpage:0" in buttons and "ccadminpage:2" in buttons,
            "Admin middle page must provide Previous 5 and Next 5")
    print("PASS: Admin Level 1 catalogue shows five products per page with Previous/Next")


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

    blocked = run_central_buy(101, pid, 2)
    require(any("only 1 item per order" in x["text"] for x in blocked),
            "Server did not block a Level 1 purchase quantity above one")
    c = v4.db()
    c.execute("UPDATE shops SET level=2 WHERE owner_id=101")
    c.commit(); c.close()
    replies = run_central_buy(101, pid, 2)
    require(any("Purchase successful" in x["text"] for x in replies), "Level 2 central purchase did not succeed")
    c = v4.db()
    c.execute("UPDATE shops SET level=1 WHERE owner_id=101")
    c.commit()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == 3,
            "Central shared stock was not reduced")
    order = c.execute("SELECT id FROM central_orders WHERE buyer_owner_id=101 AND quantity=2").fetchone()
    require(order is not None, "Level 2 purchase order missing")
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


def test_owner_wallet_and_inventory_design():
    # Wallet screen: key totals and realised profit are clearly separated.
    update, message, query = callback_update(101, "m:wallet")
    asyncio.run(v5.menu_callback(update, context()))
    wallet = message.replies[0]
    require("<b>👛 USDT WALLET</b>" in wallet["text"], "Wallet heading is missing")
    require("Available balance: <b>7.5 USDT</b>" in wallet["text"], "Wallet balance is missing or has unnecessary trailing zeros")
    require("7.500000 USDT" not in wallet["text"], "Wallet display still shows unnecessary trailing zeros")
    require(wallet["kwargs"].get("parse_mode") == "HTML", "Wallet is not using Telegram HTML")
    require("💚 <b>Recorded profit:" in wallet["text"], "Green recorded profit label is missing")

    # Inventory screen: sold/available stock and margin stay easy to scan.
    update, message, query = callback_update(101, "m:products")
    asyncio.run(v5.menu_callback(update, context()))
    inventory = message.replies[0]
    require("<b>📦 MY PRODUCTS & INVENTORY</b>" in inventory["text"],
            "My Products heading is missing")
    require("Available quantity: <b>2</b>" in inventory["text"],
            "Available inventory quantity is missing")
    require("💚 Expected margin: <b>+0.8 USDT / item</b>" in inventory["text"],
            "Expected profit margin is missing from inventory")
    require(inventory["kwargs"].get("parse_mode") == "HTML",
            "My Products is not using Telegram HTML")
    print("PASS: Owner wallet and My Products screens use readable Telegram HTML with profit status")


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
    require("OTHER ELIGIBLE MERCHANT LISTINGS (LEVEL 1–1)" in first["text"], "Other Level 1 resale section missing")
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
    # An upgraded Level 2 shop can see eligible Level 1 resale inventory.
    add_deposit(303, 3, 20)
    before_l2 = cc.balance(v4.db(), 303)
    update, message, query = callback_update(303, "rs:market")
    asyncio.run(cc.resale_callback(update, context()))
    visible_buttons=[cb for reply in message.replies for cb in callback_data(reply)]
    require("rs:buy:%d:1" % listing_id in visible_buttons,
            "Level 2 resale market did not include eligible Level 1 listings")
    require(abs(cc.balance(v4.db(),303)-before_l2)<1e-8,
            "Opening the resale market unexpectedly changed the Level 2 wallet")

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
    # A suspended seller cannot sell even when the product level is otherwise eligible.
    c.execute("UPDATE shops SET status='suspended' WHERE id=1")
    c.commit()
    c.close()
    update, message, query = callback_update(202, "rs:buy:%d:1" % listing_id)
    asyncio.run(cc.resale_callback(update, context()))
    require(query.answers[-1]["kwargs"].get("show_alert") is True,
            "Suspended seller listing was not blocked")
    require("approved merchants" in query.answers[-1]["args"][0],
            "Suspended seller rejection message is unclear")
    c = v4.db()
    require(c.execute("SELECT remaining_quantity FROM resale_listings WHERE id=?", (listing_id,)).fetchone()[0] == 1,
            "Listing changed after suspended seller rejection")
    c.execute("UPDATE shops SET status='approved',level=1 WHERE id=1")
    c.commit()
    c.close()
    require(abs(cc.balance(v4.db(), 303) - before_l2) < 1e-8,
            "Level 2 buyer wallet changed after rejection")
    print("PASS: Level 2 buyer denied; Level 1 buyer purchase, seller proceeds and 0.8 profit ledger")
    print("PASS: Non-Level 1 seller listing blocked without stock change")


def test_merchant_level_upgrade_option():
    # Dashboard must expose a dedicated upgrade button for approved active shops.
    dashboard_buttons=[
        button.callback_data
        for row in v5.owner_kb().inline_keyboard
        for button in row
        if getattr(button,"callback_data",None)
    ]
    require("m:upgrade" in dashboard_buttons,
            "Merchant dashboard is missing the Upgrade Shop Level button")

    update,message,query=callback_update(101,"m:upgrade")
    ctx=context()
    asyncio.run(v5.menu_callback(update,ctx))
    require(len(message.replies)==1, "Upgrade menu did not open")
    require("Current active level: <b>LEVEL 1</b>" in message.replies[0]["text"],
            "Upgrade menu does not show the merchant's active Level 1")
    upgrade_buttons=callback_data(message.replies[0])
    require("lv:2" in upgrade_buttons,
            "Level 1 merchant cannot choose Level 2 from upgrade menu")
    require("lv:1" not in upgrade_buttons,
            "Upgrade menu must not offer the already-active Level 1")

    # Choosing Level 2 displays the correct activation amount and payment action.
    detail_update,detail_message,_=callback_update(101,"lv:2")
    asyncio.run(v5.level_callback(detail_update,ctx))
    require("Activation price: <b>50 USDT</b>" in detail_message.replies[0]["text"],
            "Level 2 upgrade screen does not show the 50 USDT fee")
    require("lvpay:2" in callback_data(detail_message.replies[0]),
            "Level 2 upgrade screen is missing Continue to Payment")

    # Continue to payment creates one pending activation request, without duplicates.
    pay_update,pay_message,_=callback_update(101,"lvpay:2")
    asyncio.run(v5.level_pay_callback(pay_update,ctx))
    require("Amount: <b>50 USDT</b>" in pay_message.replies[0]["text"],
            "Level 2 payment screen does not request 50 USDT")
    require("net:" in " ".join(callback_data(pay_message.replies[0])),
            "Level 2 payment screen is missing TRC20/BEP20 choices")
    c=v4.db()
    requests=c.execute(
        "SELECT id,amount,status FROM payment_requests WHERE owner_id=101 AND payment_type='level_activation' AND level=2"
    ).fetchall()
    require(len(requests)==1 and float(requests[0]["amount"])==50.0 and requests[0]["status"]=="pending",
            "Level 2 upgrade payment request was not saved correctly")
    request_id=int(requests[0]["id"])
    c.close()

    duplicate_update,_,_=callback_update(101,"lvpay:2")
    asyncio.run(v5.level_pay_callback(duplicate_update,ctx))
    c=v4.db()
    count=c.execute(
        "SELECT COUNT(*) FROM payment_requests WHERE owner_id=101 AND payment_type='level_activation' AND level=2 AND status='pending'"
    ).fetchone()[0]
    c.close()
    require(count==1,"Repeated Level 2 payment tap created duplicate pending requests")

    # Back from the Level 2 detail must return to upgrade choices, not the all-level onboarding menu.
    back_update,back_message,_=callback_update(101,"lvback")
    asyncio.run(v5.level_back_callback(back_update,ctx))
    back_buttons=callback_data(back_message.replies[0])
    require("lv:2" in back_buttons and "lv:1" not in back_buttons,
            "Upgrade Back button did not return to higher levels only")
    print("PASS: Merchant dashboard Level 2 upgrade option, 50 USDT payment request and Back navigation")


def test_level2_catalogue_and_purchase_flow():
    settings_update,settings_message=message_update(999999,"/setlevel")
    settings_ctx=context()
    settings_ctx.args=["2","50","50","2.0","6.5"]
    asyncio.run(v4.setlevel(settings_update,settings_ctx))
    require(any("Saved L2 settings" in x["text"] for x in settings_message.replies),
            "Admin /setlevel did not save Level 2 activation, target and margin settings")
    c=v4.db()
    configured=c.execute(
        "SELECT activation_usdt,product_target,profit_min,profit_max FROM level_settings WHERE level=2"
    ).fetchone()
    require(tuple(configured)==(50.0,50,2.0,6.5),
            f"/setlevel did not persist requested Level 2 settings: {tuple(configured)!r}")
    rows=c.execute(
        "SELECT id,title,price_usdt,stock,image_path FROM central_catalog WHERE level_required=2 ORDER BY id"
    ).fetchall()
    require(len(rows)==50, f"Level 2 catalogue should contain 50 products, found {len(rows)}")
    require(all(int(r["stock"])==50 for r in rows), "Every new Level 2 product must begin with 50 stock")
    root=Path(__file__).resolve().parent
    require(all((root/str(r["image_path"])).is_file() for r in rows),
            "A Level 2 product image path is missing on disk")
    ids=[int(r["id"]) for r in rows]
    first_pid=ids[0]
    c.close()

    admin_update,_,_=callback_update(999999,"ccadminlevel:2")
    admin_ctx=context()
    asyncio.run(cc.admin_level_callback(admin_update,admin_ctx))
    require(product_card_ids(admin_ctx.bot.sent)==ids[:5],
            "Admin Level 2 first page did not show exactly products 1–5")
    admin_nav=next(x for x in admin_ctx.bot.sent if "ccadminpage:2:1" in sent_callback_data(x))
    require("PAGE 1/10" in sent_text(admin_nav),
            "Admin Level 2 page count or Next 5 navigation is wrong")
    before=len(admin_ctx.bot.sent)
    admin_next,_,_=callback_update(999999,"ccadminpage:2:1")
    asyncio.run(cc.admin_catalog_page_callback(admin_next,admin_ctx))
    require(product_card_ids(admin_ctx.bot.sent[before:])==ids[5:10],
            "Admin Level 2 Next 5 did not show products 6–10")

    # A Level 2 dashboard offers both catalogues and keeps Level 1 pagination available.
    menu_update,menu_message,_=callback_update(303,"m:market")
    menu_ctx=context()
    asyncio.run(v5.menu_callback(menu_update,menu_ctx))
    menu_callbacks=callback_data(menu_message.replies[0])
    require("cclevel:1" in menu_callbacks and "cclevel:2" in menu_callbacks,
            "Level 2 merchant catalogue menu does not show both Level 1 and Level 2")

    l1_rows=v4.db().execute(
        "SELECT id,title,price_usdt,stock FROM central_catalog WHERE active=1 AND level_required=1 ORDER BY id"
    ).fetchall()
    require(len(l1_rows)>=10,"Level 1 catalogue fixture should contain multiple pages")
    l1_ids=[int(row["id"]) for row in l1_rows]
    l1_pid=l1_ids[0]
    l1_title=str(l1_rows[0]["title"])
    lower_ctx=context()
    lower_update,_,_=callback_update(303,"cclevel:1")
    asyncio.run(cc.owner_level_callback(lower_update,lower_ctx))
    require(product_card_ids(lower_ctx.bot.sent)==l1_ids[:5],
            "Level 2 merchant could not open the first five Level 1 products")
    lower_nav=next(x for x in lower_ctx.bot.sent if "ccpage:1:1" in sent_callback_data(x))
    require("PAGE 1/3" in sent_text(lower_nav),
            "Level 2 merchant Level 1 catalogue pagination is missing")
    before_lower_page=len(lower_ctx.bot.sent)
    lower_next,_,_=callback_update(303,"ccpage:1:1")
    asyncio.run(cc.owner_catalog_page_callback(lower_next,lower_ctx))
    require(product_card_ids(lower_ctx.bot.sent[before_lower_page:])==l1_ids[5:10],
            "Level 2 merchant could not paginate the Level 1 catalogue")

    owner_update,_,_=callback_update(303,"cclevel:2")
    owner_ctx=context()
    asyncio.run(cc.owner_level_callback(owner_update,owner_ctx))
    require(product_card_ids(owner_ctx.bot.sent)==ids[:5],
            "Level 2 merchant catalogue did not show the first five Level 2 products")
    first_card=next(x for x in owner_ctx.bot.sent if product_card_ids([x]))
    require(any(cb.startswith(f"ccbuy:{first_pid}:") for cb in sent_callback_data(first_card)),
            "Level 2 product quantity purchase buttons are missing")

    add_deposit(303,3,300)
    replies=run_central_buy(303,first_pid,2)
    require(any("Purchase successful" in x["text"] for x in replies),
            f"Level 2 central purchase failed: {replies!r}")
    c=v4.db()
    stock=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(first_pid,)).fetchone()[0])
    require(stock==48, f"Level 2 central stock should fall from 50 to 48, got {stock}")
    inv=c.execute(
        "SELECT * FROM central_inventory WHERE owner_id=303 AND catalog_id=? AND remaining_quantity=2",
        (first_pid,)
    ).fetchone()
    require(inv is not None, "Level 2 buyer inventory was not created")
    listing=c.execute(
        "SELECT * FROM resale_listings WHERE owner_id=303 AND inventory_id=? AND status='active'",
        (inv["id"],)
    ).fetchone()
    require(listing is not None, "Level 2 purchase did not create an automatic resale listing")
    margin_pct=(float(listing["ask_price_usdt"])/float(listing["unit_cost_usdt"])-1.0)*100.0
    require(2.0 <= margin_pct <= 6.5,
            f"Level 2 resale margin is outside 2.0–6.5%: {margin_pct:.4f}%")
    listing_id=int(listing["id"])
    c.close()

    # Level 2 merchants can also buy Level 1 inventory, retaining its original +0.8 USDT margin.
    c=v4.db()
    l1_before=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(l1_pid,)).fetchone()[0])
    c.close()
    l1_replies=run_central_buy(303,l1_pid,1)
    require(any("Purchase successful" in x["text"] for x in l1_replies),
            f"Level 2 merchant could not buy a Level 1 catalogue product: {l1_replies!r}")
    c=v4.db()
    l1_after=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(l1_pid,)).fetchone()[0])
    require(l1_after==l1_before-1,"Level 1 stock did not decrease after Level 2 merchant purchase")
    l1_inventory=c.execute(
        "SELECT i.*,p.level_required FROM central_inventory i JOIN central_catalog p ON p.id=i.catalog_id "
        "WHERE i.owner_id=303 AND i.catalog_id=? AND i.remaining_quantity=1 ORDER BY i.id DESC LIMIT 1",
        (l1_pid,)
    ).fetchone()
    require(l1_inventory is not None and int(l1_inventory["level_required"])==1,
            "Level 2 merchant's Level 1 purchase was not saved as Level 1 inventory")
    l1_listing=c.execute(
        "SELECT * FROM resale_listings WHERE inventory_id=? AND status='active'",(l1_inventory["id"],)
    ).fetchone()
    require(l1_listing is not None and abs(float(l1_listing["ask_price_usdt"])-3.3)<1e-8,
            "Level 2 merchant's Level 1 purchase did not retain Level 1 +0.8 resale margin")
    l1_listing_id=int(l1_listing["id"])
    c.close()
    stock_screen_ctx=context()
    stock_screen_update,stock_screen_message,_=callback_update(303,"rs:listmenu")
    asyncio.run(cc.resale_callback(stock_screen_update,stock_screen_ctx))
    require(any(l1_title in x["text"] for x in stock_screen_message.replies),
            "Level 2 merchant's Level 1 inventory was hidden from List My Stock")

    # My Products displays the actual random resale price and percentage for Level 2 stock.
    products_update,products_message,_=callback_update(303,"m:products")
    asyncio.run(v5.menu_callback(products_update,context()))
    require("Resale price:" in products_message.replies[0]["text"]
            and "Margin:" in products_message.replies[0]["text"],
            "Level 2 My Products screen does not show its actual resale price and margin")

    add_deposit(404,4,300)
    market_update,market_message,_=callback_update(404,"rs:market")
    asyncio.run(cc.resale_callback(market_update,context()))
    market_buttons=[cb for reply in market_message.replies for cb in callback_data(reply)]
    require(f"rs:buy:{listing_id}:1" in market_buttons,
            "Level 2 resale market did not show the Level 2 seller listing")
    require(f"rs:buy:{l1_listing_id}:1" in market_buttons,
            "Level 2 resale market did not show the eligible Level 1 listing")
    buy_update,buy_message,buy_query=callback_update(404,f"rs:buy:{listing_id}:1")
    asyncio.run(cc.resale_callback(buy_update,context()))
    require(buy_query.answers[-1]["args"][0]=="Purchased",
            "Level 2 merchant-to-merchant resale purchase did not complete")
    require(any("Level: <b>2</b>" in x["text"] for x in buy_message.replies),
            "Level 2 resale success confirmation did not identify the level")

    before_l1=cc.balance(v4.db(),202)
    cross_update,_,cross_query=callback_update(202,f"rs:buy:{listing_id}:1")
    asyncio.run(cc.resale_callback(cross_update,context()))
    require(cross_query.answers[-1]["kwargs"].get("show_alert") is True,
            "Level 1 shop was allowed to buy a Level 2 listing")
    require("higher shop level" in cross_query.answers[-1]["args"][0],
            "Level 1 rejection did not explain the higher shop-level requirement")
    require(abs(cc.balance(v4.db(),202)-before_l1)<1e-8,
            "Cross-level rejected purchase changed the Level 1 wallet")

    c=v4.db()
    remaining=int(c.execute("SELECT remaining_quantity FROM resale_listings WHERE id=?",(listing_id,)).fetchone()[0])
    require(remaining==1, f"Level 2 resale listing should have one unit remaining, got {remaining}")
    seller_profit=c.execute(
        "SELECT amount FROM ledger WHERE owner_id=303 AND entry_type='resale_profit_credit' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    require(seller_profit is not None and float(seller_profit["amount"])>0,
            "Level 2 resale seller profit was not recorded")
    c.close()

    admin_card=next(x for x in admin_ctx.bot.sent if product_card_ids([x]))
    admin_data=sent_callback_data(admin_card)
    require(any(cb.startswith("cc:price:") for cb in admin_data)
            and any(cb.startswith("cc:stock:") for cb in admin_data)
            and any(cb.startswith("cc:toggle:") for cb in admin_data),
            "Level 2 admin product controls are incomplete")
    print("PASS: Level 2 50-product catalogue, 5-item pagination, images, stock, purchase and resale margin")
    print("PASS: Level 2 resale stays within level; cross-level wallet/stock protection works")


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
    stock_before=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(pid,)).fetchone()[0])
    c.execute("""INSERT INTO ledger(shop_id,owner_id,entry_type,amount,currency,note,created_at)
                 VALUES(1,101,'resale_purchase_debit',1000,'USDT','isolated test spend',?)""", (v4.now(),))
    c.commit()
    c.close()
    replies = run_central_buy(101, pid, 1)
    require(any("Insufficient USDT wallet balance" in x["text"] for x in replies),
            "Insufficient wallet purchase was not rejected")
    c = v4.db()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == stock_before,
            "Shared stock changed after rejected wallet purchase")
    c.close()
    c = v4.db()
    c.execute("UPDATE shops SET level=2 WHERE owner_id=101")
    c.commit(); c.close()
    replies = run_central_buy(101, pid, stock_before+1)
    require(any("Insufficient central stock" in x["text"] for x in replies),
            "Insufficient central stock purchase was not rejected")
    c = v4.db()
    c.execute("UPDATE shops SET level=1 WHERE owner_id=101")
    c.commit()
    require(c.execute("SELECT stock FROM central_catalog WHERE id=?", (pid,)).fetchone()[0] == stock_before,
            "Shared stock changed after rejected stock purchase")
    require(c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity check failed")
    c.close()
    print("PASS: Insufficient wallet and stock rejected without mutations")


def test_merchant_deposit_screenshot_and_admin_approval():
    owner_ctx=context()
    c=v4.db()
    credits_before=c.execute("SELECT COUNT(*) FROM ledger WHERE owner_id=101 AND entry_type='deposit_credit'").fetchone()[0]
    c.close()

    dashboard=v5.owner_kb()
    dashboard_callbacks=[button.callback_data for row in dashboard.inline_keyboard for button in row]
    require(dashboard_callbacks.count("m:deposit")==1 and "m:deposits" not in dashboard_callbacks,
            "Dashboard still shows duplicate Deposit and Deposits entries")

    center_update,center_message,_=callback_update(101,"m:deposit")
    asyncio.run(v5.menu_callback(center_update,owner_ctx))
    center_buttons=callback_data(center_message.replies[0])
    require("m:depositstart" in center_buttons and "m:deposits" in center_buttons and "m:dashboard" in center_buttons,
            "Deposit Center is missing Make Deposit, Deposit History, or Back buttons")

    start_update,start_message,_=callback_update(101,"m:depositstart")
    asyncio.run(v5.menu_callback(start_update,owner_ctx))
    require(owner_ctx.user_data.get("state")=="deposit_amount","Make Deposit did not start amount entry")
    amount_update,amount_message=message_update(101,"12.5")
    asyncio.run(v5.text_handler(amount_update,owner_ctx))
    amount_screen=owner_ctx.bot.sent[-1]["kwargs"]
    require("Choose payment network" in amount_screen["text"],
            "Deposit amount did not lead to network selection")
    network_button=next(button for row in amount_screen["reply_markup"].inline_keyboard
                        for button in row if button.callback_data and button.callback_data.startswith("net:"))
    rid=int(network_button.callback_data.split(":")[1])

    network_update,network_message,_=callback_update(101,network_button.callback_data)
    asyncio.run(v5.network_callback(network_update,owner_ctx))
    network_buttons=callback_data(network_message.replies[0])
    require("m:depositdone" in network_buttons and "I HAVE DEPOSITED" in network_message.replies[0]["text"],
            "Payment address screen is missing I HAVE DEPOSITED confirmation")
    require("m:dashboard" in network_buttons,"Payment address screen is missing Back to Dashboard")

    done_update,done_message,_=callback_update(101,"m:depositdone")
    asyncio.run(v5.menu_callback(done_update,owner_ctx))
    require(owner_ctx.user_data.get("state")=="payment_proof",
            "I HAVE DEPOSITED did not switch merchant to screenshot upload")
    require("UPLOAD YOUR PAYMENT SCREENSHOT" in done_message.replies[0]["text"],
            "Merchant was not asked for a payment screenshot")
    require("m:dashboard" in callback_data(done_message.replies[0]),
            "Screenshot prompt is missing Back to Dashboard")

    photo_update,photo_message=message_update(101,None)
    photo_message.photo=[SimpleNamespace(file_id="merchant-proof-photo")]
    asyncio.run(v4.payment_photo(photo_update,owner_ctx))
    require(any(x.get("photo") and x["args"][0]==999999 for x in owner_ctx.bot.sent),
            "Payment screenshot was not forwarded to Admin")
    c=v4.db()
    request=c.execute("SELECT status,screenshot_file_id,transaction_hash FROM payment_requests WHERE id=?",(rid,)).fetchone()
    credits=c.execute("SELECT COUNT(*) FROM ledger WHERE owner_id=101 AND entry_type='deposit_credit'").fetchone()[0]
    require(request["status"]=="pending" and request["screenshot_file_id"]=="merchant-proof-photo",
            "Screenshot was not saved while leaving request pending")
    require(request["transaction_hash"] in (None, ""),
            "A TXID was unexpectedly added when the merchant skipped the optional TXID step")
    require(credits==credits_before,"Wallet was credited before Admin approval")
    c.close()

    admin_update,admin_message,_=callback_update(999999,f"pay:approve:{rid}")
    admin_message.photo=[SimpleNamespace(file_id="merchant-proof-photo")]
    asyncio.run(v4.payment_review(admin_update,context()))
    c=v4.db()
    request=c.execute("SELECT status FROM payment_requests WHERE id=?",(rid,)).fetchone()
    credits_after=c.execute("SELECT COUNT(*) FROM ledger WHERE owner_id=101 AND entry_type='deposit_credit'").fetchone()[0]
    credit=c.execute("SELECT amount FROM ledger WHERE owner_id=101 AND entry_type='deposit_credit' AND note LIKE ? ORDER BY id DESC LIMIT 1", (f"%#{rid}%",)).fetchone()
    require(request["status"]=="approved","Admin approval did not update deposit status")
    require(credits_after==credits_before+1 and credit is not None and abs(float(credit["amount"])-12.5)<1e-8,
            "Approved deposit did not credit the merchant wallet exactly once")
    c.close()

    # The optional TXID button must lead to a real state transition and persist the hash.
    c=v4.db()
    cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
        VALUES(101,1,'wallet_deposit',0,4.0,'USDT','not_selected','pending',?)""",(v4.now(),))
    txid_rid=int(cur.lastrowid)
    c.commit(); c.close()
    txid_network_update,txid_network_message,_=callback_update(101,f"net:{txid_rid}:trc")
    txid_ctx=context()
    asyncio.run(v5.network_callback(txid_network_update,txid_ctx))
    require(f"txid:{txid_rid}" in callback_data(txid_network_message.replies[0]),
            "Deposit address screen is missing optional TXID action")
    txid_update,txid_prompt,_=callback_update(101,f"txid:{txid_rid}")
    asyncio.run(v5.txid_callback(txid_update,txid_ctx))
    require(txid_ctx.user_data.get("state")=="deposit_tx_hash",
            "Optional TXID button did not start TXID entry")
    hash_update,hash_message=message_update(101,"TXID1234567890abcdef")
    asyncio.run(v5.text_handler(hash_update,txid_ctx))
    require(txid_ctx.user_data.get("state")=="payment_proof",
            "Valid TXID did not continue to screenshot upload")
    proof_update,proof_message=message_update(101,None)
    proof_message.photo=[SimpleNamespace(file_id="txid-proof-photo")]
    asyncio.run(v4.payment_photo(proof_update,txid_ctx))
    c=v4.db()
    txid_row=c.execute("SELECT transaction_hash,screenshot_file_id,status FROM payment_requests WHERE id=?",(txid_rid,)).fetchone()
    c.close()
    require(txid_row["transaction_hash"]=="TXID1234567890abcdef" and
            txid_row["screenshot_file_id"]=="txid-proof-photo" and txid_row["status"]=="pending",
            "Optional TXID and screenshot were not saved together for Admin review")
    require(any("TXID1234567890abcdef" in x.get("kwargs",{}).get("caption","") for x in txid_ctx.bot.sent),
            "Admin proof card did not include the saved TXID")

    # A rejected deposit must leave Pending and must never credit the wallet.
    c=v4.db()
    cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
                     VALUES(101,1,'wallet_deposit',0,3.0,'USDT','USDT TRC20','pending',?)""",(v4.now(),))
    rejected_id=cur.lastrowid
    c.commit(); c.close()
    reject_update,reject_message,_=callback_update(999999,f"pay:reject:{rejected_id}")
    payment_reject_ctx=context()
    asyncio.run(v4.payment_review(reject_update,payment_reject_ctx))
    reason_update,reason_message=message_update(999999,"This is fake screenshot")
    asyncio.run(v5.text_handler(reason_update,payment_reject_ctx))
    c=v4.db()
    rejected=c.execute("SELECT status,admin_note FROM payment_requests WHERE id=?",(rejected_id,)).fetchone()
    reject_credit=c.execute("SELECT COUNT(*) FROM ledger WHERE owner_id=101 AND entry_type='deposit_credit' AND note LIKE ?",(f"%#{rejected_id}%",)).fetchone()[0]
    c.close()
    require(rejected["status"]=="rejected" and rejected["admin_note"]=="This is fake screenshot",
            "Rejected deposit did not leave Pending with the rejection reason")
    require(reject_credit==0,"Rejected deposit incorrectly credited the wallet")

    # Level activation follows the same deposit-style confirmation and screenshot sequence.
    c=v4.db()
    cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
                     VALUES(202,2,'level_activation',2,10.0,'USDT','not_selected','pending',?)""",(v4.now(),))
    level_rid=cur.lastrowid
    c.commit(); c.close()
    level_ctx=context()
    level_update,level_message,_=callback_update(202,f"net:{level_rid}:trc")
    asyncio.run(v5.network_callback(level_update,level_ctx))
    level_buttons=callback_data(level_message.replies[0])
    require(level_ctx.user_data.get("state")=="await_deposit_done",
            "Level activation did not wait for the merchant payment confirmation")
    require("m:depositdone" in level_buttons and f"txid:{level_rid}" in level_buttons,
            "Level upgrade address screen is missing I HAVE DEPOSITED or optional TXID")
    require("m:dashboard" in level_buttons and "USDT TRC20" in level_message.replies[0]["text"],
            "Level upgrade address screen is missing the selected network or Back to Dashboard")
    require("LEVEL UPGRADE PAYMENT ADDRESS" in level_message.replies[0]["text"],
            "Level upgrade address screen title is incorrect")

    level_done_update,level_done_message,_=callback_update(202,"m:depositdone")
    asyncio.run(v5.menu_callback(level_done_update,level_ctx))
    require(level_ctx.user_data.get("state")=="payment_proof",
            "I HAVE DEPOSITED did not move the level upgrade to screenshot upload")
    require("UPLOAD YOUR PAYMENT SCREENSHOT" in level_done_message.replies[0]["text"],
            "Level upgrade was not asked to upload payment proof")
    level_photo_update,level_photo_message=message_update(202,None)
    level_photo_message.photo=[SimpleNamespace(file_id="level-upgrade-proof-photo")]
    asyncio.run(v4.payment_photo(level_photo_update,level_ctx))
    c=v4.db()
    level_request=c.execute("SELECT status,screenshot_file_id FROM payment_requests WHERE id=?",(level_rid,)).fetchone()
    c.close()
    require(level_request["status"]=="pending" and
            level_request["screenshot_file_id"]=="level-upgrade-proof-photo",
            "Level upgrade screenshot was not saved for Admin review")

    # Optional TXID must also work for an upgrade payment, not only a wallet deposit.
    c=v4.db()
    cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
                     VALUES(202,2,'level_activation',3,15.0,'USDT','not_selected','pending',?)""",(v4.now(),))
    level_txid_rid=cur.lastrowid
    c.commit(); c.close()
    level_txid_ctx=context()
    tx_network_update,tx_network_message,_=callback_update(202,f"net:{level_txid_rid}:bep")
    asyncio.run(v5.network_callback(tx_network_update,level_txid_ctx))
    require(f"txid:{level_txid_rid}" in callback_data(tx_network_message.replies[0]),
            "Level upgrade payment screen is missing optional TXID")
    txid_update,txid_prompt,_=callback_update(202,f"txid:{level_txid_rid}")
    asyncio.run(v5.txid_callback(txid_update,level_txid_ctx))
    require(level_txid_ctx.user_data.get("state")=="deposit_tx_hash",
            "Level upgrade optional TXID did not start transaction-hash entry")
    hash_update,hash_message=message_update(202,"LEVELTXID1234567890abcdef")
    asyncio.run(v5.text_handler(hash_update,level_txid_ctx))
    require(level_txid_ctx.user_data.get("state")=="payment_proof",
            "Level upgrade TXID did not continue to screenshot upload")
    tx_photo_update,tx_photo_message=message_update(202,None)
    tx_photo_message.photo=[SimpleNamespace(file_id="level-upgrade-txid-proof")]
    asyncio.run(v4.payment_photo(tx_photo_update,level_txid_ctx))
    c=v4.db()
    tx_level_request=c.execute("SELECT transaction_hash,screenshot_file_id,status FROM payment_requests WHERE id=?",
                               (level_txid_rid,)).fetchone()
    c.close()
    require(tx_level_request["transaction_hash"]=="LEVELTXID1234567890abcdef" and
            tx_level_request["screenshot_file_id"]=="level-upgrade-txid-proof" and
            tx_level_request["status"]=="pending",
            "Level upgrade TXID and screenshot were not saved together for Admin review")
    print("PASS: Deposit Center and Level Upgrade share the same address, optional TXID, confirmation and proof flow")


def test_usdt_trc20_withdrawal_flow():
    available=v5.available_withdrawal_usdt(101)
    require(abs(available-6.8)<1e-8,"Withdrawable balance must use completed USDT resale proceeds only")

    owner_ctx=context()
    no_wallet_update,no_wallet_message,no_wallet_query=callback_update(101,"m:withdraw")
    asyncio.run(v5.menu_callback(no_wallet_update,owner_ctx))
    require("WITHDRAW SETUP REQUIRED" in no_wallet_message.replies[0]["text"],
            "Withdrawal was not gated on configured merchant wallet")
    require("m:withdrawsetup" in callback_data(no_wallet_message.replies[0]),
            "Withdraw setup shortcut is missing")

    setup_update,setup_message,setup_query=callback_update(101,"m:withdrawsetup")
    asyncio.run(v5.menu_callback(setup_update,owner_ctx))
    require("No payout address saved" in setup_message.replies[0]["text"],
            "New merchant does not see unset setup status")
    require("m:withdrawsetupedit" in callback_data(setup_message.replies[0]),
            "Set address button missing")

    edit_update,edit_message,edit_query=callback_update(101,"m:withdrawsetupedit")
    asyncio.run(v5.menu_callback(edit_update,owner_ctx))
    require(owner_ctx.user_data.get("state")=="withdraw_setup_address",
            "Withdraw Setup did not start address-entry state")
    bad_update,bad_message=message_update(101,"0x62eb80614112d7b6bdeacf07b745755605c4b7e0")
    asyncio.run(v5.text_handler(bad_update,owner_ctx))
    require("Invalid TRC20" in owner_ctx.bot.sent[-1]["kwargs"]["text"],
            "Non-TRC20 setup address was not rejected")
    require(owner_ctx.user_data.get("state")=="withdraw_setup_address",
            "Invalid address incorrectly cleared setup state")

    address="T"+"1"*33
    address_update,address_message=message_update(101,address)
    asyncio.run(v5.text_handler(address_update,owner_ctx))
    require(owner_ctx.user_data.get("state") is None,
            "Address setup state was not cleared after save")
    c=v4.db()
    wallet=c.execute("SELECT * FROM owner_wallets WHERE owner_id=101").fetchone()
    require(wallet is not None and wallet["network"]=="USDT TRC20" and wallet["address"]==address,
            "Merchant setup address was not stored")
    require(wallet["verified"]==0,"Saved wallet verification state should reset on address change")
    c.close()

    setup_screen_update,setup_screen,setup_screen_query=callback_update(101,"m:withdrawsetup")
    asyncio.run(v5.menu_callback(setup_screen_update,context()))
    require(address in setup_screen.replies[0]["text"] and "address saved" in setup_screen.replies[0]["text"].lower(),
            "Merchant cannot view their configured payout address")

    admin_update,admin_message,admin_query=callback_update(999999,"adm:withdrawwallets")
    asyncio.run(v5.admin_panel_callback(admin_update,context()))
    admin_cards=[x for x in admin_message.replies if address in x["text"]]
    require(admin_cards,"Admin Merchant Withdrawal Wallets screen does not show saved address")
    admin_card=admin_cards[0]
    require("Merchant ID: <code>101</code>" in admin_card["text"] and "USDT TRC20" in admin_card["text"],
            "Admin saved-wallet card is missing merchant or network details")
    copy_buttons=[button for row in admin_card["kwargs"]["reply_markup"].inline_keyboard for button in row]
    require(any(getattr(btn,"copy_text",None) and btn.copy_text.text==address for btn in copy_buttons),
            "Admin saved-wallet card has no copy-address button")

    withdraw_ctx=context()
    withdraw_update,withdraw_message,withdraw_query=callback_update(101,"m:withdraw")
    asyncio.run(v5.menu_callback(withdraw_update,withdraw_ctx))
    prompt=withdraw_message.replies[0]["text"]
    require("WITHDRAW USDT" in prompt and "6.800000 USDT" in prompt,
            "Withdrawal screen does not show available USDT balance")
    require(address in prompt,"Withdrawal screen does not show configured payout address")
    require("INR" not in prompt,"Withdrawal screen still mentions INR")

    amount_update,amount_message=message_update(101,"2.5")
    asyncio.run(v5.text_handler(amount_update,withdraw_ctx))
    submitted=withdraw_ctx.bot.sent[-2]["kwargs"]["text"]
    require("2.500000 USDT" in submitted and "PENDING ADMIN REVIEW" in submitted,
            "USDT withdrawal confirmation missing")
    require(address in submitted,"Withdrawal request did not use saved address")
    require(withdraw_ctx.user_data.get("state") is None,
            "Withdrawal state was not cleared after submission")
    require(withdraw_ctx.bot.sent[-2]["kwargs"].get("parse_mode")=="HTML",
            "Merchant withdrawal confirmation was sent without HTML parse mode")

    c=v4.db()
    row=c.execute("SELECT * FROM withdrawals WHERE owner_id=101 ORDER BY id DESC LIMIT 1").fetchone()
    require(row is not None and row["currency"]=="USDT" and row["network"]=="USDT TRC20",
            "Withdrawal was not stored as USDT TRC20")
    wid=int(row["id"])
    require(row["wallet_address"]==address and abs(float(row["amount"])-2.5)<1e-8,
            "Withdrawal did not use configured address or USDT amount")
    require(abs(v5.available_withdrawal_usdt(101)-4.3)<1e-8,
            "Pending withdrawal did not reserve withdrawable proceeds")
    c.close()

    admin_notices=[x for x in withdraw_ctx.bot.sent if x["args"] and x["args"][0]==999999]
    require(admin_notices and "2.500000 USDT" in admin_notices[-1]["args"][1]
            and address in admin_notices[-1]["args"][1],
            "Admin did not receive amount and configured TRC20 address")

    admin_wd_update,admin_wd_message,admin_wd_query=callback_update(999999,"adm:withdrawals")
    asyncio.run(v5.admin_panel_callback(admin_wd_update,context()))
    card=next((x["text"] for x in admin_wd_message.replies if address in x["text"]), "")
    require("TRC20 address" in card and "2.500000 USDT" in card,
            "Admin withdrawal review card is missing amount or payout address")
    require("INR" not in card,"Admin withdrawal card still displays INR")

    paid_update,paid_message,paid_query=callback_update(999999,f"wd:paid:{wid}")
    paid_ctx=context()
    asyncio.run(v5.withdrawal_review_callback(paid_update,paid_ctx))
    require(any(x["kwargs"].get("parse_mode")=="HTML" for x in paid_message.edits),
            "Admin withdrawal review result was edited without HTML parse mode")
    require(any("POST-WITHDRAWAL WALLET SUMMARY" in x["text"] and
                "Sale proceeds remaining: <b>4.300000 USDT</b>" in x["text"]
                for x in paid_message.replies),
            "Admin did not receive the post-payment remaining-balance summary")
    require(any(x["args"] and x["args"][0]==101 and len(x["args"])>1 and
                "POST-WITHDRAWAL WALLET SUMMARY" in x["args"][1]
                for x in paid_ctx.bot.sent),
            "Merchant did not receive the post-payment wallet summary")
    c=v4.db()
    paid=c.execute("SELECT status FROM withdrawals WHERE id=?",(wid,)).fetchone()
    debit=c.execute("SELECT amount,currency FROM ledger WHERE owner_id=101 AND entry_type='withdrawal_debit' ORDER BY id DESC LIMIT 1").fetchone()
    require(paid["status"]=="paid","Admin Mark Paid did not close withdrawal")
    require(debit and debit["currency"]=="USDT" and abs(float(debit["amount"])-2.5)<1e-8,
            "Paid withdrawal debit was not written in USDT")
    c.close()
    require(abs(v5.available_withdrawal_usdt(101)-4.3)<1e-8,
            "Paid withdrawal did not reduce available proceeds")

    reject_ctx=context()
    withdraw2_update,withdraw2_message,withdraw2_query=callback_update(101,"m:withdraw")
    asyncio.run(v5.menu_callback(withdraw2_update,reject_ctx))
    amount2_update,amount2_message=message_update(101,"1")
    asyncio.run(v5.text_handler(amount2_update,reject_ctx))
    c=v4.db()
    wid2=int(c.execute("SELECT id FROM withdrawals WHERE owner_id=101 AND status='pending' ORDER BY id DESC LIMIT 1").fetchone()[0])
    c.close()
    reject_update,reject_message,reject_query=callback_update(999999,f"wd:reject:{wid2}")
    withdrawal_reject_ctx=context()
    asyncio.run(v4.withdrawal_review(reject_update,withdrawal_reject_ctx))
    reason_update,reason_message=message_update(999999,"Withdrawal details need correction.")
    asyncio.run(v5.text_handler(reason_update,withdrawal_reject_ctx))
    c=v4.db()
    rejected=c.execute("SELECT status FROM withdrawals WHERE id=?",(wid2,)).fetchone()
    debit_count=c.execute("SELECT COUNT(*) FROM ledger WHERE owner_id=101 AND entry_type='withdrawal_debit'").fetchone()[0]
    require(rejected["status"]=="rejected" and debit_count==1,
            "Rejected withdrawal incorrectly debited funds")
    c.close()
    require(abs(v5.available_withdrawal_usdt(101)-4.3)<1e-8,
            "Rejected withdrawal did not release reserved amount")
    print("PASS: Merchant Withdraw Setup, saved TRC20 wallet, Admin wallet directory and USDT withdrawal lifecycle")

def test_usdt_balance_reconciliation():
    # Mirrors the reported figures: deposits 50, catalogue investment 138.7,
    # resale proceeds 169.3, recorded profit 55.6 and paid withdrawals 150.
    c=v4.db()
    # Run this last against the existing approved test merchant, after all other tests.
    c.execute("DELETE FROM ledger WHERE owner_id=101")
    c.execute("DELETE FROM withdrawals WHERE owner_id=101")
    sid=1
    for kind,amount in [
        ("deposit_credit",50.0),
        ("central_purchase_debit",138.7),
        ("resale_sale_credit",169.3),
        ("resale_profit_credit",55.6),
        ("withdrawal_debit",150.0),
    ]:
        c.execute("""INSERT INTO ledger(shop_id,owner_id,entry_type,amount,currency,note,created_at)
                     VALUES(?,101,?,?,'USDT','reconciliation test',?)""",
                  (sid,kind,amount,v4.now()))
    c.commit()
    summary=v5.usdt_wallet_summary(c,101)
    require(abs(summary["earned"]-169.3)<1e-8,"Total sale proceeds were not calculated correctly")
    require(abs(summary["paid"]-150.0)<1e-8,"Paid withdrawals were not included")
    require(abs(summary["sale_remaining"]-19.3)<1e-8,"169.3 minus 150 should leave 19.3 in sale proceeds")
    require(abs(summary["ledger_balance"]+69.4)<1e-8,
            "Net wallet ledger should reflect the 50 - 138.7 + 169.3 - 150 calculation")
    require(summary["available"]==0,
            "An overdrawn wallet must not allow another withdrawal")
    c.close()

    update,message,query=callback_update(101,"m:wallet")
    asyncio.run(v5.menu_callback(update,context()))
    screen=message.replies[0]["text"]
    require("Wallet shortfall: 69.4 USDT" in screen,
            f"Merchant wallet did not show the net wallet shortfall; screen was: {screen!r}")
    require("Sale proceeds remaining: 19.3 USDT" in screen,
            "Merchant wallet did not show 169.3 minus 150 = 19.3")
    require("Available to withdraw: <b>0 USDT</b>" in screen,
            "Merchant was shown withdrawable funds despite a negative net wallet")
    print("PASS: Wallet reconciliation distinguishes remaining sale proceeds from net withdrawable balance")


def test_runtime_schema_bootstrap():
    old_v4_db, old_v3_db = v4.DB, v4.v3.DB
    with tempfile.TemporaryDirectory(prefix="shopindia_fresh_") as tmp:
        fresh_db = str(Path(tmp) / "fresh.db")
        try:
            v4.DB = fresh_db
            v4.v3.DB = fresh_db
            v4.v3.setup()
            # Simulate shops that existed before the new permanent ID migration.
            legacy_conn=sqlite3.connect(fresh_db)
            legacy_conn.execute(
                "INSERT INTO shops(owner_id,shop_name,status,created_at) VALUES(701,'Legacy Shop A','approved','2025-01-01')")
            legacy_conn.execute(
                "INSERT INTO shops(owner_id,shop_name,status,created_at) VALUES(702,'Legacy Shop B','approved','2025-01-02')")
            legacy_conn.commit()
            legacy_conn.close()
            require(runtime_schema.ensure_schema(fresh_db) == "ok",
                    "Fresh database schema bootstrap failed")
            v4.v3.ensure_catalog_schema()
            cc.setup()
            cc.resale_setup()
            conn = sqlite3.connect(fresh_db)
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"users", "shops", "products", "payment_requests", "withdrawals",
                        "payment_history", "level_settings", "platform_settings", "ledger",
                        "v4_orders", "central_catalog", "central_orders", "central_inventory",
                        "resale_listings", "merchant_referrals", "referral_rewards",
                        "location_geocode_cache"}
            require(required.issubset(names),
                    f"Fresh install missing tables: {sorted(required - names)}")
            shop_cols = {r[1] for r in conn.execute("PRAGMA table_info(shops)")}
            require({"referred_by_owner_id","location_country","location_state","location_city","shop_code"}.issubset(shop_cols),
                    "Fresh shops schema is missing referral, merchant-location or unique shop-code fields")
            legacy_codes=conn.execute("SELECT shop_code FROM shops WHERE owner_id IN (701,702) ORDER BY owner_id").fetchall()
            require([r[0] for r in legacy_codes]==["SHOP-000001","SHOP-000002"],
                    f"Existing shops were not backfilled with permanent unique IDs: {legacy_codes!r}")
            conn.execute("INSERT INTO shops(owner_id,shop_name,status,created_at) VALUES(703,'New Shop','approved','2026-01-03')")
            new_code=conn.execute("SELECT shop_code FROM shops WHERE owner_id=703").fetchone()[0]
            require(new_code=="SHOP-000003","New shop did not receive an automatic permanent unique ID")
            require(conn.execute("SELECT COUNT(DISTINCT shop_code) FROM shops").fetchone()[0]==3,
                    "Shop IDs are not unique")
            require(not {"latitude","longitude","location_latitude","location_longitude"}.intersection(shop_cols),
                    "Exact merchant GPS coordinates must not be stored in the shops table")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(payment_requests)")}
            require({"transaction_hash", "tx_hash", "screenshot_file_id"}.issubset(cols),
                    "Fresh payment_requests schema is missing proof columns")
            conn.execute("INSERT INTO shops(owner_id,shop_name,status,created_at,level) VALUES(777,'Preserved Shop','approved','2026-01-01',1)")
            conn.commit()
            conn.close()
            require(runtime_schema.ensure_schema(fresh_db) == "ok",
                    "Second schema bootstrap was not idempotent")
            conn = sqlite3.connect(fresh_db)
            preserved = conn.execute("SELECT shop_name FROM shops WHERE owner_id=777").fetchone()
            require(preserved and preserved[0] == "Preserved Shop",
                    "Repeated schema bootstrap changed existing shop data")
            require(conn.execute("SELECT COUNT(*) FROM level_settings").fetchone()[0] == 5,
                    "Default shop levels were not initialized")
            level2_settings=conn.execute(
                "SELECT activation_usdt,product_target,profit_min,profit_max FROM level_settings WHERE level=2"
            ).fetchone()
            require(tuple(level2_settings)==(50.0,50,2.0,6.5),
                    f"Level 2 defaults are wrong: {tuple(level2_settings)!r}")
            level4_price=conn.execute(
                "SELECT activation_usdt FROM level_settings WHERE level=4"
            ).fetchone()
            require(level4_price and float(level4_price[0])==250.0,
                    f"Level 4 activation price must be 250 USDT: {level4_price!r}")
            level1_price=conn.execute(
                "SELECT activation_usdt FROM level_settings WHERE level=1"
            ).fetchone()
            require(level1_price and float(level1_price[0])==10.0,
                    f"Level 1 activation price must be 10 USDT: {level1_price!r}")
            level2_products=conn.execute(
                "SELECT COUNT(*),MIN(stock),MAX(stock) FROM central_catalog WHERE level_required=2"
            ).fetchone()
            require(tuple(level2_products)==(50,50,50),
                    f"Level 2 catalogue seed must have 50 products with 50 stock each: {tuple(level2_products)!r}")
            conn.close()
        finally:
            v4.DB = old_v4_db
            v4.v3.DB = old_v3_db
    print("PASS: Fresh-install schema bootstrap, required tables, and repeat-run data preservation")


def test_admin_refund_safety():
    c=v4.db()
    pid=int(c.execute("SELECT id FROM central_catalog ORDER BY id LIMIT 1").fetchone()[0])
    c.execute("UPDATE central_catalog SET stock=10 WHERE id=?",(pid,))
    stock_before=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(pid,)).fetchone()[0])
    cur=c.execute("""INSERT INTO central_orders(catalog_id,buyer_shop_id,buyer_owner_id,quantity,
        unit_price_usdt,total_usdt,status,created_at) VALUES(?,1,101,2,2.5,5,'paid',?)""",
        (pid,v4.now()))
    oid=int(cur.lastrowid)
    cur=c.execute("""INSERT INTO central_inventory(owner_id,shop_id,catalog_id,order_id,quantity,
        remaining_quantity,unit_cost_usdt,status,created_at)
        VALUES(101,1,?,?,2,2,2.5,'owned',?)""",(pid,oid,v4.now()))
    inv_id=int(cur.lastrowid)
    cur=c.execute("""INSERT INTO resale_listings(inventory_id,owner_id,shop_id,catalog_id,quantity,
        remaining_quantity,unit_cost_usdt,ask_price_usdt,status,created_at)
        VALUES(?,101,1,?,2,2,2.5,3.3,'active',?)""",(inv_id,pid,v4.now()))
    listing_id=int(cur.lastrowid)
    c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
        VALUES(?,1,101,'central_purchase_debit',5,'USDT','refund safety test',?)""",(oid,v4.now()))
    c.commit(); c.close()

    update,message,query=callback_update(999999,f"cc:refund:{oid}")
    asyncio.run(cc.admin_callback(update,context()))
    c=v4.db()
    order=c.execute("SELECT status FROM central_orders WHERE id=?",(oid,)).fetchone()
    inventory=c.execute("SELECT remaining_quantity,status FROM central_inventory WHERE id=?",(inv_id,)).fetchone()
    listing=c.execute("SELECT remaining_quantity,status FROM resale_listings WHERE id=?",(listing_id,)).fetchone()
    stock_after=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(pid,)).fetchone()[0])
    refund_count=c.execute("""SELECT COUNT(*) FROM ledger WHERE transaction_id=?
        AND entry_type='central_purchase_refund_credit'""",(oid,)).fetchone()[0]
    c.close()
    require(order["status"]=="refunded","Safe catalogue refund did not close the order")
    require(inventory["remaining_quantity"]==0 and inventory["status"]=="refunded",
            "Refund did not revoke the buyer's unused inventory")
    require(listing["remaining_quantity"]==0 and listing["status"]=="cancelled",
            "Refund left a live resale listing behind")
    require(stock_after==stock_before+2 and refund_count==1,
            "Refund did not restore catalogue stock and wallet exactly once")

    # Refuse a refund if any item from the original purchase has already been resold.
    c=v4.db()
    cur=c.execute("""INSERT INTO central_orders(catalog_id,buyer_shop_id,buyer_owner_id,quantity,
        unit_price_usdt,total_usdt,status,created_at) VALUES(?,1,101,2,2.5,5,'paid',?)""",
        (pid,v4.now()))
    sold_oid=int(cur.lastrowid)
    cur=c.execute("""INSERT INTO central_inventory(owner_id,shop_id,catalog_id,order_id,quantity,
        remaining_quantity,unit_cost_usdt,status,created_at)
        VALUES(101,1,?,?,2,1,2.5,'owned',?)""",(pid,sold_oid,v4.now()))
    sold_inv=int(cur.lastrowid)
    c.execute("""INSERT INTO resale_listings(inventory_id,owner_id,shop_id,catalog_id,quantity,
        remaining_quantity,unit_cost_usdt,ask_price_usdt,status,created_at)
        VALUES(?,101,1,?,2,1,2.5,3.3,'active',?)""",(sold_inv,pid,v4.now()))
    c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
        VALUES(?,1,101,'central_purchase_debit',5,'USDT','sold refund safety test',?)""",(sold_oid,v4.now()))
    c.commit(); c.close()
    sold_update,sold_message,_=callback_update(999999,f"cc:refund:{sold_oid}")
    asyncio.run(cc.admin_callback(sold_update,context()))
    c=v4.db()
    sold_order=c.execute("SELECT status FROM central_orders WHERE id=?",(sold_oid,)).fetchone()
    sold_stock=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(pid,)).fetchone()[0])
    sold_refunds=c.execute("""SELECT COUNT(*) FROM ledger WHERE transaction_id=?
        AND entry_type='central_purchase_refund_credit'""",(sold_oid,)).fetchone()[0]
    c.close()
    require(sold_order["status"]=="paid" and sold_stock==stock_after and sold_refunds==0,
            "Refund was not blocked after inventory had been resold")

    # Merchant-to-merchant resale orders must never replenish central shared stock.
    c=v4.db()
    cur=c.execute("""INSERT INTO central_orders(catalog_id,buyer_shop_id,buyer_owner_id,quantity,
        unit_price_usdt,total_usdt,status,created_at) VALUES(?,2,202,1,3.3,3.3,'paid',?)""",
        (pid,v4.now()))
    resale_oid=int(cur.lastrowid)
    c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
        VALUES(?,2,202,'resale_purchase_debit',3.3,'USDT','resale refund guard',?)""",(resale_oid,v4.now()))
    c.commit(); c.close()
    resale_update,resale_message,_=callback_update(999999,f"cc:refund:{resale_oid}")
    asyncio.run(cc.admin_callback(resale_update,context()))
    c=v4.db()
    resale_order=c.execute("SELECT status FROM central_orders WHERE id=?",(resale_oid,)).fetchone()
    resale_stock=int(c.execute("SELECT stock FROM central_catalog WHERE id=?",(pid,)).fetchone()[0])
    c.close()
    require(resale_order["status"]=="paid" and resale_stock==stock_after,
            "Merchant resale refund incorrectly changed central shared stock")
    print("PASS: Refund revokes unused inventory, blocks sold-stock refunds, and protects resale orders")


def test_referral_registration_and_bonuses():
    # Existing merchants receive stable invitation codes.
    c=v4.db()
    invite_code=v5.ensure_referral_code(c,101)
    c.commit()
    require(bool(invite_code),"Existing approved merchant did not receive an invite code")
    c.close()

    signup_ctx=context()
    signup_update,signup_message=message_update(505,"🏪 Register Shop")
    signup_update.effective_user=SimpleNamespace(id=505,username="referral_child",first_name="Referral Child")
    asyncio.run(v5.text_handler(signup_update,signup_ctx))
    require("What would you like to name your shop?" in signup_message.replies[0]["text"],
            "Register Shop did not ask for the shop name in English")
    require(signup_ctx.user_data.get("state")=="shop_name",
            "Registration did not enter the shop-name state")

    name_update,name_message=message_update(505,"Referral Child Shop")
    name_update.effective_user=signup_update.effective_user
    asyncio.run(v5.text_handler(name_update,signup_ctx))
    require(signup_ctx.user_data.get("state")=="referral_code",
            "Registration did not require an invitation code after shop name")
    require("invitation code" in name_message.replies[0]["text"].lower(),
            "Registration did not explain the required invitation code")

    bad_update,bad_message=message_update(505,"NOT-A-VALID-CODE")
    bad_update.effective_user=signup_update.effective_user
    asyncio.run(v5.text_handler(bad_update,signup_ctx))
    require(signup_ctx.user_data.get("state")=="referral_code" and
            "invalid" in bad_message.replies[0]["text"].lower(),
            "Invalid referral code was not rejected")

    code_update,code_message=message_update(505,invite_code)
    code_update.effective_user=signup_update.effective_user
    asyncio.run(v5.text_handler(code_update,signup_ctx))
    require(signup_ctx.user_data.get("state")=="shop_location" and
            signup_ctx.user_data.get("referrer_owner_id")==101,
            "Valid invitation code did not require location sharing before password creation")
    require("LOCATION" in code_message.replies[0]["text"].upper() and
            code_message.replies[0]["kwargs"]["reply_markup"].keyboard[0][0].request_location,
            "Registration did not request Telegram's location-sharing permission")
    bypass_update,bypass_message=message_update(505,"StrongPassword123")
    bypass_update.effective_user=signup_update.effective_user
    asyncio.run(v5.text_handler(bypass_update,signup_ctx))
    require(signup_ctx.user_data.get("state")=="shop_location" and
            "required" in bypass_message.replies[0]["text"].lower(),
            "Merchant could bypass mandatory location by sending a password as text")

    async def fake_reverse_geocode(latitude,longitude):
        require(latitude==33.6844 and longitude==73.0479,
                "Shared Telegram coordinates were not forwarded for geocoding")
        return {"country":"Pakistan","state":"Punjab","city":"Rawalpindi"}
    old_geocoder=v5.reverse_geocode
    v5.reverse_geocode=fake_reverse_geocode
    try:
        location_message=Message()
        location_message.location=SimpleNamespace(latitude=33.6844,longitude=73.0479)
        location_update=SimpleNamespace(message=location_message,effective_user=signup_update.effective_user)
        asyncio.run(v5.location_handler(location_update,signup_ctx))
    finally:
        v5.reverse_geocode=old_geocoder
    require(signup_ctx.user_data.get("state")=="shop_password" and
            signup_ctx.user_data.get("location_country")=="Pakistan" and
            signup_ctx.user_data.get("location_state")=="Punjab" and
            signup_ctx.user_data.get("location_city")=="Rawalpindi",
            "Shared location was not converted into country/state/city before password creation")

    password_update,password_message=message_update(505,"StrongPassword123")
    password_update.effective_user=signup_update.effective_user
    asyncio.run(v5.text_handler(password_update,signup_ctx))
    c=v4.db()
    child=c.execute("SELECT id,status,level,referred_by_owner_id,location_country,location_state,location_city FROM shops WHERE owner_id=505").fetchone()
    require(child is not None and child["referred_by_owner_id"]==101,
            "New shop registration did not persist its inviter")
    require((child["location_country"],child["location_state"],child["location_city"])==
            ("Pakistan","Punjab","Rawalpindi"),
            "New shop registration did not persist the detected merchant location")
    require(any("Country: <b>Pakistan</b>" in x["args"][1] and "City: <b>Rawalpindi</b>" in x["args"][1]
                for x in signup_ctx.bot.sent if x["args"] and x["args"][0]==999999),
            "Admin shop application did not include merchant country/state/city")
    child_shop_id=int(child["id"])
    c.execute("UPDATE shops SET status='approved',level=1 WHERE id=?",(child_shop_id,))
    c.commit()
    c.close()

    # A $10 verified wallet deposit must add $10 deposit + $2 bonus to the child
    # and $0.20 referral bonus to the inviter, only after Admin approval.
    c=v4.db()
    cur=c.execute("""INSERT INTO payment_requests
        (owner_id,shop_id,payment_type,level,amount,currency,network,screenshot_file_id,status,created_at)
        VALUES(505,?,'wallet_deposit',0,10,'USDT','USDT TRC20','deposit-proof','pending',?)""",
        (child_shop_id,v4.now()))
    deposit_rid=int(cur.lastrowid)
    c.commit()
    before_pending=c.execute(
        "SELECT COUNT(*) FROM ledger WHERE owner_id=505 AND entry_type='deposit_bonus_credit'"
    ).fetchone()[0]
    c.close()
    require(before_pending==0,"Deposit bonus was credited before Admin approval")

    admin_update,admin_message,_=callback_update(999999,f"pay:approve:{deposit_rid}")
    asyncio.run(v4.payment_review(admin_update,context()))
    c=v4.db()
    base_credit=c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=505 AND entry_type='deposit_credit'"
    ).fetchone()[0]
    child_bonus=c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=505 AND entry_type='deposit_bonus_credit'"
    ).fetchone()[0]
    inviter_bonus=c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=101 AND entry_type='referral_bonus_credit'"
    ).fetchone()[0]
    require(abs(float(base_credit)-10.0)<1e-8 and abs(float(child_bonus)-2.0)<1e-8,
            "Approved $10 deposit did not credit the base amount plus 20% bonus")
    require(abs(float(inviter_bonus)-0.2)<1e-8,
            "Inviter did not receive 2% of the referred merchant's verified deposit")
    c.close()

    # Repeated clicks on the same reviewed request must not duplicate any reward.
    duplicate_update,duplicate_message,_=callback_update(999999,f"pay:approve:{deposit_rid}")
    asyncio.run(v4.payment_review(duplicate_update,context()))
    c=v4.db()
    require(c.execute(
        "SELECT COUNT(*) FROM ledger WHERE owner_id=505 AND entry_type='deposit_bonus_credit'"
    ).fetchone()[0]==1,"Duplicate approval credited the deposit bonus twice")
    require(c.execute(
        "SELECT COUNT(*) FROM referral_rewards WHERE payment_request_id=?",(deposit_rid,)
    ).fetchone()[0]==1,"Duplicate approval created a duplicate referral reward")
    c.close()

    # A Level 2 activation payment is also eligible for the 20% bonus and the
    # inviter's 2% payment reward.
    c=v4.db()
    cur=c.execute("""INSERT INTO payment_requests
        (owner_id,shop_id,payment_type,level,amount,currency,network,screenshot_file_id,status,created_at)
        VALUES(505,?,'level_activation',2,10,'USDT','USDT BEP20','upgrade-proof','pending',?)""",
        (child_shop_id,v4.now()))
    level_rid=int(cur.lastrowid)
    c.commit()
    c.close()
    level_update,level_message,_=callback_update(999999,f"pay:approve:{level_rid}")
    asyncio.run(v4.payment_review(level_update,context()))
    c=v4.db()
    child_bonus_total=float(c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=505 AND entry_type='deposit_bonus_credit'"
    ).fetchone()[0])
    inviter_bonus_total=float(c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=101 AND entry_type='referral_bonus_credit'"
    ).fetchone()[0])
    child_level=int(c.execute("SELECT level FROM shops WHERE id=?",(child_shop_id,)).fetchone()[0])
    rewards=c.execute(
        "SELECT COUNT(*),COALESCE(SUM(bonus_amount),0) FROM referral_rewards WHERE referrer_owner_id=101"
    ).fetchone()
    require(child_level==2,"Approved Level 2 activation payment did not unlock Level 2")
    require(abs(child_bonus_total-4.0)<1e-8,
            "Two approved $10 payments did not award two 20% bonuses")
    require(abs(inviter_bonus_total-0.4)<1e-8 and rewards[0]==2 and abs(float(rewards[1])-0.4)<1e-8,
            "Inviter did not receive 2% on both verified deposit and level-payment transactions")
    level_payment_credit=float(c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=505 AND entry_type='level_activation_wallet_credit'"
    ).fetchone()[0])
    require(abs(level_payment_credit-10.0)<1e-8,
            "Approved level activation did not credit the full 10 USDT payment")
    require(abs(cc.balance(c,505)-24.0)<1e-8,
            "Wallet balance must include the base deposit, full level payment and both 20% bonuses")
    c.close()

    # The inviter dashboard must list the new merchant and show accumulated rewards.
    referral_update,referral_message,_=callback_update(101,"m:referrals")
    asyncio.run(v5.menu_callback(referral_update,context()))
    screen=referral_message.replies[0]["text"]
    require("Referral Child Shop" in screen and invite_code in screen,
            "Invited Merchants screen does not show the invited shop and its code")
    require("0.400000 USDT" in screen and "2% of each invited merchant" in screen,
            "Invited Merchants screen does not show the earned 2% rewards")
    buttons=referral_message.replies[0]["kwargs"]["reply_markup"].inline_keyboard
    require(any(getattr(button,"copy_text",None) and button.copy_text.text==invite_code
                for row in buttons for button in row),
            "Invited Merchants screen is missing the copy invite-code button")
    print("PASS: Merchant referral registration, invite list, 20% verified payment bonuses and 2% upline rewards")


def test_admin_rejection_reasons():
    # Payment rejection must pause for a reason and notify the merchant with it.
    c=v4.db()
    shop=c.execute("SELECT id FROM shops WHERE owner_id=101 ORDER BY id DESC LIMIT 1").fetchone()
    cur=c.execute("""INSERT INTO payment_requests
        (owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
        VALUES(101,?,'wallet_deposit',0,4,'USDT','USDT TRC20','pending',?)""",
        (shop["id"],v4.now()))
    payment_id=int(cur.lastrowid); c.commit(); c.close()
    admin_ctx=context()
    pu,pm,pq=callback_update(999999,f"pay:reject:{payment_id}")
    asyncio.run(v4.payment_review(pu,admin_ctx))
    require(admin_ctx.user_data.get("pending_admin_rejection")=={"kind":"payment","id":payment_id},
            "Payment rejection did not request an Admin reason")
    reason="Transaction screenshot is unclear."
    ru,rm=message_update(999999,reason)
    asyncio.run(v5.text_handler(ru,admin_ctx))
    c=v4.db(); payment=c.execute("SELECT status,admin_note FROM payment_requests WHERE id=?",(payment_id,)).fetchone(); c.close()
    require(payment["status"]=="rejected" and payment["admin_note"]==reason,
            "Payment rejection reason was not saved")
    require(any(x["args"] and x["args"][0]==101 and reason in (x["args"][1] if len(x["args"])>1 else x["kwargs"].get("text",""))
                for x in admin_ctx.bot.sent),
            "Merchant did not receive the payment rejection reason")

    # Withdrawal rejection must preserve the reserved amount and send its reason.
    c=v4.db()
    cur=c.execute("""INSERT INTO withdrawals
        (owner_id,shop_id,amount,currency,network,wallet_address,status,created_at)
        VALUES(101,? ,1.25,'USDT','USDT TRC20','T111111111111111111111111111111111','pending',?)""",
        (shop["id"],v4.now()))
    withdrawal_id=int(cur.lastrowid); c.commit(); c.close()
    wu,wm,wq=callback_update(999999,f"wd:reject:{withdrawal_id}")
    asyncio.run(v4.withdrawal_review(wu,admin_ctx))
    require(admin_ctx.user_data.get("pending_admin_rejection")=={"kind":"withdrawal","id":withdrawal_id},
            "Withdrawal rejection did not request an Admin reason")
    wreason="Payout address does not match the verified wallet."
    wru,wrm=message_update(999999,wreason)
    asyncio.run(v5.text_handler(wru,admin_ctx))
    c=v4.db(); withdrawal=c.execute("SELECT status,admin_note FROM withdrawals WHERE id=?",(withdrawal_id,)).fetchone(); c.close()
    require(withdrawal["status"]=="rejected" and withdrawal["admin_note"]==wreason,
            "Withdrawal rejection reason was not saved")
    require(any(x["args"] and x["args"][0]==101 and wreason in (x["args"][1] if len(x["args"])>1 else x["kwargs"].get("text",""))
                for x in admin_ctx.bot.sent),
            "Merchant did not receive the withdrawal rejection reason")

    # Product suggestion rejection must also collect a reason and notify the owner.
    v5.v3.ensure_catalog_schema()
    c=v4.db()
    cur=c.execute("""INSERT INTO product_suggestions
        (owner_id,shop_id,title,description,suggested_price,status,created_at)
        VALUES(101,?,'Test Listing','Test description',100,'pending',?)""",
        (shop["id"],v4.now()))
    suggestion_id=int(cur.lastrowid); c.commit(); c.close()
    pu2,pm2,pq2=callback_update(999999,f"product:reject:{suggestion_id}")
    asyncio.run(v5.v3.product_review(pu2,admin_ctx))
    require(admin_ctx.user_data.get("pending_admin_rejection")=={"kind":"product_suggestion","id":suggestion_id},
            "Product listing rejection did not request an Admin reason")
    preason="Product photos or details need correction."
    pru,prm=message_update(999999,preason)
    asyncio.run(v5.text_handler(pru,admin_ctx))
    c=v4.db(); product=c.execute("SELECT status FROM product_suggestions WHERE id=?",(suggestion_id,)).fetchone(); c.close()
    require(product["status"]=="rejected","Product suggestion was not rejected after reason entry")
    require(any(x["args"] and x["args"][0]==101 and preason in (x["args"][1] if len(x["args"])>1 else x["kwargs"].get("text",""))
                for x in admin_ctx.bot.sent),
            "Merchant did not receive the product listing rejection reason")
    print("PASS: Admin rejection reasons saved and delivered for deposits, withdrawals and product listings")


def test_admin_shop_controls_and_search():
    c=v4.db()
    shop=c.execute("SELECT id FROM shops WHERE owner_id=101 ORDER BY id DESC LIMIT 1").fetchone()
    require(shop is not None,"Shop control fixture is missing")
    sid=int(shop["id"])
    c.close()

    # Admin can freeze and the shared order gate blocks purchases.
    update,message,_=callback_update(999999,f"admshop:freeze:{sid}")
    asyncio.run(v5.admin_shop_callback(update,context()))
    c=v4.db()
    status=c.execute("SELECT control_status FROM shops WHERE id=?",(sid,)).fetchone()[0]
    allowed,reason=v4.shop_order_gate(c,sid)
    c.close()
    require(status=="frozen" and not allowed and "frozen" in reason,
            "Freeze did not persist or block new orders")

    # Enable restores access.
    update,message,_=callback_update(999999,f"admshop:enable:{sid}")
    asyncio.run(v5.admin_shop_callback(update,context()))
    c=v4.db()
    require(c.execute("SELECT control_status FROM shops WHERE id=?",(sid,)).fetchone()[0]=="active",
            "Enable did not restore the shop")
    c.close()

    # Warning requires an Admin-entered reason and is consumed once by next successful order.
    admin_ctx=context()
    update,message,_=callback_update(999999,f"admshop:warn:{sid}")
    asyncio.run(v5.admin_shop_callback(update,admin_ctx))
    require(admin_ctx.user_data.get("admin_shop_warning_id")==sid,
            "Warn Next Order did not request a reason")
    reason="Please verify product descriptions before accepting another order."
    warning_update,warning_message=message_update(999999,reason)
    asyncio.run(v5.text_handler(warning_update,admin_ctx))
    c=v4.db()
    warning=c.execute("SELECT next_order_warning,warning_reason FROM shops WHERE id=?",(sid,)).fetchone()
    c.close()
    require(warning["next_order_warning"]==1 and warning["warning_reason"]==reason,
            "Mandatory next-order warning reason was not saved")
    c=v4.db()
    allowed,shown=v4.shop_order_gate(c,sid)
    c.close()
    require(not allowed and reason in shown and "NOT placed" in shown,
            "Next-order warning did not stop the first attempt and show its reason")
    c=v4.db()
    allowed_again,shown_again=v4.shop_order_gate(c,sid)
    c.close()
    require(allowed_again and shown_again is None,
            "Shop was not allowed to retry after the one-time warning was shown")

    # Search accepts both the formatted unique ID and the underlying numeric ID.
    search_ctx=context()
    search_update,search_message,_=callback_update(999999,"admshop:search")
    asyncio.run(v5.admin_shop_callback(search_update,search_ctx))
    require(search_ctx.user_data.get("admin_shop_search") is True,
            "Shop ID search did not enter search mode")
    numeric_update,numeric_message=message_update(999999,f"SHOP-{sid:06d}")
    asyncio.run(v5.text_handler(numeric_update,search_ctx))
    require(any(f"SHOP-{sid:06d}" in x["text"] for x in numeric_message.replies),
            "Shop ID search did not return the exact matching shop")

    # Disable and soft-delete also block orders without removing historical rows.
    for action,expected in (("disable","disabled"),("delete","deleted")):
        update,message,_=callback_update(999999,f"admshop:{action}:{sid}")
        asyncio.run(v5.admin_shop_callback(update,context()))
        c=v4.db()
        row=c.execute("SELECT control_status FROM shops WHERE id=?",(sid,)).fetchone()
        allowed,reason=v4.shop_order_gate(c,sid)
        c.close()
        require(row["control_status"]==expected and not allowed,
                f"{action} did not block new orders")
    c=v4.db()
    require(c.execute("SELECT COUNT(*) FROM shops WHERE id=?",(sid,)).fetchone()[0]==1,
            "Soft delete removed the shop record and historical links")
    c.execute("UPDATE shops SET control_status='active',control_note=NULL WHERE id=?",(sid,))
    c.commit(); c.close()
    print("PASS: Unique Shop ID search, freeze/disable/enable/soft-delete controls and one-time reasoned next-order warning")


def main():
    test_runtime_schema_bootstrap()
    fixture()
    test_admin_shop_controls_and_search()
    test_level1_catalogue_quantity_buttons()
    test_admin_catalogue_pagination()
    pid, listing_id, inventory_id = test_central_purchase_and_auto_listing()
    test_owner_wallet_and_inventory_design()
    test_list_stock_profit_screen()
    test_seller_market_screen(listing_id)
    test_resale_buyer_guards_and_sale(listing_id, inventory_id)
    test_merchant_level_upgrade_option()
    test_level2_catalogue_and_purchase_flow()
    test_admin_resale_flow(listing_id, inventory_id)
    test_merchant_deposit_screenshot_and_admin_approval()
    test_usdt_trc20_withdrawal_flow()
    test_failed_central_purchases(pid)
    test_admin_refund_safety()
    test_usdt_balance_reconciliation()
    test_referral_registration_and_bonuses()
    test_admin_rejection_reasons()
    print("PASS: All isolated Telegram-handler end-to-end tests completed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        TMP.cleanup()
