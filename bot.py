import os
import sqlite3
import hashlib
import secrets
from datetime import datetime, timezone

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "shop_in_india.db")

if not TOKEN:
    raise RuntimeError("Set BOT_TOKEN in your hosting environment.")
if not ADMIN_ID:
    raise RuntimeError("Set ADMIN_ID to your own numeric Telegram user ID.")

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL, shop_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL, title TEXT NOT NULL,
            description TEXT NOT NULL, price REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS money_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, kind TEXT NOT NULL,
            amount REAL NOT NULL, currency TEXT NOT NULL,
            note TEXT, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        """)
        con.commit()

def now():
    return datetime.now(timezone.utc).isoformat()

def main_keyboard():
    return ReplyKeyboardMarkup([
        ["🏪 Register / Login Shop", "📦 Supplier Account"],
        ["🛒 Buy Products", "📤 Sell Products"],
        ["💰 Wallet", "📊 My Dashboard"],
        ["🚚 Orders & Shipping", "🎧 Customer Support"],
        ["🌐 Language", "ℹ️ Help"]
    ], resize_keyboard=True)

def shop_keyboard():
    return ReplyKeyboardMarkup([
        ["🏪 Shop details", "➕ Add product"],
        ["📦 My products", "📊 My orders"],
        ["⬅️ Back"]
    ], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as con:
        con.execute("INSERT OR REPLACE INTO users(tg_id, username, first_name, created_at) VALUES(?,?,?,?)",
                    (u.id, u.username or "", u.first_name or "", now()))
        con.commit()
    context.user_data.clear()
    await update.message.reply_text(
        "🛍️ Welcome to Shop in India\\n\\nWorldwide marketplace demo. Choose an option:",
        reply_markup=main_keyboard()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use the menu buttons. Shop registrations and money requests require owner approval. "
        "This starter does not process real payments or arrange shipping."
    )

async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with db() as con:
        shops = con.execute("SELECT id, owner_id, shop_name FROM shops WHERE status='pending'").fetchall()
        money = con.execute("SELECT id, user_id, kind, amount, currency FROM money_requests WHERE status='pending'").fetchall()
    lines = ["PENDING SHOP REQUESTS:"]
    lines += [f"Shop #{r['id']} | owner {r['owner_id']} | {r['shop_name']} — /approve_shop_{r['id']}" for r in shops]
    lines += ["", "PENDING MONEY REQUESTS:"]
    lines += [f"Request #{r['id']} | user {r['user_id']} | {r['kind']} {r['amount']} {r['currency']} — /approve_money_{r['id']}" for r in money]
    await update.message.reply_text("\\n".join(lines) if len(lines) > 2 else "No pending requests.")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    cmd = update.message.text or ""
    try:
        if cmd.startswith("/approve_shop_"):
            rid = int(cmd.rsplit("_", 1)[1])
            with db() as con:
                con.execute("UPDATE shops SET status='open' WHERE id=? AND status='pending'", (rid,))
                row = con.execute("SELECT owner_id, shop_name FROM shops WHERE id=?", (rid,)).fetchone()
                con.commit()
            if row:
                await context.bot.send_message(row["owner_id"], f"✅ Your shop '{row['shop_name']}' is approved and open.")
            await update.message.reply_text("Shop approval processed.")
        elif cmd.startswith("/approve_money_"):
            rid = int(cmd.rsplit("_", 1)[1])
            with db() as con:
                con.execute("UPDATE money_requests SET status='reviewed' WHERE id=? AND status='pending'", (rid,))
                con.commit()
            await update.message.reply_text("Request marked reviewed. Verify payment manually before crediting any balance.")
    except Exception:
        await update.message.reply_text("Could not process that approval command.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (update.message.text or "").strip()
    uid = update.effective_user.id
    state = context.user_data.get("state")

    if state == "shop_name":
        with db() as con:
            cur = con.execute("INSERT INTO shops(owner_id, shop_name, status, created_at) VALUES(?,?,?,?)",
                              (uid, msg[:80], "pending", now()))
            shop_id = cur.lastrowid
            con.commit()
        context.user_data.clear()
        await update.message.reply_text(
            f"⏳ Shop request submitted. Shop ID: {shop_id}. The owner must approve it before use.",
            reply_markup=main_keyboard()
        )
        try:
            await context.bot.send_message(ADMIN_ID, f"New shop approval request #{shop_id}\\nOwner: {uid}\\nShop: {msg[:80]}\\nUse /pending")
        except Exception:
            pass
        return

    if state == "product_title":
        context.user_data["product_title"] = msg[:100]
        context.user_data["state"] = "product_description"
        await update.message.reply_text("Send a short product description:")
        return
    if state == "product_description":
        context.user_data["product_description"] = msg[:500]
        context.user_data["state"] = "product_price"
        await update.message.reply_text("Send product price as a number (USD):")
        return
    if state == "product_price":
        try:
            price = float(msg)
            if price <= 0: raise ValueError()
        except ValueError:
            await update.message.reply_text("Please enter a valid amount greater than zero.")
            return
        with db() as con:
            shop = con.execute("SELECT id FROM shops WHERE owner_id=? AND status='open' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
            if not shop:
                context.user_data.clear()
                await update.message.reply_text("You need an approved shop first.", reply_markup=main_keyboard())
                return
            con.execute("INSERT INTO products(shop_id,title,description,price,currency) VALUES(?,?,?,?,?)",
                        (shop["id"], context.user_data["product_title"], context.user_data["product_description"], price, "USD"))
            con.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Product listed.", reply_markup=shop_keyboard())
        return

    if state == "money_amount":
        try:
            amount = float(msg)
            if amount <= 0: raise ValueError()
        except ValueError:
            await update.message.reply_text("Enter a valid amount greater than zero.")
            return
        kind = context.user_data.get("money_kind", "deposit")
        with db() as con:
            con.execute("INSERT INTO money_requests(user_id,kind,amount,currency,note,created_at) VALUES(?,?,?,?,?,?)",
                        (uid, kind, amount, "USD", "Manual review only", now()))
            con.commit()
        context.user_data.clear()
        await update.message.reply_text("Your request was submitted for manual review. No funds have been moved.", reply_markup=main_keyboard())
        await context.bot.send_message(ADMIN_ID, f"Money request: user {uid}, {kind} {amount} USD. Check /pending")
        return

    if msg == "🏪 Register / Login Shop":
        await update.message.reply_text("Choose: register a shop by sending its name, or use /myshops to view your shops.")
        context.user_data["state"] = "shop_name"
        await update.message.reply_text("To register, enter your proposed shop name:")
    elif msg == "📦 Supplier Account":
        await update.message.reply_text("Supplier onboarding is not enabled yet. It will be added after supplier verification.")
    elif msg == "🛒 Buy Products":
        with db() as con:
            rows = con.execute("SELECT p.id,p.title,p.description,p.price,p.currency,s.shop_name FROM products p JOIN shops s ON s.id=p.shop_id WHERE p.active=1 AND s.status='open' ORDER BY p.id DESC LIMIT 20").fetchall()
        if not rows:
            await update.message.reply_text("No approved products are listed yet.", reply_markup=main_keyboard())
        else:
            text = "\\n\\n".join([f"#{r['id']} — {r['title']}\\n{r['description']}\\n{r['price']:.2f} {r['currency']} | Shop: {r['shop_name']}\\nOrder: /order_{r['id']}" for r in rows])
            await update.message.reply_text(text)
    elif msg == "📤 Sell Products":
        await update.message.reply_text("To sell products, register a shop and wait for owner approval.", reply_markup=main_keyboard())
    elif msg == "💰 Wallet":
        await update.message.reply_text("Wallet requests are manual-review placeholders. Choose /deposit or /withdraw. No real payments are processed.")
    elif msg == "📊 My Dashboard":
        with db() as con:
            shops = con.execute("SELECT id,shop_name,status FROM shops WHERE owner_id=?", (uid,)).fetchall()
        if shops:
            await update.message.reply_text("\\n".join([f"🏪 {r['shop_name']} | ID {r['id']} | {r['status']}" for r in shops]), reply_markup=shop_keyboard())
        else:
            await update.message.reply_text("You do not have a shop yet.", reply_markup=main_keyboard())
    elif msg == "🚚 Orders & Shipping":
        await update.message.reply_text("Order tracking and carrier integrations are not enabled yet.")
    elif msg == "🎧 Customer Support":
        await update.message.reply_text("For support, contact the platform owner through the bot administrator.")
    elif msg == "🌐 Language":
        await update.message.reply_text("Language selection will be added in the next version. Current language: English.")
    elif msg == "ℹ️ Help" or msg == "/help":
        await help_cmd(update, context)
    elif msg == "⬅️ Back":
        context.user_data.clear()
        await update.message.reply_text("Main menu:", reply_markup=main_keyboard())
    elif msg == "🏪 Shop details":
        with db() as con:
            r = con.execute("SELECT id,shop_name,status,created_at FROM shops WHERE owner_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        await update.message.reply_text(f"Shop: {r['shop_name']}\\nID: {r['id']}\\nStatus: {r['status']}\\nCreated: {r['created_at']}" if r else "No shop found.")
    elif msg == "➕ Add product":
        with db() as con:
            r = con.execute("SELECT id FROM shops WHERE owner_id=? AND status='open' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        if not r:
            await update.message.reply_text("You need an approved shop first.")
        else:
            context.user_data["state"] = "product_title"
            await update.message.reply_text("Send product title:")
    elif msg == "📦 My products":
        with db() as con:
            rows = con.execute("SELECT p.title,p.price,p.currency FROM products p JOIN shops s ON s.id=p.shop_id WHERE s.owner_id=? ORDER BY p.id DESC", (uid,)).fetchall()
        await update.message.reply_text("\\n".join([f"{r['title']} — {r['price']:.2f} {r['currency']}" for r in rows]) or "No products yet.")
    elif msg == "📊 My orders":
        with db() as con:
            rows = con.execute("SELECT id,status,quantity FROM orders WHERE buyer_id=? ORDER BY id DESC LIMIT 20", (uid,)).fetchall()
        await update.message.reply_text("\\n".join([f"Order #{r['id']} | {r['status']} | Qty {r['quantity']}" for r in rows]) or "No orders yet.")
    else:
        await update.message.reply_text("Please choose an option from the menu.", reply_markup=main_keyboard())

async def myshops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as con:
        rows = con.execute("SELECT id,shop_name,status FROM shops WHERE owner_id=?", (uid,)).fetchall()
    await update.message.reply_text("\\n".join([f"#{r['id']} {r['shop_name']} — {r['status']}" for r in rows]) or "No shops found.")

async def money_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = update.message.text.lstrip("/").split("@")[0]
    context.user_data["money_kind"] = kind
    context.user_data["state"] = "money_amount"
    await update.message.reply_text(f"Enter {kind} amount in USD. This only creates a manual review request; it does not transfer money.")

async def order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(update.message.text.rsplit("_",1)[1])
    except Exception:
        return
    with db() as con:
        p = con.execute("SELECT id,title FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            await update.message.reply_text("Product not found.")
            return
        cur = con.execute("INSERT INTO orders(buyer_id,product_id,quantity,status,created_at) VALUES(?,?,?,?,?)",
                          (update.effective_user.id,pid,1,"pending",now()))
        oid = cur.lastrowid
        con.commit()
    await update.message.reply_text(f"✅ Order request #{oid} created for {p['title']}. The seller must confirm availability and shipping.")

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("myshops", myshops))
    app.add_handler(CommandHandler("deposit", money_command))
    app.add_handler(CommandHandler("withdraw", money_command))
    app.add_handler(MessageHandler(filters.Regex(r"^/approve_(shop|money)_\d+$"), approve))
    app.add_handler(MessageHandler(filters.Regex(r"^/order_\d+$"), order_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Shop in India bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
