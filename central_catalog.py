"""Shared Level-1 central catalogue paid from owners' USDT wallet."""
from pathlib import Path
from datetime import datetime, timedelta, timezone, time as datetime_time
from zoneinfo import ZoneInfo
import logging
import html
import random
import bot_v4 as v4
import level2.products as level2_products
import mobile_phones.products as mobile_phone_products

log = logging.getLogger("central_catalog")

async def _safe_callback_answer(query, text=None, show_alert=False):
    """Ignore expired Telegram callback IDs without masking other failures."""
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception as exc:
        message=str(exc).lower()
        if "query is too old" in message or "query id is invalid" in message:
            log.info("Ignoring expired Telegram callback query")
            return
        raise

CATALOGUE_PAGE_SIZE=5
PRODUCTS = [
("oxidised_jhumka","Oxidised Jhumka Earrings","Traditional and stylish earrings for women, perfect for daily wear and parties.","images/01_jhumka.jpg"),
("mobile_back_covers","Mobile Back Covers","Attractive mobile covers available in different colours and designs.","images/02_mobile_cover.jpg"),
("hair_clips_scrunchies","Hair Clips & Scrunchies","Fashionable hair accessories for women and girls.","images/03_hair_clips.jpg"),
("decorative_wall_stickers","Decorative Wall Stickers","Beautiful self-adhesive stickers for home decoration.","images/04_wall_stickers.jpg"),
("microfiber_cleaning_cloth","Microfiber Cleaning Cloth","Reusable cleaning cloth for kitchens, furniture and vehicles.","images/05_cleaning_cloth.jpg"),
("mini_vegetable_chopper","Mini Vegetable Chopper","Compact kitchen tool for chopping vegetables, onions and garlic.","images/06_chopper.jpg"),
("lemon_squeezer","Lemon Squeezer","Useful kitchen accessory for extracting fresh lemon juice.","images/07_lemon_squeezer.jpg"),
("coconut_opener","Coconut Opener","Handy kitchen tool for opening tender coconuts.","images/08_coconut_opener.jpg"),
("digital_kitchen_scale","Digital Kitchen Scale","Electronic weighing scale for measuring food ingredients.","images/09_kitchen_scale.jpg"),
("garbage_bags","Garbage Bags","Disposable household garbage bags in different sizes.","images/10_garbage_bags.jpg"),
("printed_tshirts","Printed T-Shirts","Casual printed cotton T-shirts in different colours and designs.","images/11_tshirts.jpg"),
("kids_activity_books","Kids Activity Books","Educational books featuring drawing, colouring and puzzles.","images/12_activity_books.jpg"),
]

def setup(c=None):
    own = c is None
    conn = c or v4.db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS central_catalog (
          id INTEGER PRIMARY KEY AUTOINCREMENT, product_key TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL, description TEXT, price_usdt REAL NOT NULL DEFAULT 0,
          stock INTEGER NOT NULL DEFAULT 0, image_path TEXT, active INTEGER NOT NULL DEFAULT 1,
          level_required INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT)""")
        catalog_columns={row[1] for row in conn.execute("PRAGMA table_info(central_catalog)")}
        if "image_file_id" not in catalog_columns:
            conn.execute("ALTER TABLE central_catalog ADD COLUMN image_file_id TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id INTEGER, shop_id INTEGER NOT NULL,
          owner_id INTEGER NOT NULL, entry_type TEXT NOT NULL, amount REAL NOT NULL,
          currency TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL, owner_id INTEGER,
          action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS central_orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL,
          buyer_shop_id INTEGER NOT NULL, buyer_owner_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL, unit_price_usdt REAL NOT NULL,
          total_usdt REAL NOT NULL, status TEXT NOT NULL DEFAULT 'paid',
          created_at TEXT NOT NULL, FOREIGN KEY(catalog_id) REFERENCES central_catalog(id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS central_inventory (
          id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
          shop_id INTEGER NOT NULL, catalog_id INTEGER NOT NULL,
          order_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
          remaining_quantity INTEGER NOT NULL, unit_cost_usdt REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'owned', created_at TEXT NOT NULL,
          UNIQUE(order_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS catalogue_visits (
          owner_id INTEGER PRIMARY KEY, visits INTEGER NOT NULL DEFAULT 0,
          last_seen TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS merchant_level_item_limits (
          shop_id INTEGER NOT NULL, level INTEGER NOT NULL,
          item_limit INTEGER NOT NULL CHECK(item_limit >= 0),
          updated_at TEXT NOT NULL, updated_by INTEGER NOT NULL,
          PRIMARY KEY(shop_id, level))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS merchant_extra_item_allowances (
          shop_id INTEGER PRIMARY KEY, remaining_units INTEGER NOT NULL DEFAULT 0,
          total_approved_units INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)""")
        for key,title,desc,img in PRODUCTS:
            conn.execute("""INSERT OR IGNORE INTO central_catalog
              (product_key,title,description,price_usdt,stock,image_path,active,level_required,created_at,updated_at)
              VALUES(?,?,?,0,0,?,1,1,?,?)""",(key,title,desc,img,v4.now(),v4.now()))
        # Seed the original 50 Level 2 products first; their IDs/history remain unchanged.
        for item in level2_products.PRODUCTS:
            product_key=f"level2_{item['id']}"
            image_path=f"level2/{item['image_file']}"
            conn.execute("""INSERT OR IGNORE INTO central_catalog
              (product_key,title,description,price_usdt,stock,image_path,active,level_required,created_at,updated_at)
              VALUES(?,?,?,?,?,?,1,2,?,?)""",
              (product_key,item["name"],item["description"],float(item["price_usd"]),50,
               image_path,v4.now(),v4.now()))
        # Seed mobile variants strictly after the legacy 50. System approvals reveal
        # these in groups of ten; INSERT OR IGNORE preserves IDs, edits and stock.
        for item in mobile_phone_products.PRODUCTS:
            product_key=f"mobile_phone_{item['id']}"
            title=f"{item['brand']} {item['model']} — {item['ram']} / {item['storage']} — {item['color']}"
            image_path=f"mobile_phones/{item['image_file']}"
            conn.execute("""INSERT OR IGNORE INTO central_catalog
              (product_key,title,description,price_usdt,stock,image_path,active,level_required,created_at,updated_at)
              VALUES(?,?,?,?,?,?,1,2,?,?)""",
              (product_key,title,item["description"],float(item["price_usdt"]),50,
               image_path,v4.now(),v4.now()))
        conn.commit()
    finally:
        if own: conn.close()

def _resale_ask_price(conn, unit_cost, level):
    """Level 1 keeps its established fixed margin; Level 2 uses a random configured percentage."""
    cost=float(unit_cost)
    if int(level)==1:
        return round(cost+0.8,6)
    row=conn.execute(
        "SELECT profit_min,profit_max FROM level_settings WHERE level=?",(int(level),)
    ).fetchone()
    low=float(row["profit_min"]) if row else 2.0
    high=float(row["profit_max"]) if row else 6.5
    if high < low:
        low,high=2.0,6.5
    return round(cost*(1.0+random.uniform(low,high)/100.0),6)


def _pair_utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pair_reset_timestamp(reached_at, timezone_name):
    """Return 07:00 local on the calendar day after the 50th sale, in UTC."""
    zone=ZoneInfo(str(timezone_name))
    reached=datetime.fromisoformat(str(reached_at))
    if reached.tzinfo is None:
        reached=reached.replace(tzinfo=timezone.utc)
    local_reached=reached.astimezone(zone)
    next_day=local_reached.date()+timedelta(days=1)
    local_reset=datetime.combine(next_day,datetime_time(7,0),tzinfo=zone)
    return local_reset.astimezone(timezone.utc).isoformat(timespec="seconds")


def ensure_pair_progress(c, shop_id, owner_id, timezone_name=None, exclude_sold_quantity=0):
    """Create a cycle row once, carrying forward legacy completed sales safely."""
    row=c.execute("SELECT completed_count FROM merchant_pair_progress WHERE shop_id=?",
                  (int(shop_id),)).fetchone()
    if row:
        return row
    legacy=c.execute("""SELECT COALESCE(SUM(
        CASE WHEN status<>'refunded' THEN MAX(quantity-remaining_quantity,0) ELSE 0 END),0)
        FROM central_inventory WHERE shop_id=? AND owner_id=?""",
        (int(shop_id),int(owner_id))).fetchone()[0]
    previous=max(0,int(legacy or 0)-max(0,int(exclude_sold_quantity or 0)))
    count=min(50,previous)
    now=_pair_utc_now()
    reached_at=now if count>=50 else None
    reset_at=None
    if reached_at and timezone_name:
        try:
            reset_at=_pair_reset_timestamp(reached_at,timezone_name)
        except Exception:
            log.exception("Could not schedule legacy pair progress for shop %s",shop_id)
    c.execute("""INSERT OR IGNORE INTO merchant_pair_progress
        (shop_id,owner_id,completed_count,reached_at,reset_at,cycle_started_at,updated_at)
        VALUES(?,?,?,?,?,?,?)""",
        (int(shop_id),int(owner_id),count,reached_at,reset_at,now,now))
    return c.execute("SELECT completed_count FROM merchant_pair_progress WHERE shop_id=?",
                     (int(shop_id),)).fetchone()


