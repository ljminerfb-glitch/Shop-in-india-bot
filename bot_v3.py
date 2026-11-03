import os
import re
import hmac
import hashlib
import secrets
import sqlite3
import logging
from datetime import datetime, timezone

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB = "shop_in_india.db"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

MENU = [
    ["🏪 Register Shop", "🔐 Login"],
    ["👤 My Profile", "💳 Withdrawal Wallet"],
    ["🛍️ Marketplace", "📝 Suggest Product"],
    ["📋 My Suggestions", "💬 Support"]
]

def now():
    return datetime.now(timezone.utc).isoformat()

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, 200000
    )
    return salt.hex() + "$" + digest.hex()

def verify_password(password, stored):
    try:
        salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), 200000
        )
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False

def setup():
    con = connect()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL,
            role TEXT DEFAULT 'shop_owner',
            status TEXT DEFAULT 'pending',
            language TEXT DEFAULT 'en'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            shop_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            password_hash TEXT,
            level INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Add missing columns without deleting existing records.
    shop_columns = [r["name"] for r in cur.execute(
        "PRAGMA table_info(shops)"
    ).fetchall()]

    if "password_hash" not in shop_columns:
        cur.execute("ALTER TABLE shops ADD COLUMN password_hash TEXT")
    if "level" not in shop_columns:
        cur.execute("ALTER TABLE shops ADD COLUMN level INTEGER NOT NULL DEFAULT 0")

    user_columns = [r["name"] for r in cur.execute(
        "PRAGMA table_info(users)"
    ).fetchall()]

    for name, definition in [
        ("role", "TEXT DEFAULT 'shop_owner'"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("language", "TEXT DEFAULT 'en'")
    ]:
        if name not in user_columns:
            cur.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS owner_wallets (
            owner_id INTEGER PRIMARY KEY,
            network TEXT NOT NULL,
            address TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0
        )
    """)

    wallet_columns = [r["name"] for r in cur.execute(
        "PRAGMA table_info(owner_wallets)"
    ).fetchall()]
    if "verified" not in wallet_columns:
        cur.execute(
            "ALTER TABLE owner_wallets ADD COLUMN verified INTEGER NOT NULL DEFAULT 0"
        )

    # Enforce case-insensitive unique shop names.
    duplicates = cur.execute("""
        SELECT LOWER(shop_name), COUNT(*) AS total
        FROM shops GROUP BY LOWER(shop_name)
        HAVING COUNT(*) > 1
    """).fetchall()

    if not duplicates:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS unique_shop_name
            ON shops(LOWER(shop_name))
        """)
    else:
        log.warning("Duplicate old shop names found; unique index not added yet.")

    con.commit()
    con.close()

def get_shop(owner_id):
    con = connect()
    row = con.execute("""
        SELECT * FROM shops WHERE owner_id=?
        ORDER BY id DESC LIMIT 1
    """, (owner_id,)).fetchone()
    con.close()
    return row

def main_keyboard():
    return ReplyKeyboardMarkup(MENU, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🌍 SHOP IN INDIA\n\nWelcome! Select an option:",
        reply_markup=main_keyboard()
    )

async def register_start(update, context):
    existing = get_shop(update.effective_user.id)
    if existing:
        await update.message.reply_text(
            f"You already have a shop application.\n"
            f"Shop: {existing['shop_name']}\n"
            f"Status: {existing['status']}"
        )
        return

    context.user_data.clear()
    context.user_data["state"] = "shop_name"
    await update.message.reply_text("🏪 Enter your unique shop name:")

async def save_shop_name(update, context):
    name = update.message.text.strip()

    if len(name) < 2 or len(name) > 80:
        await update.message.reply_text("Shop name must be 2–80 characters.")
        return

    con = connect()
    exists = con.execute(
        "SELECT id FROM shops WHERE LOWER(shop_name)=LOWER(?)",
        (name,)
    ).fetchone()
    con.close()

    if exists:
        await update.message.reply_text(
            "❌ This shop name is already registered. Please choose another."
        )
        return

    context.user_data["new_shop_name"] = name
    context.user_data["state"] = "shop_password"
    await update.message.reply_text(
        "🔐 Create a password for your shop.\n"
        "Minimum 8 characters. Do not share it with anyone."
    )

