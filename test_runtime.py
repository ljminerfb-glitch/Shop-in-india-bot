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
    nav=next(x for x in ctx.bot.sent if "ccpage:1" in sent_callback_data(x))
    require("PAGE 1/3" in sent_text(nav),
            "First catalogue page indicator or Next 5 button is missing")
    before=len(ctx.bot.sent)
    next_update,_,_=callback_update(101,"ccpage:1")
    asyncio.run(cc.owner_catalog_page_callback(next_update,ctx))
    next_page_sent=ctx.bot.sent[before:]
    require(product_card_ids(next_page_sent)==expected_ids[5:10],
            "Next 5 did not show products 6 through 10")
    next_nav=next(x for x in next_page_sent if any(cb.startswith("ccpage:") for cb in sent_callback_data(x)))
    next_nav_buttons=sent_callback_data(next_nav)
    require("ccpage:0" in next_nav_buttons and "ccpage:2" in next_nav_buttons,
            "Middle page must provide Previous 5 and Next 5 buttons")
    before=len(ctx.bot.sent)
    last_update,_,_=callback_update(101,"ccpage:2")
    asyncio.run(cc.owner_catalog_page_callback(last_update,ctx))
    last_page_sent=ctx.bot.sent[before:]
    require(product_card_ids(last_page_sent)==expected_ids[10:],
            "Final catalogue page should show only the remaining products")
    last_nav=next(x for x in last_page_sent if any(cb.startswith("ccpage:") for cb in sent_callback_data(x)))
    require("ccpage:3" not in sent_callback_data(last_nav) and
            "ccpage:1" in sent_callback_data(last_nav),
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
    expected=[f"ccbuy:{pid}:{n}" for n in range(1,11)]
    require(callbacks[:10]==expected,"Level 1 catalogue does not show quantities 1 through 10")
    require(visible_labels[:10]==[str(n) for n in range(1,11)],
            f"Quantity button labels are not visible numbers: {visible_labels[:10]!r}")
    require([len(row) for row in markup.inline_keyboard[:5]]==[2,2,2,2,2],
            "Quantity buttons are not arranged in compact two-button rows")
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
    require(callbacks[:3]==[f"ccbuy:{pid}:{n}" for n in range(1,4)] and
            f"ccbuy:{pid}:4" not in callbacks,
            "Catalogue offers quantities above available stock")
    print("PASS: Level 1 catalogue Buy ×1–×10 buttons and stock cap")


def test_admin_catalogue_pagination():
    update,_,_=callback_update(999999,"ccadminlevel:1")
    ctx=context()
    asyncio.run(cc.admin_level_callback(update,ctx))
    ids=[int(r[0]) for r in v4.db().execute(
        "SELECT id FROM central_catalog WHERE level_required=1 ORDER BY id").fetchall()]
    require(product_card_ids(ctx.bot.sent)==ids[:5],
            "Admin Level 1 page must show only the first five products")
    nav=next(x for x in ctx.bot.sent if "ccadminpage:1" in sent_callback_data(x))
    require("PAGE 1/3" in nav["kwargs"].get("text",""),
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


def test_owner_wallet_and_inventory_design():
    # Wallet screen: key totals and realised profit are clearly separated.
    update, message, query = callback_update(101, "m:wallet")
    asyncio.run(v5.menu_callback(update, context()))
    wallet = message.replies[0]
    require("<b>👛 USDT WALLET</b>" in wallet["text"], "Wallet heading is missing")
    require("Available balance: <b>7.5" in wallet["text"], "Wallet balance is missing")
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
                 VALUES(1,101,'resale_purchase_debit',1000,'USDT','isolated test spend',?)""", (v4.now(),))
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
    asyncio.run(v4.payment_review(reject_update,context()))
    c=v4.db()
    rejected=c.execute("SELECT status,admin_note FROM payment_requests WHERE id=?",(rejected_id,)).fetchone()
    reject_credit=c.execute("SELECT COUNT(*) FROM ledger WHERE owner_id=101 AND entry_type='deposit_credit' AND note LIKE ?",(f"%#{rejected_id}%",)).fetchone()[0]
    c.close()
    require(rejected["status"]=="rejected" and rejected["admin_note"]=="This is fake screenshot",
            "Rejected deposit did not leave Pending with the rejection reason")
    require(reject_credit==0,"Rejected deposit incorrectly credited the wallet")

    # Level activation goes directly to screenshot upload; it must not use the wallet-deposit confirmation button.
    c=v4.db()
    cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
                     VALUES(202,2,'level_activation',2,10.0,'USDT','not_selected','pending',?)""",(v4.now(),))
    level_rid=cur.lastrowid
    c.commit(); c.close()
    level_ctx=context()
    level_update,level_message,_=callback_update(202,f"net:{level_rid}:trc")
    asyncio.run(v5.network_callback(level_update,level_ctx))
    level_buttons=callback_data(level_message.replies[0])
    require(level_ctx.user_data.get("state")=="payment_proof",
            "Level activation network selection did not proceed directly to payment proof")
    require("m:depositdone" not in level_buttons,
            "Level activation incorrectly displayed the wallet-deposit confirmation button")
    require("USDT TRC20" in level_message.replies[0]["text"],
            "Level activation network screen did not show the selected network")
    print("PASS: Deposit Center, transaction hash, approval/rejection lifecycle and level activation network flow")


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
    asyncio.run(v4.withdrawal_review(reject_update,context()))
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
    require("Wallet shortfall: 69.400000 USDT" in screen,
            f"Merchant wallet did not show the net wallet shortfall; screen was: {screen!r}")
    require("Sale proceeds remaining: 19.300000 USDT" in screen,
            "Merchant wallet did not show 169.3 minus 150 = 19.3")
    require("Available to withdraw: <b>0.000000 USDT</b>" in screen,
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
                        "resale_listings"}
            require(required.issubset(names),
                    f"Fresh install missing tables: {sorted(required - names)}")
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


def main():
    test_runtime_schema_bootstrap()
    fixture()
    test_level1_catalogue_quantity_buttons()
    test_admin_catalogue_pagination()
    pid, listing_id, inventory_id = test_central_purchase_and_auto_listing()
    test_owner_wallet_and_inventory_design()
    test_list_stock_profit_screen()
    test_seller_market_screen(listing_id)
    test_resale_buyer_guards_and_sale(listing_id, inventory_id)
    test_admin_resale_flow(listing_id, inventory_id)
    test_merchant_deposit_screenshot_and_admin_approval()
    test_usdt_trc20_withdrawal_flow()
    test_failed_central_purchases(pid)
    test_admin_refund_safety()
    test_usdt_balance_reconciliation()
    print("PASS: All isolated Telegram-handler end-to-end tests completed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        TMP.cleanup()