def record_completed_pairs(c, shop_id, owner_id, quantity, source_type, source_order_id):
    """Record successfully settled resale units once, inside the sale transaction."""
    qty=max(0,int(quantity))
    if qty<1:
        return
    now=_pair_utc_now()
    event=c.execute("""INSERT OR IGNORE INTO merchant_pair_events
        (source_type,source_order_id,shop_id,owner_id,quantity,created_at)
        VALUES(?,?,?,?,?,?)""",
        (str(source_type),int(source_order_id),int(shop_id),int(owner_id),qty,now))
    if event.rowcount!=1:
        return
    shop=c.execute("SELECT location_timezone FROM shops WHERE id=? AND owner_id=?",
                    (int(shop_id),int(owner_id))).fetchone()
    timezone_name=shop["location_timezone"] if shop else None
    ensure_pair_progress(c,int(shop_id),int(owner_id),timezone_name,exclude_sold_quantity=qty)
    row=c.execute("SELECT completed_count,reached_at FROM merchant_pair_progress WHERE shop_id=?",
                  (int(shop_id),)).fetchone()
    if row and int(row["completed_count"] or 0)>=50:
        return
    current=int(row["completed_count"] or 0) if row else 0
    new_count=min(50,current+qty)
    reached_at=now if new_count>=50 else None
    reset_at=None
    if reached_at and timezone_name:
        try:
            reset_at=_pair_reset_timestamp(reached_at,timezone_name)
        except Exception:
            log.exception("Could not calculate merchant pair reset time for shop %s",shop_id)
    if row:
        c.execute("""UPDATE merchant_pair_progress
            SET completed_count=?,reached_at=CASE WHEN ?=50 THEN ? ELSE reached_at END,
                reset_at=CASE WHEN ?=50 THEN ? ELSE reset_at END,updated_at=?
            WHERE shop_id=?""",(new_count,new_count,reached_at,new_count,reset_at,now,int(shop_id)))
    else:
        c.execute("""INSERT INTO merchant_pair_progress
            (shop_id,owner_id,completed_count,reached_at,reset_at,cycle_started_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (int(shop_id),int(owner_id),new_count,reached_at,reset_at,now,now))


def balance(c, owner_id):
    row=c.execute("""SELECT
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type='deposit_credit' THEN amount ELSE 0 END),0) deposits,
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type IN ('central_purchase_debit','resale_purchase_debit','withdrawal_debit') THEN amount ELSE 0 END),0) debits,
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type IN ('central_purchase_refund_credit','resale_sale_credit','level_activation_wallet_credit','deposit_bonus_credit','referral_bonus_credit') THEN amount ELSE 0 END),0) refunds
      FROM ledger WHERE owner_id=?""",(owner_id,)).fetchone()
    return round(float(row["deposits"] or 0)-float(row["debits"] or 0)+float(row["refunds"] or 0),6)

def catalogue_text(c):
    rows=c.execute("SELECT id,title,price_usdt,stock,active FROM central_catalog ORDER BY id").fetchall()
    return "🛍 CENTRAL CATALOGUE\n\n"+ "\n\n".join(
      f"#{r['id']} {r['title']}\nPrice: {float(r['price_usdt']):g} USDT | Stock: {r['stock']} | Active: {'Yes' if r['active'] else 'No'}"
      for r in rows)

async def admin_catalog(update,ctx):
    if not v4.admin(update.effective_user.id):
        await update.effective_message.reply_text("System only."); return
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    await update.effective_message.reply_text(
        "<b>🛒 BUY PRODUCT</b>\n━━━━━━━━━━━━━━━━━━\nPick your level:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🟦 LEVEL 1",callback_data="ccadminlevel:1")],
            [InlineKeyboardButton("🟩 LEVEL 2 — 50 PRODUCTS",callback_data="ccadminlevel:2")],
            [InlineKeyboardButton("📦 Manage Central Orders",callback_data="cc:orders")],
            [InlineKeyboardButton("⬅️ System Panel",callback_data="adm:home")]]),
        parse_mode="HTML")

def _catalogue_page_keyboard(page,total,prefix,back_label,back_callback,dashboard_callback):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    last_page=max(0,(total-1)//CATALOGUE_PAGE_SIZE)
    rows=[]
    navigation=[]
    if page>0:
        navigation.append(InlineKeyboardButton("⬅️ Previous 5",callback_data=f"{prefix}:{page-1}"))
    if page<last_page:
        navigation.append(InlineKeyboardButton("Next 5 ➡️",callback_data=f"{prefix}:{page+1}"))
    if navigation:
        rows.append(navigation)
    rows.append([
        InlineKeyboardButton(back_label,callback_data=back_callback),
        InlineKeyboardButton("🏠 Dashboard",callback_data=dashboard_callback)
    ])
    return InlineKeyboardMarkup(rows)


async def _send_admin_level_page(update,ctx,level=1,page=0):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    level=int(level)
    if level not in (1,2):
        await update.callback_query.message.reply_text("Invalid catalogue level.")
        return
    setup(); c=v4.db()
    try:
        rows=c.execute("SELECT * FROM central_catalog WHERE level_required=? ORDER BY id",(level,)).fetchall()
    finally: c.close()
    total=len(rows)
    last_page=max(0,(total-1)//CATALOGUE_PAGE_SIZE)
    page=max(0,min(int(page),last_page))
    start=page*CATALOGUE_PAGE_SIZE
    page_rows=rows[start:start+CATALOGUE_PAGE_SIZE]
    q=update.callback_query
    await q.message.reply_text(
        f"<b>🛍 LEVEL {level} PRODUCTS</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Showing {start+1 if total else 0}–{min(start+CATALOGUE_PAGE_SIZE,total)} of {total} products. Manage each product below.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back to Levels",callback_data="adm:central"),
            InlineKeyboardButton("🏠 Dashboard",callback_data="adm:home")
        ]]),parse_mode="HTML")
    for p in page_rows:
        caption=(f"<b>#{p['id']} {html.escape(str(p['title']))}</b>\n{html.escape(str(p['description'] or ''))}\n\n"
                 f"💰 <b>{float(p['price_usdt']):g} USDT</b> / item\n📦 Stock: <b>{int(p['stock'])}</b>\n"
                 f"{'🟢 ACTIVE' if p['active'] else '🔴 OFF'}")
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("💲 Price",callback_data=f"cc:price:{p['id']}"),InlineKeyboardButton("📦 Stock",callback_data=f"cc:stock:{p['id']}")],
            [InlineKeyboardButton("🖼 Change Picture",callback_data=f"cc:photo:{p['id']}")],
            [InlineKeyboardButton("🔴 Turn Off" if p['active'] else "🟢 Turn On",callback_data=f"cc:toggle:{p['id']}")],
            [InlineKeyboardButton("⬅️ Levels",callback_data="adm:central"),InlineKeyboardButton("🏠 Dashboard",callback_data="adm:home")]])
        image_path=Path(__file__).resolve().parent/(p["image_path"] or "")
        try:
            if p["image_file_id"]:
                await ctx.bot.send_photo(q.message.chat_id,photo=p["image_file_id"],caption=caption[:1024],reply_markup=kb,parse_mode="HTML")
            elif image_path.is_file():
                with image_path.open("rb") as photo:
                    await ctx.bot.send_photo(q.message.chat_id,photo=photo,caption=caption[:1024],reply_markup=kb,parse_mode="HTML")
            else:
                await ctx.bot.send_message(q.message.chat_id,caption+"\n⚠️ Product photo unavailable.",reply_markup=kb,parse_mode="HTML")
        except Exception:
            log.exception("Could not send admin catalogue card")
            await ctx.bot.send_message(q.message.chat_id,caption,reply_markup=kb,parse_mode="HTML")
    await ctx.bot.send_message(
        q.message.chat_id,
        f"<b>PAGE {page+1}/{last_page+1}</b> • Products {start+1 if total else 0}–{min(start+CATALOGUE_PAGE_SIZE,total)} of {total}",
        reply_markup=_catalogue_page_keyboard(
            page,total,("ccadminpage" if level==1 else "ccadminpage:2"),
            "⬅️ Back to Levels","adm:central","adm:home"),
        parse_mode="HTML")


async def admin_level_callback(update,ctx):
    q=update.callback_query
    if not v4.admin(q.from_user.id):
        await safe_callback_answer(q,"System only",show_alert=True); return
    try:
        level=int(q.data.split(":")[1])
        if level not in (1,2): raise ValueError()
    except (ValueError,IndexError):
        await safe_callback_answer(q,"Invalid catalogue level",show_alert=True); return
    await safe_callback_answer(q)
    await _send_admin_level_page(update,ctx,level=level,page=0)


async def admin_catalog_page_callback(update,ctx):
    q=update.callback_query
    if not v4.admin(q.from_user.id):
        await safe_callback_answer(q,"System only",show_alert=True); return
    try:
        parts=q.data.split(":")
        if len(parts)==2:  # support old Level 1 page buttons
            level,page=1,int(parts[1])
        elif len(parts)==3:
            level,page=int(parts[1]),int(parts[2])
        else:
            raise ValueError()
        if level not in (1,2) or page<0: raise ValueError()
    except (ValueError,IndexError):
        await safe_callback_answer(q,"Invalid page",show_alert=True); return
    await safe_callback_answer(q)
    await _send_admin_level_page(update,ctx,level=level,page=page)


async def set_price(update,ctx):
    if not v4.admin(update.effective_user.id): return
    if len(ctx.args)!=2:
        await update.effective_message.reply_text("Usage: /centralprice PRODUCT_ID USDT_PRICE"); return
    try:
        pid=int(ctx.args[0]); price=round(float(ctx.args[1]),6)
        if pid<1 or price<0: raise ValueError()
    except ValueError:
        await update.effective_message.reply_text("Enter a valid product ID and non-negative USDT price."); return
    c=v4.db()
    row=c.execute("SELECT title,price_usdt FROM central_catalog WHERE id=?",(pid,)).fetchone()
    if not row: c.close(); await update.effective_message.reply_text("Central product not found."); return
    old=float(row["price_usdt"])
    c.execute("UPDATE central_catalog SET price_usdt=?,updated_at=? WHERE id=?",(price,v4.now(),pid))
    c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,None,"central_price_changed",f"Product #{pid} {row['title']}: {old:g} -> {price:g} USDT",v4.now()))
    c.commit(); c.close()
    await update.effective_message.reply_text(f"Updated #{pid} {row['title']}: {old:g} → {price:g} USDT.")

async def set_stock(update,ctx):
    if not v4.admin(update.effective_user.id): return
    if len(ctx.args)!=2:
        await update.effective_message.reply_text("Usage: /centralstock PRODUCT_ID QUANTITY"); return
    try:
        pid=int(ctx.args[0]); qty=int(ctx.args[1])
        if pid<1 or qty<0: raise ValueError()
    except ValueError:
        await update.effective_message.reply_text("Enter valid product ID and stock quantity (0 or more)."); return
    c=v4.db()
    old=c.execute("SELECT stock,title FROM central_catalog WHERE id=?",(pid,)).fetchone()
    cur=c.execute("UPDATE central_catalog SET stock=?,updated_at=? WHERE id=?",(qty,v4.now(),pid))
    if cur.rowcount and old:
        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,None,"central_stock_changed",f"Product #{pid} {old['title']}: {old['stock']} -> {qty}",v4.now()))
    c.commit(); c.close()
    await update.effective_message.reply_text(f"Stock updated for product #{pid}." if cur.rowcount else "Central product not found.")

async def set_active(update,ctx):
    if not v4.admin(update.effective_user.id): return
    if len(ctx.args)!=2 or ctx.args[1].lower() not in ("on","off"):
        await update.effective_message.reply_text("Usage: /centralactive PRODUCT_ID on|off"); return
    try: pid=int(ctx.args[0])
    except ValueError: await update.effective_message.reply_text("Invalid product ID."); return
    c=v4.db(); before=c.execute("SELECT title,active FROM central_catalog WHERE id=?",(pid,)).fetchone()
    new_active=1 if ctx.args[1].lower()=="on" else 0
    cur=c.execute("UPDATE central_catalog SET active=?,updated_at=? WHERE id=?",(new_active,v4.now(),pid))
    if cur.rowcount and before:
        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,None,"central_active_changed",f"Product #{pid} {before['title']}: {before['active']} -> {new_active}",v4.now()))
    c.commit(); c.close()
    await update.effective_message.reply_text("Catalogue status updated." if cur.rowcount else "Central product not found.")

async def buy(update,ctx):
    if len(ctx.args)!=2:
        await update.effective_message.reply_text("Usage: /centralbuy PRODUCT_ID QUANTITY"); return
    shop=v4.shop_for(update.effective_user.id)
    if not shop or shop["status"]!="approved" or int(shop["level"]) not in (1,2):
        await update.effective_message.reply_text("Approved Level 1 or Level 2 shop required for this catalogue."); return
    try:
        pid=int(ctx.args[0]); qty=int(ctx.args[1])
        if pid<1 or qty<1 or qty>1000: raise ValueError()
        if int(shop["level"])==1 and qty>1:
            await update.effective_message.reply_text(
                "🔒 Level 1 purchase limit: you can buy only 1 item per order. "
                "Upgrade to Level 2 to purchase multiple items.")
            return
    except ValueError:
        await update.effective_message.reply_text("Invalid product ID or quantity."); return
    c=v4.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        allowed, buyer_warning=v4.shop_order_gate(c,shop["id"])
        if not allowed:
            c.rollback(); await update.effective_message.reply_text(buyer_warning); return
        purchase_allowed,purchase_message=consume_extra_purchase_units(
            c,shop["id"],update.effective_user.id,qty)
        if not purchase_allowed:
            c.rollback(); await update.effective_message.reply_text(purchase_message); return
        p=c.execute("SELECT * FROM central_catalog WHERE id=? AND active=1 AND level_required=?",(pid,int(shop["level"]))).fetchone()
        if not p or float(p["price_usdt"])<=0:
            c.rollback(); await update.effective_message.reply_text("Product unavailable. System has not set its USDT price yet."); return
        if int(shop["level"])==2 and int(p["level_required"])==2:
            # Enforce the admin-assigned catalogue range and sequential unlock on the server,
            # not only in the visible Telegram buttons (commands/crafted callbacks cannot bypass it).
            assigned_limit=c.execute(
                "SELECT item_limit FROM merchant_level_item_limits WHERE shop_id=? AND level=2",
                (int(shop["id"]),)).fetchone()
            level_products=c.execute(
                "SELECT id FROM central_catalog WHERE active=1 AND level_required=2 ORDER BY id").fetchall()
            if assigned_limit is not None:
                level_products=level_products[:max(0,int(assigned_limit["item_limit"]))]
            else:
                # Existing merchants default to the original 50 only. Mobile SKUs
                # require an explicit System-approved item-limit expansion.
                level_products=[x for x in level_products if str(c.execute(
                    "SELECT product_key FROM central_catalog WHERE id=?",(int(x["id"]),)).fetchone()[0]).startswith("level2_")]
            assigned_ids=[int(x["id"]) for x in level_products]
            if pid not in assigned_ids:
                c.rollback()
                await update.effective_message.reply_text(
                    "🔒 This product is outside the Level 2 item limit assigned to your shop by System.")
                return
            prior=c.execute("SELECT COUNT(*) FROM central_orders WHERE buyer_shop_id=? AND catalog_id=?",
                            (shop["id"],pid)).fetchone()[0]
            if int(prior)>0:
                c.rollback()
                await update.effective_message.reply_text(
                    "🔒 This Level 2 product has already been purchased by your shop. "
                    "Each Level 2 catalogue product can be purchased only once.")
                return
            purchased_ids={int(x[0]) for x in c.execute(
                "SELECT DISTINCT catalog_id FROM central_orders WHERE buyer_shop_id=?",
                (int(shop["id"]),)).fetchall()}
            next_product=next((product_id for product_id in assigned_ids if product_id not in purchased_ids),None)
            if pid!=next_product:
                c.rollback()
                await update.effective_message.reply_text(
                    "⏳ Please complete the currently unlocked Level 2 product first. "
                    "The next product unlocks only after a successful purchase.")
                return
        if int(p["stock"])<qty:
            c.rollback(); await update.effective_message.reply_text(f"Insufficient central stock. Available: {p['stock']}"); return
        total=round(float(p["price_usdt"])*qty,6)
        available=balance(c,update.effective_user.id)
        if available+1e-9<total:
            c.rollback()
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
            tron=v4.PAY_TRON
            bep=v4.PAY_BSC
            deposit_text=(
                f"Insufficient USDT wallet balance.\n"
                f"Required: {total:g} USDT\n"
                f"Available: {available:g} USDT\n\n"
                "💳 DEPOSIT USDT TO TOP UP YOUR WALLET\n"
                "Choose the correct network. Do not send TRC20 to BEP20 or BEP20 to TRC20 address.\n\n"
                f"🔵 TRC20 address:\n{tron}\n\n"
                f"🟡 BEP20 address:\n{bep}\n\n"
                "After sending, open Deposit and submit your transaction screenshot for System verification."
            )
            deposit_kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Copy TRC20 Address",copy_text=CopyTextButton(tron))],
                [InlineKeyboardButton("📋 Copy BEP20 Address",copy_text=CopyTextButton(bep))],
                [InlineKeyboardButton("💳 Open Deposit",callback_data="m:deposit")]
            ])
            await update.effective_message.reply_text(deposit_text,reply_markup=deposit_kb)
            return
        c.execute("UPDATE central_catalog SET stock=stock-?,updated_at=? WHERE id=? AND stock>=?",(qty,v4.now(),pid,qty))
        if c.execute("SELECT changes()").fetchone()[0]!=1:
            c.rollback(); await update.effective_message.reply_text("Stock changed. Please retry."); return
        cur=c.execute("""INSERT INTO central_orders(catalog_id,buyer_shop_id,buyer_owner_id,quantity,unit_price_usdt,total_usdt,status,created_at)
          VALUES(?,?,?,?,?,?,'paid',?)""",(pid,shop["id"],update.effective_user.id,qty,p["price_usdt"],total,v4.now()))
        oid=cur.lastrowid
        c.execute("""INSERT INTO central_inventory(owner_id,shop_id,catalog_id,order_id,quantity,remaining_quantity,unit_cost_usdt,status,created_at)
          VALUES(?,?,?,?,?,?,?,'owned',?)""",
          (update.effective_user.id,shop["id"],pid,oid,qty,qty,float(p["price_usdt"]),v4.now()))
        inventory_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Apply resale rules belonging to the product's own level.
        resale_setup(c)
        product_level=int(p["level_required"])
        ask_price=_resale_ask_price(c,float(p["price_usdt"]),product_level)
        c.execute("""INSERT INTO resale_listings(inventory_id,owner_id,shop_id,catalog_id,quantity,remaining_quantity,
                     unit_cost_usdt,ask_price_usdt,status,created_at)
                     VALUES(?,?,?,?,?,?,?,?, 'active',?)""",
                  (inventory_id,update.effective_user.id,shop["id"],pid,qty,qty,float(p["price_usdt"]),ask_price,v4.now()))
        remaining_stock=int(p['stock'])-qty
        c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
          VALUES(?,?,?,'central_purchase_debit',?,'USDT',?,?)""",(oid,shop["id"],update.effective_user.id,total,f"Central catalogue order #{oid}",v4.now()))
        c.execute("""INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at)
          VALUES(?,?,?,?,?)""",(v4.ADMIN_ID,update.effective_user.id,"central_order_paid",f"Order #{oid}; product #{pid}; qty {qty}; {total} USDT",v4.now()))
        c.commit()
    except Exception:
        c.rollback(); log.exception("Central catalogue purchase failed")
        await update.effective_message.reply_text("Purchase failed safely. No wallet debit or stock deduction was committed.")
        return
    finally: c.close()
    await update.effective_message.reply_text(f"✅ Purchase successful\nOrder #{oid}\n{p['title']} × {qty}\nTotal paid: {total:g} USDT\nRemaining shared stock: {remaining_stock}\nRemaining USDT wallet: {balance_after_text(total,available)}" + (f"\n⚠️ SYSTEM WARNING: {buyer_warning}" if buyer_warning else ""))
    try:
        await ctx.bot.send_message(v4.ADMIN_ID,f"🛒 CENTRAL ORDER #{oid}\n{p['title']} × {qty}\nOwner: {update.effective_user.id} | Shop #{shop['id']}\nPaid: {total:g} USDT")
    except Exception: log.exception("System central order notification failed")