async def save_shop_password(update, context):
    password = update.message.text.strip()

    if len(password) < 8 or len(password) > 128:
        await update.message.reply_text(
            "Password must be between 8 and 128 characters. Try again."
        )
        return

    user = update.effective_user
    name = context.user_data.get("new_shop_name")

    if not name:
        context.user_data.clear()
        await update.message.reply_text("Please start registration again.")
        return

    con = connect()
    cur = con.cursor()

    try:
        cur.execute("""
            INSERT INTO users
            (tg_id, username, first_name, created_at, role, status)
            VALUES (?, ?, ?, ?, 'shop_owner', 'pending')
            ON CONFLICT(tg_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
        """, (user.id, user.username, user.first_name, now()))

        cur.execute("""
            INSERT INTO shops
            (owner_id, shop_name, status, created_at, password_hash)
            VALUES (?, ?, 'pending', ?, ?)
        """, (user.id, name, now(), hash_password(password)))

        shop_id = cur.lastrowid
        con.commit()

    except sqlite3.IntegrityError:
        con.rollback()
        con.close()
        context.user_data.clear()
        await update.message.reply_text(
            "❌ This shop name is already registered. Please register with another name."
        )
        return

    con.close()
    context.user_data.clear()

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{shop_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject:{shop_id}")
    ]])

    admin_sent = False
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🏪 NEW SHOP APPLICATION\n\n"
            f"Shop: {name}\nOwner: {user.first_name}\n"
            f"Username: @{user.username or 'Not set'}\n"
            f"Telegram ID: {user.id}\nShop ID: {shop_id}",
            reply_markup=keyboard
        )
        admin_sent = True
    except Exception:
        log.exception("Admin notification failed")

    message = "Your application is waiting for Admin approval."
    if not admin_sent:
        message += "\nPlease contact support: Admin notification could not be delivered."

    await update.message.reply_text(
        f"✅ Registration submitted!\nShop ID: {shop_id}\n{message}",
        reply_markup=main_keyboard()
    )

async def login_start(update, context):
    shop = get_shop(update.effective_user.id)

    if not shop:
        await update.message.reply_text("Please register your shop first.")
        return

    context.user_data["state"] = "login_password"
    await update.message.reply_text("Enter your shop password:")

async def login_password(update, context):
    password = update.message.text.strip()
    shop = get_shop(update.effective_user.id)
    context.user_data.clear()

    if not shop or not shop["password_hash"]:
        await update.message.reply_text("No shop password found. Contact support.")
        return

    if not verify_password(password, shop["password_hash"]):
        await update.message.reply_text("❌ Incorrect password.")
        return

    if shop["status"] != "approved":
        await update.message.reply_text(
            f"⏳ Your shop status is: {shop['status']}.\n"
            "Admin approval is required before marketplace access."
        )
        return

    context.user_data["logged_in"] = True
    context.user_data["shop_id"] = shop["id"]

    await update.message.reply_text(
        f"🔓 Login successful!\n\n"
        f"Shop: {shop['shop_name']}\n"
        f"Shop ID: {shop['id']}\nLevel: {shop['level']}",
        reply_markup=main_keyboard()
    )