def balance_after_text(total, before):
    return f"{max(0.0, before-total):.6f}".rstrip("0").rstrip(".")

def consume_extra_purchase_units(c, shop_id, owner_id, quantity):
    """At 50 completed resale pairs, require and consume System-approved units atomically."""
    qty=max(1,int(quantity))
    progress=c.execute(
        "SELECT completed_count FROM merchant_pair_progress WHERE shop_id=? AND owner_id=?",
        (int(shop_id),int(owner_id))).fetchone()
    completed=int(progress["completed_count"] or 0) if progress else 0
    if completed<50:
        return True, None
    allowance=c.execute(
        "SELECT remaining_units FROM merchant_extra_item_allowances WHERE shop_id=?",
        (int(shop_id),)).fetchone()
    remaining=int(allowance["remaining_units"] or 0) if allowance else 0
    if remaining<qty:
        return False, (
            "🔒 Your 50/50 completed-pair limit has been reached. "
            f"System approval is required for more purchases. Approved units remaining: {remaining}.")
    changed=c.execute(
        "UPDATE merchant_extra_item_allowances SET remaining_units=remaining_units-?,updated_at=? "
        "WHERE shop_id=? AND remaining_units>=?",
        (qty,v4.now(),int(shop_id),qty))
    if changed.rowcount!=1:
        return False, "🔒 Approved purchase units changed. Please try again."
    return True, None

async def orders(update,ctx):
    if not v4.admin(update.effective_user.id): return
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    c=v4.db()
    rows=c.execute("""SELECT o.id,o.buyer_owner_id,o.buyer_shop_id,o.quantity,o.total_usdt,o.status,o.created_at,p.title,
      (SELECT entry_type FROM ledger l WHERE l.transaction_id=o.id AND l.owner_id=o.buyer_owner_id
       AND l.entry_type IN ('central_purchase_debit','resale_purchase_debit') ORDER BY l.id LIMIT 1) purchase_type
      FROM central_orders o JOIN central_catalog p ON p.id=o.catalog_id ORDER BY o.id DESC LIMIT 50""").fetchall()
    listings=c.execute("""SELECT l.id,l.owner_id,l.shop_id,l.remaining_quantity,l.ask_price_usdt,
      p.title,s.shop_name FROM resale_listings l
      JOIN central_catalog p ON p.id=l.catalog_id JOIN shops s ON s.id=l.shop_id
      WHERE l.status='active' AND l.remaining_quantity>0
      ORDER BY l.id DESC LIMIT 50""").fetchall()
    c.close()
    # Keep the overview short. Truncating a long HTML string at an arbitrary
    # character can cut through <code>/<b> tags and makes Telegram reject it.
    orders_text=(
        "<b>📦 MANAGE CENTRAL ORDERS</b>\n━━━━━━━━━━━━━━━━━━\n"
        "<i>Original catalogue orders are shown below. Merchant-listed stock is available "
        "for direct System approval with an automatic 2% bonus on the listed amount.</i>\n"
        f"Original catalogue orders: <b>{len(rows)}</b>"
    )
    await update.effective_message.reply_text(orders_text,parse_mode="HTML")
    for r in rows:
        buttons=[]
        if r["status"]=="paid":
            buttons=[[InlineKeyboardButton("✅ Mark Fulfilled",callback_data=f"cc:fulfill:{r['id']}")]]
            if r["purchase_type"]=="central_purchase_debit":
                buttons[0].append(InlineKeyboardButton("↩️ Refund + Restock",callback_data=f"cc:refund:{r['id']}"))
        buttons.append([InlineKeyboardButton("⬅️ Central Catalogue",callback_data="adm:central"),
                        InlineKeyboardButton("🏠 System Control Center",callback_data="adm:home")])
        await update.effective_message.reply_text(
            f"<b>Central order #{r['id']}</b> — {html.escape(str(r['status']).upper())}\n"
            f"{html.escape(str(r['title']))} × {int(r['quantity'])}\n"
            f"Owner: <code>{int(r['buyer_owner_id'])}</code> • Shop: #{int(r['buyer_shop_id'])}\n"
            f"Amount: <b>{float(r['total_usdt']):g} USDT</b>\n"
            f"Created: <code>{html.escape(str(r['created_at']))}</code>",
            reply_markup=InlineKeyboardMarkup(buttons),parse_mode="HTML")
    await update.effective_message.reply_text(
        "<b>🏷️ MERCHANT-LISTED ORDERS</b>\nListed amount + 2% automatic bonus. Approve to purchase all available quantity.",
        parse_mode="HTML")
    for r in listings:
        listed=float(r["ask_price_usdt"])
        payout=round(listed*1.02,6)
        qty=int(r["remaining_quantity"])
        await update.effective_message.reply_text(
            f"<b>Listing #{r['id']} — {html.escape(str(r['title']))}</b>\n"
            f"Merchant: {html.escape(str(r['shop_name']))} • Shop #{r['shop_id']}\n"
            f"Available: <b>{qty}</b> item(s)\n"
            f"Merchant listed amount: <b>{listed:g} USDT / item</b>\n"
            f"Automatic 2% bonus: <b>+{listed*0.02:.6f} USDT / item</b>\n"
            f"Merchant receives: <b>{payout:g} USDT / item</b>\n"
            f"Total System payment: <b>{payout*qty:.6f} USDT</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ APPROVE + BUY (2% BONUS)",callback_data=f"cc:approve_listing:{r['id']}")],
                [InlineKeyboardButton("❌ Reject Listing",callback_data=f"rs:reject:{r['id']}")],
                [InlineKeyboardButton("⬅️ Refresh Orders",callback_data="cc:orders")]]),
            parse_mode="HTML")
    if not listings:
        await update.effective_message.reply_text("No active merchant-listed orders right now.")

async def admin_photo(update,ctx):
    """Persist an System-uploaded Telegram photo as the central catalogue image."""
    if not v4.admin(update.effective_user.id):
        ctx.user_data.pop("central_admin_photo",None); return
    pending=ctx.user_data.get("central_admin_photo")
    if not pending: return
    photos=getattr(update.message,"photo",None) or []
    if not photos:
        await update.message.reply_text("Please send a photo, or type CANCEL to keep the existing image."); return
    file_id=getattr(photos[-1],"file_id",None)
    if not file_id:
        await update.message.reply_text("Telegram did not provide a usable photo. Please send it again."); return
    pid=int(pending["product_id"]); conn=v4.db()
    try:
        row=conn.execute("SELECT title FROM central_catalog WHERE id=?",(pid,)).fetchone()
        if not row:
            ctx.user_data.pop("central_admin_photo",None)
            await update.message.reply_text("Product not found. No picture was changed."); return
        conn.execute("UPDATE central_catalog SET image_file_id=?,updated_at=? WHERE id=?",(file_id,v4.now(),pid))
        conn.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                     (v4.ADMIN_ID,None,"central_product_photo_changed",f"Product #{pid} {row['title']}: Telegram photo updated",v4.now()))
        conn.commit()
    except Exception:
        conn.rollback(); log.exception("Saving central catalogue photo failed")
        await update.message.reply_text("Photo could not be saved. The previous picture remains unchanged."); return
    finally: conn.close()
    ctx.user_data.pop("central_admin_photo",None)
    await update.message.reply_text(f"✅ Picture updated for product #{pid} — {html.escape(str(row['title']))}. The new image will appear in System and merchant catalogues.",parse_mode="HTML")