async def approval(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ Admin access only.")
        return

    action, shop_id_text = query.data.split(":")
    shop_id = int(shop_id_text)
    status = "approved" if action == "approve" else "rejected"

    con = connect()
    cur = con.cursor()
    shop = cur.execute(
        "SELECT owner_id, shop_name FROM shops WHERE id=?", (shop_id,)
    ).fetchone()

    if not shop:
        con.close()
        await query.edit_message_text("Shop not found.")
        return

    cur.execute("UPDATE shops SET status=? WHERE id=?", (status, shop_id))

    if status == "approved":
        cur.execute(
            "UPDATE users SET status='approved', role='shop_owner' WHERE tg_id=?",
            (shop["owner_id"],)
        )

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
    cur.execute("""
        INSERT INTO admin_audit
        (admin_id, owner_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (ADMIN_ID, shop["owner_id"], status, shop["shop_name"], now()))

    con.commit()
    con.close()

    await query.edit_message_text(f"Shop {status.upper()}.")

    try:
        await context.bot.send_message(
            shop["owner_id"],
            f"📢 Your shop application has been {status.upper()}."
        )
    except Exception:
        log.exception("Owner notification failed")

async def wallet_start(update, context):
    shop = get_shop(update.effective_user.id)

    if not shop or shop["status"] != "approved":
        await update.message.reply_text("Approved shop owner access required.")
        return

    con = connect()
    wallet = con.execute(
        "SELECT * FROM owner_wallets WHERE owner_id=?",
        (update.effective_user.id,)
    ).fetchone()
    con.close()

    if wallet:
        await update.message.reply_text(
            f"💳 YOUR WITHDRAWAL WALLET\n\n"
            f"Network: {wallet['network']}\n"
            f"Verification: {'Verified' if wallet['verified'] else 'Pending Verification'}\n\n"
            "Your full wallet address is hidden for security. "
            "To change it, select the network below."
        )
    else:
        await update.message.reply_text("No withdrawal wallet saved yet.")

    context.user_data["state"] = "wallet_network"
    await update.message.reply_text(
        "Choose your withdrawal network:",
        reply_markup=ReplyKeyboardMarkup(
            [["USDT TRC20", "USDT BEP20"], ["Cancel"]],
            resize_keyboard=True
        )
    )

async def save_wallet_network(update, context):
    network = update.message.text.strip()

    if network == "Cancel":
        context.user_data.clear()
        await update.message.reply_text("Cancelled.", reply_markup=main_keyboard())
        return

    if network not in ("USDT TRC20", "USDT BEP20"):
        await update.message.reply_text("Please select USDT TRC20 or USDT BEP20.")
        return

    context.user_data["wallet_network"] = network
    context.user_data["state"] = "wallet_address"
    await update.message.reply_text("Now enter your wallet address:")

async def save_wallet_address(update, context):
    address = update.message.text.strip()
    network = context.user_data.get("wallet_network")
    owner_id = update.effective_user.id

    if not network:
        context.user_data.clear()
        await update.message.reply_text("Please start wallet setup again.")
        return

    if network == "USDT TRC20":
        valid = bool(re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", address))
    else:
        valid = bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", address))

    if not valid:
        await update.message.reply_text(
            "❌ Address format does not match the selected network. Please check it and try again."
        )
        return

    con = connect()
    con.execute("""
        INSERT INTO owner_wallets (owner_id, network, address, updated_at, verified)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(owner_id) DO UPDATE SET
        network=excluded.network,
        address=excluded.address,
        updated_at=excluded.updated_at,
        verified=0
    """, (owner_id, network, address, now()))

    con.commit()
    con.close()
    context.user_data.clear()

    # Admin can see the saved address; owner sees only saved status.
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"💳 OWNER WITHDRAWAL WALLET SAVED/UPDATED\n\n"
            f"Owner Telegram ID: {owner_id}\n"
            f"Shop: {get_shop(owner_id)['shop_name']}\n"
            f"Network: {network}\nAddress: {address}\n"
            "Status: Saved by owner; not independently verified."
        )
    except Exception:
        log.exception("Admin wallet notification failed")

    await update.message.reply_text(
        f"✅ Withdrawal wallet saved.\n"
        f"Network: {network}\n"
        "Your profile now shows that this address is saved. "
        "It is not yet verified for payment.",
        reply_markup=main_keyboard()
    )

async def profile(update, context):
    owner_id = update.effective_user.id
    shop = get_shop(owner_id)

    if not shop:
        await update.message.reply_text("No shop profile found. Register first.")
        return

    con = connect()
    wallet = con.execute(
        "SELECT network, verified FROM owner_wallets WHERE owner_id=?", (owner_id,)
    ).fetchone()
    con.close()

    wallet_status = (
        f"{wallet['network']} — {'Verified' if wallet['verified'] else 'Pending Verification'}"
        if wallet else "Not set"
    )

    await update.message.reply_text(
        f"👤 SHOP OWNER PROFILE\n\n"
        f"Shop ID: {shop['id']}\n"
        f"Shop name: {shop['shop_name']}\n"
        f"Status: {shop['status']}\n"
        f"Level: {shop['level']}\n"
        f"Withdrawal wallet: {wallet_status}",
        reply_markup=main_keyboard()
    )

async def verify_wallet(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin access only.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /verifywallet OWNER_ID\nExample: /verifywallet 123456789"
        )
        return

    owner_id = int(context.args[0])
    con = connect()
    wallet = con.execute(
        "SELECT network, address FROM owner_wallets WHERE owner_id=?",
        (owner_id,)
    ).fetchone()

    if not wallet:
        con.close()
        await update.message.reply_text("No saved wallet found for this owner.")
        return

    con.execute(
        "UPDATE owner_wallets SET verified=1 WHERE owner_id=?",
        (owner_id,)
    )
    con.commit()
    con.close()

    await update.message.reply_text(
        f"Wallet verified successfully.\nOwner ID: {owner_id}\n"
        f"Network: {wallet['network']}\nAddress: {wallet['address']}"
    )

    try:
        await context.bot.send_message(
            owner_id,
            "✅ Your withdrawal wallet has been verified by Admin. "
            "Your profile now shows: Verified."
        )
    except Exception:
        await update.message.reply_text(
            "Verification saved, but Telegram could not notify the owner. "
            "The owner may need to start the bot first."
        )


async def admin(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin access only.")
        return

    con = connect()
    shops = con.execute("""
        SELECT s.id, s.shop_name, s.owner_id, s.status,
               w.network, w.address, w.verified
        FROM shops s
        LEFT JOIN owner_wallets w ON w.owner_id=s.owner_id
        ORDER BY s.id DESC LIMIT 30
    """).fetchall()
    con.close()

    lines = ["🛡️ ADMIN DASHBOARD", ""]

    if not shops:
        lines.append("No shops registered.")
    for s in shops:
        lines.append(
            f"Shop #{s['id']} — {s['shop_name']}\n"
            f"Owner ID: {s['owner_id']}\nStatus: {s['status']}\n"
            f"Withdrawal wallet: {s['network'] or 'Not set'}\n"
            f"Address: {s['address'] or 'Not set'}\n"
            f"Wallet verification: {'Verified' if s['verified'] else 'Pending' if s['address'] else 'Not set'}\n"
        )

    await update.message.reply_text("\n".join(lines))


def ensure_catalog_schema():
    con = connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS product_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            shop_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            suggested_price REAL NOT NULL,
            final_price REAL,
            stock INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        )
    """)
    cols = [r["name"] for r in con.execute(
        "PRAGMA table_info(products)"
    ).fetchall()]
    if "stock" not in cols:
        con.execute(
            "ALTER TABLE products ADD COLUMN stock INTEGER NOT NULL DEFAULT 1"
        )
    con.commit()
    con.close()


async def suggest_product_start(update, context):
    shop = get_shop(update.effective_user.id)
    if not shop or shop["status"] != "approved":
        await update.message.reply_text("Admin-approved shop required.")
        return
    ensure_catalog_schema()
    context.user_data["state"] = "suggest_title"
    await update.message.reply_text(
        "📝 Enter the product name for Admin approval. Type Cancel to stop."
    )


async def save_suggestion_title(update, context):
    value = update.message.text.strip()
    if value.lower() == "cancel":
        context.user_data.clear()
        await update.message.reply_text("Cancelled.", reply_markup=main_keyboard())
        return
    if not 2 <= len(value) <= 100:
        await update.message.reply_text("Name must be 2–100 characters.")
        return
    context.user_data["suggest_title"] = value
    context.user_data["state"] = "suggest_description"
    await update.message.reply_text("Enter product description:")


async def save_suggestion_description(update, context):
    value = update.message.text.strip()
    if value.lower() == "cancel":
        context.user_data.clear()
        await update.message.reply_text("Cancelled.", reply_markup=main_keyboard())
        return
    if not value or len(value) > 1000:
        await update.message.reply_text("Description must be 1–1000 characters.")
        return
    context.user_data["suggest_description"] = value
    context.user_data["state"] = "suggest_price"
    await update.message.reply_text(
        "Enter your suggested price in INR. Admin will decide the final price:"
    )


async def save_suggestion_price(update, context):
    raw = update.message.text.strip()
    if raw.lower() == "cancel":
        context.user_data.clear()
        await update.message.reply_text("Cancelled.", reply_markup=main_keyboard())
        return
    try:
        price = float(raw.replace(",", ""))
        if not 0 < price <= 100000000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Enter a valid INR price, or type Cancel.")
        return

    shop = get_shop(update.effective_user.id)
    if not shop or shop["status"] != "approved":
        context.user_data.clear()
        await update.message.reply_text("Approved shop required.")
        return

    ensure_catalog_schema()
    con = connect()
    cur = con.execute("""
        INSERT INTO product_suggestions
        (owner_id, shop_id, title, description, suggested_price, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (
        update.effective_user.id, shop["id"],
        context.user_data["suggest_title"],
        context.user_data["suggest_description"],
        price, now()
    ))
    suggestion_id = cur.lastrowid
    con.commit()
    con.close()

    title = context.user_data["suggest_title"]
    description = context.user_data["suggest_description"]
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ Product suggestion submitted!\n"
        f"Suggestion ID: #{suggestion_id}\n"
        f"Product: {title}\n"
        f"Suggested price: ₹{price:,.2f}\n"
        "Status: Pending Admin approval.",
        reply_markup=main_keyboard()
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"product:approve:{suggestion_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"product:reject:{suggestion_id}")
    ]])
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📝 NEW PRODUCT SUGGESTION #{suggestion_id}\n\n"
            f"Shop: {shop['shop_name']}\nOwner ID: {update.effective_user.id}\n"
            f"Product: {title}\n"
            f"Description: {description}\n"
            f"Suggested price: ₹{price:,.2f}\n\n"
            "Approve or reject this suggestion:",
            reply_markup=keyboard
        )
    except Exception:
        log.exception("Could not notify admin about product suggestion")
        await update.message.reply_text(
            "Suggestion saved, but Admin notification failed. Please contact support."
        )


async def product_review(update, context):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("Admin access only.")
        return

    ensure_catalog_schema()
    _, action, sid_text = query.data.split(":")
    sid = int(sid_text)
    con = connect()
    item = con.execute(
        "SELECT * FROM product_suggestions WHERE id=?", (sid,)
    ).fetchone()

    if not item or item["status"] != "pending":
        con.close()
        await query.edit_message_text("Suggestion not found or already reviewed.")
        return

    if action == "reject":
        con.execute(
            "UPDATE product_suggestions SET status='rejected', reviewed_at=? WHERE id=?",
            (now(), sid)
        )
        con.commit()
        con.close()
        await query.edit_message_text(f"Suggestion #{sid} rejected.")
        try:
            await context.bot.send_message(
                item["owner_id"],
                f"❌ Your product suggestion #{sid} was rejected by Admin."
            )
        except Exception:
            log.exception("Owner rejection notification failed")
        return

    con.execute(
        "UPDATE product_suggestions SET status='pricing' WHERE id=?", (sid,)
    )
    con.commit()
    con.close()
    context.user_data["admin_product_id"] = sid
    context.user_data["state"] = "admin_product_price"
    await query.edit_message_text(
        f"Suggestion #{sid} approved for listing.\n"
        "Now enter the FINAL selling price in INR. This price will be controlled by Admin."
    )


async def admin_product_price(update, context):
    if update.effective_user.id != ADMIN_ID:
        context.user_data.pop("state", None)
        return
    raw = update.message.text.strip()
    try:
        price = float(raw.replace(",", ""))
        if not 0 < price <= 100000000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Enter a valid final price in INR.")
        return
    context.user_data["admin_final_price"] = price
    context.user_data["state"] = "admin_product_stock"
    await update.message.reply_text("Now enter the available stock quantity (whole number):")


async def admin_product_stock(update, context):
    if update.effective_user.id != ADMIN_ID:
        context.user_data.pop("state", None)
        return
    try:
        stock = int(update.message.text.strip())
        if not 1 <= stock <= 1000000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Enter a whole stock quantity from 1 to 1,000,000.")
        return

    sid = context.user_data.get("admin_product_id")
    price = context.user_data.get("admin_final_price")
    ensure_catalog_schema()
    con = connect()
    item = con.execute(
        "SELECT * FROM product_suggestions WHERE id=? AND status='pricing'", (sid,)
    ).fetchone()
    if not item:
        con.close()
        context.user_data.clear()
        await update.message.reply_text("Suggestion is no longer awaiting pricing.")
        return

    cur = con.execute("""
        INSERT INTO products (shop_id, title, description, price, currency, active, stock)
        VALUES (?, ?, ?, ?, 'INR', 1, ?)
    """, (item["shop_id"], item["title"], item["description"], price, stock))
    product_id = cur.lastrowid
    con.execute("""
        UPDATE product_suggestions
        SET status='approved', final_price=?, stock=?, reviewed_at=?
        WHERE id=?
    """, (price, stock, now(), sid))
    con.commit()
    con.close()
    context.user_data.clear()

    await update.message.reply_text(
        f"✅ Product published.\nProduct ID: #{product_id}\n"
        f"Final price: ₹{price:,.2f}\nStock: {stock}"
    )
    try:
        await context.bot.send_message(
            item["owner_id"],
            f"✅ Your product suggestion #{sid} was approved and listed.\n"
            f"Product: {item['title']}\n"
            f"Admin final price: ₹{price:,.2f}\nStock: {stock}"
        )
    except Exception:
        log.exception("Owner approval notification failed")


async def my_suggestions(update, context):
    ensure_catalog_schema()
    con = connect()
    rows = con.execute("""
        SELECT id, title, status, final_price
        FROM product_suggestions WHERE owner_id=?
        ORDER BY id DESC LIMIT 20
    """, (update.effective_user.id,)).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("You have no product suggestions yet.")
        return
    lines = ["📋 YOUR PRODUCT SUGGESTIONS", ""]
    for row in rows:
        line = f"#{row['id']} — {row['title']}\nStatus: {row['status'].title()}"
        if row["final_price"] is not None:
            line += f"\nFinal price: ₹{row['final_price']:,.2f}"
        lines.append(line + "\n")
    await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())


async def handle_message(update, context):
    if not update.message or not update.message.text:
        return

    msg = update.message.text.strip()
    state = context.user_data.get("state")

    if state == "suggest_title":
        await save_suggestion_title(update, context)
    elif state == "suggest_description":
        await save_suggestion_description(update, context)
    elif state == "suggest_price":
        await save_suggestion_price(update, context)
    elif state == "admin_product_price":
        await admin_product_price(update, context)
    elif state == "admin_product_stock":
        await admin_product_stock(update, context)
    elif state == "shop_name":
        await save_shop_name(update, context)
    elif state == "shop_password":
        await save_shop_password(update, context)
    elif state == "login_password":
        await login_password(update, context)
    elif state == "wallet_network":
        await save_wallet_network(update, context)
    elif state == "wallet_address":
        await save_wallet_address(update, context)
    elif msg == "🏪 Register Shop":
        await register_start(update, context)
    elif msg == "🔐 Login":
        await login_start(update, context)
    elif msg == "👤 My Profile":
        await profile(update, context)
    elif msg == "💳 Withdrawal Wallet":
        await wallet_start(update, context)
    elif msg == "📝 Suggest Product":
        await suggest_product_start(update, context)
    elif msg == "📋 My Suggestions":
        await my_suggestions(update, context)
    elif msg == "🛍️ Marketplace":
        shop = get_shop(update.effective_user.id)
        if shop and shop["status"] == "approved":
            ensure_catalog_schema()
            con = connect()
            rows = con.execute("""
                SELECT p.id, p.title, p.description, p.price, p.currency, p.stock,
                       s.shop_name
                FROM products p JOIN shops s ON s.id=p.shop_id
                WHERE p.active=1 AND p.stock>0
                ORDER BY p.id DESC LIMIT 20
            """).fetchall()
            con.close()
            if not rows:
                await update.message.reply_text("No approved products are listed yet.")
            else:
                lines = ["🛍️ MARKETPLACE", ""]
                for r in rows:
                    lines.append(
                        f"#{r['id']} — {r['title']}\nShop: {r['shop_name']}\n"
                        f"Price: {r['currency']} {r['price']:,.2f}\nStock: {r['stock']}\n"
                    )
                await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("Admin-approved shop required.")
    elif msg == "💬 Support":
        await update.message.reply_text("Please contact the administrator.")
    else:
        await update.message.reply_text(
            "Please choose an option from the menu.",
            reply_markup=main_keyboard()
        )

async def help_command(update, context):
    await update.message.reply_text(
        "/start — Main menu\n/admin — Admin dashboard\n/help — Help"
    )

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN is missing!")
    if not ADMIN_ID:
        raise ValueError("ADMIN_ID is missing!")

    setup()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("verifywallet", verify_wallet))
    app.add_handler(CallbackQueryHandler(product_review, pattern=r"^product:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(approval))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("SHOP IN INDIA V3 is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