async def admin_callback(update,ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    q=update.callback_query
    if not v4.admin(q.from_user.id):
        await q.answer("System only",show_alert=True); return
    await _safe_callback_answer(q)
    parts=q.data.split(":")
    action=parts[1]
    if action=="orders":
        await orders(update,ctx); return
    if action=="view":
        pid=int(parts[2]); c=v4.db()
        row=c.execute("SELECT * FROM central_catalog WHERE id=?",(pid,)).fetchone(); c.close()
        if not row:
            await q.message.reply_text("Product not found."); return
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb=InlineKeyboardMarkup([
          [InlineKeyboardButton("💲 Edit USDT Price",callback_data=f"cc:price:{pid}"),
           InlineKeyboardButton("📦 Edit Shared Stock",callback_data=f"cc:stock:{pid}")],
          [InlineKeyboardButton("🔴 Turn Off" if row["active"] else "🟢 Turn On",callback_data=f"cc:toggle:{pid}")],
          [InlineKeyboardButton("⬅️ Catalogue List",callback_data="adm:central"),
           InlineKeyboardButton("🏠 System Panel",callback_data="adm:home")]
        ])
        active_label="🟢 ACTIVE" if row["active"] else "🔴 OFF"
        await q.message.reply_text(
          f"<b>🛍 {html.escape(str(row['title']))}</b>\n━━━━━━━━━━━━━━━━━━\n"
          f"Price: <b>{float(row['price_usdt']):g} USDT</b>\nShared stock: <b>{row['stock']}</b>\nStatus: <b>{active_label}</b>",
          reply_markup=kb,parse_mode="HTML"); return
    if action=="photo":
        pid=int(parts[2]); conn=v4.db()
        try: row=conn.execute("SELECT title FROM central_catalog WHERE id=?",(pid,)).fetchone()
        finally: conn.close()
        if not row:
            await q.message.reply_text("Product not found."); return
        ctx.user_data["central_admin_photo"]={"product_id":pid}
        await q.message.reply_text(f"🖼 Send the new Telegram photo for product #{pid} — {row['title']}. The new photo will replace the catalogue image. Type CANCEL to keep the current picture.")
        return
    if action in ("price","stock"):
        pid=int(parts[2]); ctx.user_data["central_admin_edit"]={"field":action,"product_id":pid}
        prompt="new price in USDT" if action=="price" else "new shared stock quantity"
        await q.message.reply_text(f"Product #{pid}: send the {prompt} as a number. Type CANCEL to stop.")
        return
    if action=="toggle":
        pid=int(parts[2]); c=v4.db()
        before=c.execute("SELECT title,active FROM central_catalog WHERE id=?",(pid,)).fetchone()
        c.execute("UPDATE central_catalog SET active=CASE WHEN active=1 THEN 0 ELSE 1 END,updated_at=? WHERE id=?",(v4.now(),pid))
        row=c.execute("SELECT title,active FROM central_catalog WHERE id=?",(pid,)).fetchone()
        if row and before:
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,None,"central_active_changed",f"Product #{pid} {row['title']}: {before['active']} -> {row['active']}",v4.now()))
        c.commit(); c.close()
        await q.message.reply_text(
          f"<b>{html.escape(str(row['title']))}</b> — {'🟢 ACTIVE' if row['active'] else '🔴 OFF'}",
          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Product",callback_data=f"cc:view:{pid}"),InlineKeyboardButton("⬅️ Catalogue",callback_data="adm:central")]]),parse_mode="HTML"); return
    if action=="approve_listing":
        lid=int(parts[2]); c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            listing=c.execute("""SELECT l.*,p.title,s.shop_name,s.status shop_status,
                s.control_status FROM resale_listings l
                JOIN central_catalog p ON p.id=l.catalog_id
                JOIN shops s ON s.id=l.shop_id WHERE l.id=?""",(lid,)).fetchone()
            if (not listing or listing["status"]!="active"
                or int(listing["remaining_quantity"])<1
                or listing["shop_status"]!="approved"
                or str(listing["control_status"] or "active").lower()!="active"):
                c.rollback()
                await q.message.reply_text("Merchant listing is no longer available or the shop is not active. No payment was made.")
                return
            qty=int(listing["remaining_quantity"])
            listed_price=float(listing["ask_price_usdt"])
            unit_price=round(listed_price*1.02,6)
            total=round(unit_price*qty,6)
            profit=round((unit_price-float(listing["unit_cost_usdt"]))*qty,6)
            changed=c.execute("""UPDATE resale_listings SET remaining_quantity=0,status='sold'
                WHERE id=? AND status='active' AND remaining_quantity=?""",(lid,qty))
            if changed.rowcount!=1: raise RuntimeError("Merchant listing changed during System approval")
            cur=c.execute("""INSERT INTO admin_resale_orders
                (listing_id,seller_owner_id,seller_shop_id,catalog_id,quantity,unit_price_usdt,total_usdt,status,created_at)
                VALUES(?,?,?,?,?,?,?,'purchased',?)""",
                (lid,listing["owner_id"],listing["shop_id"],listing["catalog_id"],qty,unit_price,total,v4.now()))
            oid=cur.lastrowid
            changed=c.execute("""UPDATE central_inventory SET remaining_quantity=remaining_quantity-?
                WHERE id=? AND owner_id=? AND remaining_quantity>=?""",
                (qty,listing["inventory_id"],listing["owner_id"],qty))
            if changed.rowcount!=1: raise RuntimeError("Merchant inventory changed; System approval rolled back")
            c.execute("""INSERT INTO admin_resale_inventory
                (admin_id,catalog_id,order_id,quantity,remaining_quantity,unit_cost_usdt,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (v4.ADMIN_ID,listing["catalog_id"],oid,qty,qty,unit_price,v4.now()))
            c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
                VALUES(?,?,?,'resale_sale_credit',?,'USDT',?,?)""",
                (oid,listing["shop_id"],listing["owner_id"],total,
                 f"System purchase listing #{lid} including 2% bonus",v4.now()))
            if profit>0:
                c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
                    VALUES(?,?,?,'resale_profit_credit',?,'USDT',?,?)""",
                    (oid,listing["shop_id"],listing["owner_id"],profit,
                     f"Profit on System purchase #{oid}",v4.now()))
            record_completed_pairs(c,listing["shop_id"],listing["owner_id"],qty,"system_resale",oid)
            c.execute("""INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at)
                VALUES(?,?,?,?,?)""",
                (q.from_user.id,listing["owner_id"],"system_resale_approved",
                 f"Order #{oid}; listing #{lid}; qty {qty}; listed {listed_price:g}; unit payout {unit_price:g}; total {total:g} USDT; margin {profit:g}",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("System merchant listing approval failed")
            await q.message.reply_text("System approval failed safely. No wallet or inventory changes were committed.")
            return
        finally:
            c.close()
        await q.message.reply_text(
            f"<b>✅ MERCHANT LISTING APPROVED</b>\nOrder #{oid} • Listing #{lid}\n"
            f"Quantity: {qty}\nListed amount: {listed_price:g} USDT / item\n"
            f"2% bonus: +{listed_price*0.02:.6f} USDT / item\n"
            f"Total credited: <b>{total:.6f} USDT</b>",parse_mode="HTML")
        try:
            await ctx.bot.send_message(listing["owner_id"],
                f"<b>✅ YOUR LISTING WAS APPROVED BY SYSTEM</b>\nListing #{lid} • Order #{oid}\n"
                f"Quantity: {qty}\nListed amount: {listed_price:g} USDT / item\n"
                f"2% bonus: +{listed_price*0.02:.6f} USDT / item\n"
                f"Total credited: <b>{total:.6f} USDT</b>",parse_mode="HTML")
        except Exception: log.exception("System resale approval merchant notification failed")
        return
    if action in ("fulfill","refund"):
        oid=int(parts[2]); c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            order=c.execute("SELECT * FROM central_orders WHERE id=?",(oid,)).fetchone()
            if not order or order["status"]!="paid":
                c.rollback(); await q.message.reply_text("Order not found or already processed. No changes made."); return
            if action=="fulfill":
                c.execute("UPDATE central_orders SET status='fulfilled' WHERE id=? AND status='paid'",(oid,))
                label="fulfilled"
                details=f"Central order #{oid} marked fulfilled"
            else:
                purchase=c.execute("""SELECT entry_type FROM ledger
                    WHERE transaction_id=? AND owner_id=?
                      AND entry_type IN ('central_purchase_debit','resale_purchase_debit')
                    ORDER BY id LIMIT 1""",(oid,order["buyer_owner_id"])).fetchone()
                if not purchase or purchase["entry_type"]!="central_purchase_debit":
                    c.rollback()
                    await q.message.reply_text(
                        "Automatic refund is only available for original Central Catalogue purchases. "
                        "This order is a merchant-to-merchant resale; review both wallets and stock before any manual correction.")
                    return
                inventory=c.execute("""SELECT id,quantity,remaining_quantity FROM central_inventory
                    WHERE order_id=? AND owner_id=? AND shop_id=? ORDER BY id LIMIT 1""",
                    (oid,order["buyer_owner_id"],order["buyer_shop_id"])).fetchone()
                if not inventory or int(inventory["remaining_quantity"])!=int(order["quantity"]):
                    c.rollback()
                    await q.message.reply_text(
                        "Refund blocked: some or all items have already been resold or used. "
                        "No wallet, inventory, listing, or catalogue stock was changed.")
                    return
                changed=c.execute("UPDATE central_orders SET status='refunded' WHERE id=? AND status='paid'",(oid,))
                if changed.rowcount!=1:
                    raise RuntimeError("Order already processed")
                # Revoke the buyer's untouched inventory and every active listing created from it.
                c.execute("""UPDATE resale_listings SET remaining_quantity=0,status='cancelled'
                    WHERE inventory_id=? AND status='active'""",(inventory["id"],))
                c.execute("""UPDATE central_inventory SET remaining_quantity=0,status='refunded'
                    WHERE id=? AND owner_id=? AND remaining_quantity=?""",
                    (inventory["id"],order["buyer_owner_id"],order["quantity"]))
                if c.execute("SELECT changes()").fetchone()[0]!=1:
                    raise RuntimeError("Inventory changed during refund")
                c.execute("UPDATE central_catalog SET stock=stock+?,updated_at=? WHERE id=?",
                          (order["quantity"],v4.now(),order["catalog_id"]))
                c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
                  VALUES(?,?,?,'central_purchase_refund_credit',?,'USDT',?,?)""",
                  (oid,order["buyer_shop_id"],order["buyer_owner_id"],order["total_usdt"],f"Refund for central order #{oid}",v4.now()))
                label="refunded + inventory revoked + stock restored"
                details=f"Central order #{oid} refunded {order['total_usdt']} USDT; buyer inventory/listings revoked and shared stock restored"
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
              (v4.ADMIN_ID,order["buyer_owner_id"],f"central_order_{action}",details,v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Central order action failed")
            await q.message.reply_text("Action failed safely. No refund or status change was committed."); return
        finally: c.close()
        status_icon="✅" if action=="fulfill" else "↩️"
        await q.message.reply_text(f"<b>{status_icon} ORDER #{oid} {html.escape(label.upper())}</b>",parse_mode="HTML")
        try:
            await ctx.bot.send_message(order["buyer_owner_id"],
              f"Central order #{oid} is {label.upper()}."+
              (f" {order['total_usdt']:g} USDT has been returned to your wallet and stock restored." if action=="refund" else ""))
        except Exception: log.exception("Central order owner notification failed")


async def owner_market(update,ctx,shop):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    if not shop or shop["status"] != "approved" or int(shop["level"]) not in (1,2):
        await update.callback_query.message.reply_text(
            "This catalogue is available only to approved Level 1 or Level 2 shops.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data="m:dashboard")]]))
        return
    level=int(shop["level"])
    buttons=[
        [InlineKeyboardButton(
            "🟦 LEVEL 1 — CLASSIC CATALOGUE" if available_level==1 else f"🟩 LEVEL {available_level} CATALOGUE",
            callback_data=f"cclevel:{available_level}")]
        for available_level in (level,)
    ]
    await update.callback_query.message.reply_text(
        "<b>🛒 BUY PRODUCT</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Choose a catalogue. Your active level includes all lower-level catalogues:",
        reply_markup=InlineKeyboardMarkup(buttons+[
            [InlineKeyboardButton("⬅️ Back to My Products",callback_data="m:products"),
             InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")]]),
        parse_mode="HTML")


async def owner_level_callback(update,ctx):
    q=update.callback_query
    shop=v4.shop_for(q.from_user.id)
    try: level=int(q.data.split(":")[1])
    except (ValueError,IndexError): level=0
    if not shop or shop["status"]!="approved" or int(shop["level"]) not in (1,2) or level!=int(shop["level"]):
        await safe_callback_answer(q,"Your approved shop level does not match this catalogue",show_alert=True); return
    await safe_callback_answer(q)
    await show_owner_level_products(update,ctx,shop,level=level)


async def owner_catalog_page_callback(update,ctx):
    q=update.callback_query
    shop=v4.shop_for(q.from_user.id)
    if not shop or shop["status"]!="approved" or int(shop["level"]) not in (1,2):
        await safe_callback_answer(q,"Approved Level 1 or Level 2 shop required",show_alert=True); return
    try:
        parts=q.data.split(":")
        if len(parts)==2:  # legacy Level 1 page callback
            level,page=1,int(parts[1])
        elif len(parts)==3:
            level,page=int(parts[1]),int(parts[2])
        else:
            raise ValueError()
        if level<1 or level!=int(shop["level"]) or page<0: raise ValueError()
    except (ValueError,IndexError):
        await safe_callback_answer(q,"Invalid page",show_alert=True); return
    await safe_callback_answer(q)
    await show_owner_level_products(update,ctx,shop,level=level,page=page,track_visit=False)


async def show_owner_level_products(update,ctx,shop,page=0,track_visit=True,level=None):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    level=int(level if level is not None else (shop["level"] if shop else 0))
    if not shop or shop["status"] != "approved" or level!=int(shop["level"]) or level not in (1,2):
        await update.callback_query.message.reply_text(
            "This catalogue is available only to an approved shop at the matching level.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data="m:dashboard")]]))
        return
    setup()
    c=v4.db()
    try:
        if track_visit:
            c.execute("""INSERT INTO catalogue_visits(owner_id,visits,last_seen) VALUES(?,1,?)
              ON CONFLICT(owner_id) DO UPDATE SET visits=visits+1,last_seen=excluded.last_seen""",
              (update.effective_user.id,v4.now()))
        total_visitors=c.execute("SELECT COUNT(*) FROM catalogue_visits").fetchone()[0]
        rows=c.execute("""
            SELECT p.*,
              CASE WHEN EXISTS(SELECT 1 FROM central_orders o
                WHERE o.buyer_shop_id=? AND o.catalog_id=p.id) THEN 1 ELSE 0 END AS already_purchased,
              (SELECT COUNT(*) FROM central_orders o
               WHERE o.buyer_shop_id=? AND o.catalog_id=p.id) AS shop_order_count,
              (SELECT COALESCE(SUM(o.quantity),0) FROM central_orders o
               WHERE o.buyer_shop_id=? AND o.catalog_id=p.id) AS shop_units_bought
            FROM central_catalog p WHERE p.active=1 AND p.level_required=? ORDER BY p.id
        """,(shop["id"],shop["id"],shop["id"],level)).fetchall()
        settings=c.execute("SELECT profit_min,profit_max FROM level_settings WHERE level=?",(level,)).fetchone()
        limit_row=c.execute("SELECT item_limit FROM merchant_level_item_limits WHERE shop_id=? AND level=?",
                            (int(shop["id"]),level)).fetchone()
        c.commit()
    finally: c.close()
    if limit_row is not None:
        rows=rows[:max(0,int(limit_row["item_limit"]))]
    elif level==2:
        # No explicit System grant means exactly the original 50 Level 2 products.
        rows=[row for row in rows if str(row["product_key"]).startswith("level2_")]
    total=len(rows)
    if level==2:
        # System-assigned Level 2 products unlock strictly one at a time.
        # A successful paid purchase is the only event that reveals the next item.
        purchased_count=sum(1 for row in rows if int(row["already_purchased"]))
        page_rows=[next((row for row in rows if not int(row["already_purchased"])),None)]
        page_rows=[row for row in page_rows if row is not None]
        start=purchased_count
        last_page=max(0,total-1)
    else:
        last_page=max(0,(total-1)//CATALOGUE_PAGE_SIZE)
        page=max(0,min(int(page),last_page))
        start=page*CATALOGUE_PAGE_SIZE
        page_rows=rows[start:start+CATALOGUE_PAGE_SIZE]
    margin_text=(f"Resale margin is randomly set between {float(settings['profit_min']):g}% and {float(settings['profit_max']):g}% per merchant/product."
                 if level==2 and settings else "Resale margin is a reference, not guaranteed income.")
    await update.callback_query.message.reply_text(
        f"<b>🛍 LEVEL {level} CENTRAL CATALOGUE</b>\n━━━━━━━━━━━━━━━━━━\n"
        +(f"Purchased: {purchased_count}/{total} • Next product unlocks after purchase\n" if level==2 else f"Showing {start+1 if total else 0}–{min(start+CATALOGUE_PAGE_SIZE,total)} of {total} products\n")
        +f"{total_visitors} unique visitors\n"
        f"<i>{margin_text} Profit is credited after a completed sale.</i>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back to My Products",callback_data="m:products"),
            InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")]]),
        parse_mode="HTML")
    if level==2 and not page_rows:
        await ctx.bot.send_message(
            update.effective_chat.id,
            ("<b>🎉 LEVEL 2 ASSIGNED ITEMS COMPLETED</b>\n"
             f"You have purchased all {total} products assigned by System."),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ My Products",callback_data="m:products"),InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")]]),
            parse_mode="HTML")
    for p in page_rows:
        price=float(p["price_usdt"] or 0); stock=int(p["stock"] or 0)
        caption=(f"<b>#{p['id']} {html.escape(str(p['title']))}</b>\n"
                 f"{html.escape(str(p['description'] or ''))}\n\n"
                 f"Price: <b>{price:g} USDT</b>\n"
                 f"Available stock: <b>{stock}</b>\n"+
                 (f"Your purchase: <b>{int(p['shop_order_count'])} order(s) • {int(p['shop_units_bought'])} item(s)</b>\n"
                  if level==2 else "")+
                 ("🔒 Already purchased — this Level 2 product can only be bought once."
                  if level==2 and int(p["already_purchased"]) else
                  ("Select a quantity below to buy." if price>0 and stock>0 else "🔴 Unavailable — System must set price and stock.")))
        # Level 1 merchants may purchase only one unit per order.
        max_quantity=1 if int(shop["level"])==1 else 10
        already_purchased=level==2 and bool(int(p["already_purchased"]))
        quantities=list(range(1,min(max_quantity,stock)+1)) if stock>0 and not already_purchased else []
        buttons=[
            [InlineKeyboardButton(str(n),callback_data=f"ccbuy:{p['id']}:{n}")
             for n in quantities[pos:pos+2]]
            for pos in range(0,len(quantities),2)
        ]
        buttons.append([
            InlineKeyboardButton("⬅️ Back to My Products",callback_data="m:products"),
            InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")])
        kb=InlineKeyboardMarkup(buttons)
        image_path=Path(__file__).resolve().parent/(p["image_path"] or "")
        try:
            if p["image_file_id"]:
                await ctx.bot.send_photo(update.effective_chat.id,photo=p["image_file_id"],caption=caption[:1024],reply_markup=kb,parse_mode="HTML")
            elif image_path.is_file():
                with image_path.open("rb") as photo:
                    await ctx.bot.send_photo(update.effective_chat.id,photo=photo,caption=caption[:1024],reply_markup=kb,parse_mode="HTML")
            else:
                await ctx.bot.send_message(update.effective_chat.id,caption+"\n⚠️ Product photo unavailable.",reply_markup=kb,parse_mode="HTML")
        except Exception:
            log.exception("Could not send catalogue item")
            await ctx.bot.send_message(update.effective_chat.id,caption,reply_markup=kb,parse_mode="HTML")
    if level==1:
        await ctx.bot.send_message(
            update.effective_chat.id,
            f"<b>PAGE {page+1}/{last_page+1}</b> • Products {start+1 if total else 0}–{min(start+CATALOGUE_PAGE_SIZE,total)} of {total}",
            reply_markup=_catalogue_page_keyboard(
                page,total,f"ccpage:{level}",
                "⬅️ Back to My Products","m:products","m:dashboard"),
            parse_mode="HTML")


async def owner_buy_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    try:
        _,pid_s,qty_s=q.data.split(":")
        c=v4.db()
        try:
            product=c.execute("SELECT level_required FROM central_catalog WHERE id=?",(int(pid_s),)).fetchone()
        finally:
            c.close()
        ctx.args=[pid_s,qty_s]
        await buy(update,ctx)
        if product and int(product["level_required"])==2:
            # Refresh the Level 2 screen; after a successful buy the next
            # unpurchased product is shown, otherwise the same one remains.
            shop=v4.shop_for(q.from_user.id)
            if shop and shop["status"]=="approved" and int(shop["level"])>=2:
                await show_owner_level_products(update,ctx,shop,level=2,track_visit=False)
    except Exception:
        log.exception("Catalogue button purchase failed")
        await q.message.reply_text("Purchase could not be completed. Please retry with /centralbuy PRODUCT_ID QUANTITY.")


# --- Owner resale listings and System direct-purchase market ---
def resale_setup(c=None):
    own=c is None; conn=c or v4.db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS resale_listings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, inventory_id INTEGER NOT NULL,
          owner_id INTEGER NOT NULL, shop_id INTEGER NOT NULL, catalog_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL, remaining_quantity INTEGER NOT NULL,
          unit_cost_usdt REAL NOT NULL, ask_price_usdt REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_resale_orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL,
          seller_owner_id INTEGER NOT NULL, seller_shop_id INTEGER NOT NULL,
          catalog_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
          unit_price_usdt REAL NOT NULL, total_usdt REAL NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_resale_inventory(
          id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
          catalog_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL, remaining_quantity INTEGER NOT NULL,
          unit_cost_usdt REAL NOT NULL, created_at TEXT NOT NULL)""")
        if own:
            # Normalize existing active Level 1 listings to the fixed 0.8 USDT/item margin.
            conn.execute("""UPDATE resale_listings SET ask_price_usdt=ROUND(unit_cost_usdt+0.8,6)
              WHERE status='active' AND remaining_quantity>0
                AND shop_id IN (SELECT id FROM shops WHERE status='approved' AND level=1)""")
            # Idempotently publish remaining stock bought by every approved Level 1 merchant.
            conn.execute("""INSERT INTO resale_listings
              (inventory_id,owner_id,shop_id,catalog_id,quantity,remaining_quantity,unit_cost_usdt,ask_price_usdt,status,created_at)
              SELECT i.id,i.owner_id,i.shop_id,i.catalog_id,i.remaining_quantity,i.remaining_quantity,
                     i.unit_cost_usdt,ROUND(i.unit_cost_usdt+0.8,6),'active',?
              FROM central_inventory i JOIN shops s ON s.id=i.shop_id
              WHERE i.remaining_quantity>0 AND s.status='approved' AND s.level=1
                AND NOT EXISTS (SELECT 1 FROM resale_listings l
                                WHERE l.inventory_id=i.id AND l.status='active' AND l.remaining_quantity>0)""",(v4.now(),))
            conn.commit()
    finally:
        if own: conn.close()

async def list_inventory(update,ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    if v4.admin(update.effective_user.id): return
    shop=v4.shop_for(update.effective_user.id)
    if not shop or shop["status"]!="approved" or int(shop["level"]) not in (1,2):
        await update.effective_message.reply_text("<b>🔒 APPROVED LEVEL REQUIRED</b>\nYour shop must be approved and active at Level 1 or Level 2.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML"); return
    resale_setup(); c=v4.db()
    rows=c.execute("""SELECT i.id,i.catalog_id,i.remaining_quantity,i.unit_cost_usdt,p.title,p.level_required,
      cfg.profit_min AS level_profit_min,cfg.profit_max AS level_profit_max,
      COALESCE((SELECT SUM(l.remaining_quantity) FROM resale_listings l WHERE l.inventory_id=i.id AND l.status='active'),0) listed,
      (SELECT COUNT(*) FROM central_orders o WHERE o.buyer_shop_id=i.shop_id AND o.catalog_id=i.catalog_id) shop_purchase_orders,
      (SELECT COALESCE(SUM(o.quantity),0) FROM central_orders o WHERE o.buyer_shop_id=i.shop_id AND o.catalog_id=i.catalog_id) shop_units_bought,
      (SELECT COALESCE(SUM(ci.quantity-ci.remaining_quantity),0) FROM central_inventory ci
       WHERE ci.shop_id=i.shop_id AND ci.catalog_id=i.catalog_id) shop_units_sold
      FROM central_inventory i JOIN central_catalog p ON p.id=i.catalog_id
      LEFT JOIN level_settings cfg ON cfg.level=p.level_required
      WHERE i.owner_id=? AND p.level_required<=? AND i.remaining_quantity>0 ORDER BY i.id DESC""",
      (update.effective_user.id,int(shop["level"]))).fetchall()
    c.close()
    if not rows:
        await update.effective_message.reply_text("<b>📦 LIST MY STOCK</b>\nNo available inventory to list right now.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML"); return
    for r in rows:
        free=max(0,int(r["remaining_quantity"])-int(r["listed"]))
        unit_cost=round(float(r["unit_cost_usdt"]),6)
        item_level=int(r["level_required"])
        if item_level==1:
            low_profit=high_profit=0.8
            price=round(unit_cost+0.8,6)
        else:
            low_pct=float(r["level_profit_min"]) if r["level_profit_min"] is not None else 2.0
            high_pct=float(r["level_profit_max"]) if r["level_profit_max"] is not None else 6.5
            low_profit=round(unit_cost*low_pct/100.0,6)
            high_profit=round(unit_cost*high_pct/100.0,6)
            price=round(unit_cost+(low_profit+high_profit)/2,6)
        listed_profit_low=round(low_profit*int(r["listed"]),6)
        listed_profit_high=round(high_profit*int(r["listed"]),6)
        free_profit_low=round(low_profit*free,6)
        free_profit_high=round(high_profit*free,6)
        kb=[[InlineKeyboardButton(f"🏷️ List {free} items",callback_data=f"rs:list:{r['id']}")] if free else []]
        kb.append([InlineKeyboardButton("⬅️ My Products",callback_data="m:products"),InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")])
        details=(
            f"<b>📦 {html.escape(str(r['title']))}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Owned: <b>{int(r['remaining_quantity'])}</b>  •  Already listed: <b>{int(r['listed'])}</b>\n"
            f"Free to list: <b>{free}</b>\n"
            + (f"Purchase orders: <b>{int(r['shop_purchase_orders'])}</b> • Items bought: <b>{int(r['shop_units_bought'])}</b>\n"
               f"Items sold: <b>{int(r['shop_units_sold'])}</b>\n" if item_level==2 else "")
            + "\n"
            "<b>PRICE & PROFIT</b>\n"
            f"Your cost: {unit_cost:g} USDT / item\n"
            + (f"Resale margin: {low_profit/unit_cost*100:.2f}%–{high_profit/unit_cost*100:.2f}% (random per Level 2 listing)\n"
               if item_level==2 and unit_cost else f"Resale price: {price:g} USDT / item\n")
            + (f"💚 <b>Expected profit: +{low_profit:g} to +{high_profit:g} USDT / sold item</b>\n"
               if item_level==2 else f"💚 <b>Expected profit: +{low_profit:g} USDT / sold item</b>\n")
            + (f"🟢 Listed stock potential: <b>+{listed_profit_low:g} USDT</b>\n"
               f"🟢 Free stock potential: <b>+{free_profit_low:g} USDT</b>\n"
               if item_level==1 else
               f"🟢 Listed stock potential: <b>+{listed_profit_low:g} to +{listed_profit_high:g} USDT</b>\n"
               f"🟢 Free stock potential: <b>+{free_profit_low:g} to +{free_profit_high:g} USDT</b>\n")
            + "<i>Profit is credited after a sale is completed.</i>"
        )
        await update.effective_message.reply_text(details,reply_markup=InlineKeyboardMarkup([x for x in kb if x]),parse_mode="HTML")

async def safe_callback_answer(query, *args, **kwargs):
    """Answer Telegram callbacks without crashing when Telegram has expired the query."""
    from telegram.error import BadRequest, TimedOut, NetworkError
    try:
        return await query.answer(*args, **kwargs)
    except (BadRequest, TimedOut, NetworkError) as exc:
        message = str(exc).lower()
        if isinstance(exc, BadRequest) and ("query is too old" in message or "query id is invalid" in message):
            log.info("Ignoring expired Telegram callback query")
            return None
        log.warning("Could not answer Telegram callback: %s", exc)
        return None


async def resale_callback(update,ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    q=update.callback_query; resale_setup()
    parts=q.data.split(":"); action=parts[1]
    if action=="listmenu":
        await safe_callback_answer(q, ); await list_inventory(update,ctx); return
    if action=="list":
        inv_id=int(parts[2]); c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            inv=c.execute("""SELECT i.*,p.title,p.level_required FROM central_inventory i
                JOIN central_catalog p ON p.id=i.catalog_id WHERE i.id=? AND i.owner_id=?""",
                (inv_id,q.from_user.id)).fetchone()
            if not inv: c.rollback(); await safe_callback_answer(q, "Inventory not found",show_alert=True); return
            seller_shop=c.execute("SELECT id,owner_id,status,level FROM shops WHERE id=?",(inv["shop_id"],)).fetchone()
            if not seller_shop or int(seller_shop["owner_id"])!=q.from_user.id or seller_shop["status"]!="approved" or int(seller_shop["level"])<int(inv["level_required"]):
                c.rollback(); await safe_callback_answer(q,"Inventory level does not match your approved shop",show_alert=True); return
            listed=c.execute("SELECT COALESCE(SUM(remaining_quantity),0) FROM resale_listings WHERE inventory_id=? AND status='active'",(inv_id,)).fetchone()[0]
            qty=int(inv["remaining_quantity"])-int(listed)
            if qty<=0: c.rollback(); await safe_callback_answer(q, "No unlisted stock left",show_alert=True); return
            price=_resale_ask_price(c,float(inv["unit_cost_usdt"]),int(inv["level_required"]))
            c.execute("""INSERT INTO resale_listings(inventory_id,owner_id,shop_id,catalog_id,quantity,remaining_quantity,unit_cost_usdt,ask_price_usdt,status,created_at)
              VALUES(?,?,?,?,?,?,?,?, 'active',?)""",(inv_id,q.from_user.id,inv["shop_id"],inv["catalog_id"],qty,qty,inv["unit_cost_usdt"],price,v4.now()))
            lid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,q.from_user.id,"resale_listed",f"Listing #{lid}; {inv['title']}; qty {qty}; {price} USDT",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Resale listing failed"); await q.message.reply_text("Listing failed safely."); return
        finally: c.close()
        await safe_callback_answer(q, "Listed")
        cost=float(inv["unit_cost_usdt"])
        expected_profit=round((price-cost)*qty,6)
        await q.message.reply_text(
            f"<b>✅ STOCK LISTED</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"<b>{html.escape(str(inv['title']))}</b> × {qty}\n"
            f"Cost: {cost:g} USDT / item\nAsking price: {price:g} USDT / item\n"
            f"💚 Expected profit if all sell: <b>+{expected_profit:g} USDT</b>\n\n"
            f"Your listing is now available in the Level {int(inv['level_required'])} resale market.",
            parse_mode="HTML")
        try: await ctx.bot.send_message(v4.ADMIN_ID,f"📣 NEW RESALE LISTING #{lid}\n{inv['title']} ×{qty}\nSeller: {q.from_user.id} | Shop #{inv['shop_id']}\nAsking: {price:g} USDT each",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Review / Buy",callback_data=f"rs:review:{lid}")],[InlineKeyboardButton("⬅️ System Panel",callback_data="adm:home")]]))
        except Exception: log.exception("System listing notification failed")
        return
    if action=="reject":
        if not v4.admin(q.from_user.id): await safe_callback_answer(q, "System only",show_alert=True); return
        lid=int(parts[2])
        c=v4.db()
        listing=c.execute("SELECT owner_id,status FROM resale_listings WHERE id=?",(lid,)).fetchone()
        c.close()
        if not listing or listing["status"]!="active":
            await safe_callback_answer(q,"Listing not found or already reviewed",show_alert=True); return
        ctx.user_data["pending_admin_rejection"]={"kind":"central_listing","id":lid}
        await safe_callback_answer(q)
        await q.message.reply_text(
            f"✍️ Enter the rejection reason for resale listing #{lid}. "
            "The seller will receive this reason. Type CANCEL to stop.")
        return
    if action=="review":
        if not v4.admin(q.from_user.id): await safe_callback_answer(q, "System only",show_alert=True); return
        lid=int(parts[2]); c=v4.db()
        r=c.execute("""SELECT l.*,p.title,s.shop_name FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id JOIN shops s ON s.id=l.shop_id WHERE l.id=?""",(lid,)).fetchone(); c.close()
        if not r: await safe_callback_answer(q, "Listing not found",show_alert=True); return
        if r["status"]!="active" or int(r["remaining_quantity"])<1:
            await safe_callback_answer(q, "This listing is no longer available",show_alert=True); return
        ctx.user_data["admin_resale_offer"]={"listing_id":lid}
        await safe_callback_answer(q, )
        suggested_price=float(r["ask_price_usdt"])
        available_qty=int(r["remaining_quantity"])
        await q.message.reply_text(
            f"<b>🛒 RESALE LISTING #{lid}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"<b>{html.escape(str(r['title']))}</b>\n"
            f"Seller: {html.escape(str(r['shop_name']))}\n"
            f"Available: <b>{available_qty}</b> item(s)\n"
            f"Asking price: <b>{suggested_price:g} USDT / item</b>\n\n"
            "<b>SYSTEM OFFER</b>\nSend: <code>PRICE QUANTITY</code>\n"
            f"Buy all at asking price: <code>{suggested_price:g} {available_qty}</code>\n"
            f"<i>Maximum quantity: {available_qty}. Type CANCEL to stop.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ REJECT LISTING",callback_data=f"rs:reject:{lid}")],
                [InlineKeyboardButton("⬅️ System Panel",callback_data="adm:home")]]),parse_mode="HTML")
        return
    if action=="market":
        shop=v4.shop_for(q.from_user.id)
        if not shop or shop["status"]!="approved" or int(shop["level"]) not in (1,2):
            await safe_callback_answer(q, "Approved Level 1 or Level 2 shop required",show_alert=True); return
        level=int(shop["level"])
        c=v4.db()
        rows=c.execute("""SELECT l.id,l.remaining_quantity,l.ask_price_usdt,p.title,s.shop_name
            FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id
            JOIN shops s ON s.id=l.shop_id
            WHERE l.status='active' AND l.remaining_quantity>0 AND l.owner_id<>?
              AND s.status='approved' AND s.level>=p.level_required AND p.level_required<=?
            ORDER BY l.id DESC LIMIT 30""",(q.from_user.id,level)).fetchall()
        c.close()
        await safe_callback_answer(q)
        if not rows: await q.message.reply_text(f"<b>🛍 LEVEL {level} + LOWER RESALE MARKET</b>\nNo merchant listings available right now.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML"); return
        for r in rows:
            buttons=[[InlineKeyboardButton("Buy 1",callback_data=f"rs:buy:{r['id']}:1")]]
            if level>=2 and int(r["remaining_quantity"])>=5:
                buttons[0].append(InlineKeyboardButton("Buy 5",callback_data=f"rs:buy:{r['id']}:5"))
            buttons.append([InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")])
            await q.message.reply_text(
                f"<b>🛍 {html.escape(str(r['title']))}</b>\n"
                f"Seller: {html.escape(str(r['shop_name']))}\n"
                f"Price: <b>{float(r['ask_price_usdt']):g} USDT / item</b>\n"
                f"Available: <b>{int(r['remaining_quantity'])}</b>",
                reply_markup=InlineKeyboardMarkup(buttons),parse_mode="HTML")
        return
    if action=="buy":
        shop=v4.shop_for(q.from_user.id)
        if not shop or shop["status"]!="approved" or int(shop["level"]) not in (1,2):
            await safe_callback_answer(q, "Approved Level 1 or Level 2 shop required",show_alert=True); return
        buyer_level=int(shop["level"])
        lid,qty=int(parts[2]),int(parts[3])
        if buyer_level==1 and qty>1:
            await safe_callback_answer(q,"Level 1 purchase limit: only 1 item per order.",show_alert=True)
            return
        c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            l=c.execute("""SELECT l.*,p.level_required FROM resale_listings l
                JOIN central_catalog p ON p.id=l.catalog_id
                WHERE l.id=? AND l.status='active'""",(lid,)).fetchone()
            if not l or int(l["owner_id"])==q.from_user.id or qty<1 or qty>int(l["remaining_quantity"]):
                c.rollback(); await safe_callback_answer(q, "Listing/quantity unavailable",show_alert=True); return
            listing_level=int(l["level_required"])
            if listing_level<1 or listing_level>buyer_level:
                c.rollback(); await safe_callback_answer(q, "This listing requires a higher shop level",show_alert=True); return
            allowed,buyer_warning=v4.shop_order_gate(c,shop["id"])
            if not allowed:
                c.rollback()
                await safe_callback_answer(q,"System warning shown" if "SYSTEM WARNING" in str(buyer_warning) else buyer_warning,show_alert=True)
                if "SYSTEM WARNING" in str(buyer_warning):
                    await q.message.reply_text(buyer_warning)
                return
            seller_shop=c.execute("SELECT status,level,control_status FROM shops WHERE id=?",(l["shop_id"],)).fetchone()
            if not seller_shop or seller_shop["status"]!="approved" or str(seller_shop["control_status"] or "active").lower()!="active" or int(seller_shop["level"])<listing_level:
                c.rollback(); await safe_callback_answer(q, "Only approved merchants with this level or higher can sell here",show_alert=True); return
            total=round(float(l["ask_price_usdt"])*qty,6); bal=balance(c,q.from_user.id)
            if bal+1e-9<total: c.rollback(); await safe_callback_answer(q, "Insufficient USDT balance",show_alert=True); return
            purchase_allowed,purchase_message=consume_extra_purchase_units(
                c,shop["id"],q.from_user.id,qty)
            if not purchase_allowed:
                c.rollback(); await safe_callback_answer(q,purchase_message,show_alert=True); return
            changed=c.execute("UPDATE resale_listings SET remaining_quantity=remaining_quantity-?,status=CASE WHEN remaining_quantity-?=0 THEN 'sold' ELSE status END WHERE id=? AND status='active' AND remaining_quantity>=?",(qty,qty,lid,qty))
            if changed.rowcount != 1:
                c.rollback(); await safe_callback_answer(q, "Listing stock changed. Please refresh the market.",show_alert=True); return
            cur=c.execute("""INSERT INTO central_orders(catalog_id,buyer_shop_id,buyer_owner_id,quantity,unit_price_usdt,total_usdt,status,created_at) VALUES(?,?,?,?,?,?,'paid',?)""",(l["catalog_id"],shop["id"],q.from_user.id,qty,l["ask_price_usdt"],total,v4.now())); oid=cur.lastrowid
            changed=c.execute("UPDATE central_inventory SET remaining_quantity=remaining_quantity-? WHERE id=? AND owner_id=? AND remaining_quantity>=?",(qty,l["inventory_id"],l["owner_id"],qty))
            if changed.rowcount != 1:
                raise RuntimeError("Seller inventory changed; resale purchase rolled back")
            c.execute("""INSERT INTO central_inventory(owner_id,shop_id,catalog_id,order_id,quantity,remaining_quantity,unit_cost_usdt,status,created_at) VALUES(?,?,?,?,?,?,?,'owned',?)""",(q.from_user.id,shop["id"],l["catalog_id"],oid,qty,qty,l["ask_price_usdt"],v4.now()))
            c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_purchase_debit',?,'USDT',?,?)""",(oid,shop["id"],q.from_user.id,total,f"Resale purchase listing #{lid}",v4.now()))
            profit=round((float(l["ask_price_usdt"])-float(l["unit_cost_usdt"]))*qty,6)
            c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_sale_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],total,f"Resale sale listing #{lid}",v4.now()))
            if profit!=0: c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_profit_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],profit,f"Profit on resale listing #{lid}",v4.now()))
            record_completed_pairs(c,l["shop_id"],l["owner_id"],qty,"merchant_resale",oid)
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,q.from_user.id,"resale_bought",f"Listing #{lid}; qty {qty}; total {total} USDT",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Resale purchase failed"); await q.message.reply_text("Purchase failed safely; no changes committed."); return
        finally: c.close()
        await safe_callback_answer(q, "Purchased")
        await q.message.reply_text(
            f"<b>✅ PURCHASE CONFIRMED</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Level: <b>{buyer_level}</b>\nQuantity: <b>{qty}</b> item(s)\nTotal paid: <b>{total:g} USDT</b>\nOrder: <code>#{oid}</code>" + (f"\n⚠️ SYSTEM WARNING: {html.escape(buyer_warning)}" if buyer_warning else ""),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Marketplace",callback_data="rs:market")],[InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")]]),
            parse_mode="HTML")
        try: await ctx.bot.send_message(l["owner_id"],f"🎉 Your resale listing #{lid} sold {qty} item(s). Sale proceeds: {total:g} USDT; recorded profit: {max(0,profit):g} USDT.")
        except Exception: log.exception("Resale seller notification failed")
        return

async def admin_resale_text(update,ctx):
    data=ctx.user_data.get("admin_resale_offer")
    if not data or not v4.admin(update.effective_user.id): return False
    raw=(update.message.text or "").strip()
    if raw.lower()=="cancel":
        ctx.user_data.pop("admin_resale_offer",None); await update.message.reply_text("Offer cancelled.",reply_markup=__import__('telegram').InlineKeyboardMarkup([[__import__('telegram').InlineKeyboardButton("⬅️ System Panel",callback_data="adm:home")]])); return True
    try:
        price_s,qty_s=raw.split(); price=round(float(price_s),6); qty=int(qty_s)
        if price<=0 or qty<1 or qty>100000: raise ValueError()
    except Exception:
        await update.message.reply_text("Format: PRICE QUANTITY (example: 2.5 10), or CANCEL."); return True
    lid=int(data["listing_id"]); c=v4.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        l=c.execute("SELECT * FROM resale_listings WHERE id=? AND status='active'",(lid,)).fetchone()
        if not l or qty>int(l["remaining_quantity"]): c.rollback(); await update.message.reply_text("Listing no longer available in that quantity."); return True
        total=round(price*qty,6); profit=round((price-float(l["unit_cost_usdt"]))*qty,6)
        changed=c.execute("UPDATE resale_listings SET remaining_quantity=remaining_quantity-?,status=CASE WHEN remaining_quantity-?=0 THEN 'sold' ELSE status END WHERE id=? AND status='active' AND remaining_quantity>=?",(qty,qty,lid,qty))
        if changed.rowcount != 1:
            c.rollback(); await update.message.reply_text("Listing stock changed. Refresh the System Resale Market."); return True
        cur=c.execute("""INSERT INTO admin_resale_orders(listing_id,seller_owner_id,seller_shop_id,catalog_id,quantity,unit_price_usdt,total_usdt,status,created_at) VALUES(?,?,?,?,?,?,?,'purchased',?)""",(lid,l["owner_id"],l["shop_id"],l["catalog_id"],qty,price,total,v4.now())); oid=cur.lastrowid
        changed=c.execute("UPDATE central_inventory SET remaining_quantity=remaining_quantity-? WHERE id=? AND owner_id=? AND remaining_quantity>=?",(qty,l["inventory_id"],l["owner_id"],qty))
        if changed.rowcount != 1:
            raise RuntimeError("Seller inventory changed; System purchase rolled back")
        c.execute("""INSERT INTO admin_resale_inventory(admin_id,catalog_id,order_id,quantity,remaining_quantity,unit_cost_usdt,created_at) VALUES(?,?,?,?,?,?,?)""",(v4.ADMIN_ID,l["catalog_id"],oid,qty,qty,price,v4.now()))
        c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_sale_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],total,f"System purchase listing #{lid}",v4.now()))
        if profit>0: c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_profit_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],profit,f"Profit on System purchase #{oid}",v4.now()))
        record_completed_pairs(c,l["shop_id"],l["owner_id"],qty,"admin_resale",oid)
        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,l["owner_id"],"admin_resale_purchased",f"Order #{oid}; listing #{lid}; qty {qty}; offer {price:g}; total {total:g} USDT; seller margin {profit:g}",v4.now()))
        c.commit()
    except Exception:
        c.rollback(); log.exception("System resale purchase failed"); await update.message.reply_text("System purchase failed safely. No changes committed."); return True
    finally: c.close()
    ctx.user_data.pop("admin_resale_offer",None)
    await update.message.reply_text(f"✅ SYSTEM PURCHASE COMPLETE\nOrder #{oid}\nQuantity: {qty}\nOffer: {price:g} USDT each\nSeller paid: {total:g} USDT\nSeller profit: {profit:g} USDT (negative means sold below cost).")
    try: await ctx.bot.send_message(l["owner_id"],f"✅ Customer purchased your listing #{lid}: {qty} item(s) at {price:g} USDT each. Total credited: {total:g} USDT. Margin vs your recorded cost: {profit:g} USDT.")
    except Exception: log.exception("System resale seller notification failed")
    return True

async def admin_resale_market(update,ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    if not v4.admin(update.effective_user.id): return
    resale_setup(); c=v4.db()
    rows=c.execute("""SELECT l.id,l.owner_id,l.shop_id,l.remaining_quantity,l.ask_price_usdt,p.title,s.shop_name FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id JOIN shops s ON s.id=l.shop_id WHERE l.status='active' AND l.remaining_quantity>0 ORDER BY l.id DESC LIMIT 50""").fetchall(); c.close()
    await update.effective_message.reply_text(
        "<b>🛒 SYSTEM RESALE MARKET</b>\n━━━━━━━━━━━━━━━━━━\nReview merchant listings and make a USDT offer.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ System Panel",callback_data="adm:home")]]),parse_mode="HTML")
    for r in rows:
        await update.effective_message.reply_text(
            f"<b>#{r['id']} {html.escape(str(r['title']))}</b>\n"
            f"Seller: {html.escape(str(r['shop_name']))} | Shop #{r['shop_id']}\n"
            f"Available: <b>{int(r['remaining_quantity'])}</b>\n"
            f"Asking: <b>{float(r['ask_price_usdt']):g} USDT / item</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Review / Buy",callback_data=f"rs:review:{r['id']}")],[InlineKeyboardButton("⬅️ System Panel",callback_data="adm:home")]]),parse_mode="HTML")
    if not rows: await update.effective_message.reply_text("No active merchant resale listings.")
