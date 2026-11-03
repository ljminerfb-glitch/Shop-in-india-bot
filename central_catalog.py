"""Shared Level-1 central catalogue paid from owners' USDT wallet."""
from pathlib import Path
import logging
import html
import bot_v4 as v4

log = logging.getLogger("central_catalog")
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
        for key,title,desc,img in PRODUCTS:
            conn.execute("""INSERT OR IGNORE INTO central_catalog
              (product_key,title,description,price_usdt,stock,image_path,active,level_required,created_at,updated_at)
              VALUES(?,?,?,0,0,?,1,1,?,?)""",(key,title,desc,img,v4.now(),v4.now()))
        conn.commit()
    finally:
        if own: conn.close()

def balance(c, owner_id):
    row=c.execute("""SELECT
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type='deposit_credit' THEN amount ELSE 0 END),0) deposits,
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type IN ('central_purchase_debit','resale_purchase_debit','withdrawal_debit') THEN amount ELSE 0 END),0) debits,
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type IN ('central_purchase_refund_credit','resale_sale_credit','level_activation_wallet_credit') THEN amount ELSE 0 END),0) refunds
      FROM ledger WHERE owner_id=?""",(owner_id,)).fetchone()
    return round(float(row["deposits"] or 0)-float(row["debits"] or 0)+float(row["refunds"] or 0),6)

def catalogue_text(c):
    rows=c.execute("SELECT id,title,price_usdt,stock,active FROM central_catalog ORDER BY id").fetchall()
    return "🛍 CENTRAL LEVEL 1 CATALOGUE\n\n"+ "\n\n".join(
      f"#{r['id']} {r['title']}\nPrice: {float(r['price_usdt']):g} USDT | Stock: {r['stock']} | Active: {'Yes' if r['active'] else 'No'}"
      for r in rows)

async def admin_catalog(update,ctx):
    if update.effective_user.id != v4.ADMIN_ID:
        await update.effective_message.reply_text("Admin only."); return
    setup()
    c=v4.db()
    try:
        rows=c.execute("SELECT id,title,price_usdt,stock,active FROM central_catalog ORDER BY id").fetchall()
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard=[]
        for r in rows:
            keyboard.append([InlineKeyboardButton(
                f"#{r['id']} {r['title']} | {float(r['price_usdt']):g} USDT | Stock {r['stock']}",
                callback_data=f"cc:view:{r['id']}")])
        keyboard += [[InlineKeyboardButton("📦 Manage Central Orders",callback_data="cc:orders")],
                     [InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]
        await update.effective_message.reply_text(
            "<b>🛠 CENTRAL CATALOGUE</b>\n━━━━━━━━━━━━━━━━━━\nChoose a product to manage its USDT price, shared stock or availability.",
            reply_markup=InlineKeyboardMarkup(keyboard),parse_mode="HTML")
    finally: c.close()

async def set_price(update,ctx):
    if update.effective_user.id != v4.ADMIN_ID: return
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
    if update.effective_user.id != v4.ADMIN_ID: return
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
    if update.effective_user.id != v4.ADMIN_ID: return
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
    if not shop or shop["status"]!="approved" or int(shop["level"])!=1:
        await update.effective_message.reply_text("Approved Level 1 shop required. Higher and lower levels cannot use this catalogue."); return
    try:
        pid=int(ctx.args[0]); qty=int(ctx.args[1])
        if pid<1 or qty<1 or qty>1000: raise ValueError()
    except ValueError:
        await update.effective_message.reply_text("Invalid product ID or quantity."); return
    c=v4.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        p=c.execute("SELECT * FROM central_catalog WHERE id=? AND active=1 AND level_required=1",(pid,)).fetchone()
        if not p or float(p["price_usdt"])<=0:
            c.rollback(); await update.effective_message.reply_text("Product unavailable. Admin has not set its USDT price yet."); return
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
                "After sending, open Deposit and submit your transaction screenshot for Admin verification."
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
        # Every approved Level 1 buyer gets an automatic resale listing at cost + 0.8 USDT/item.
        resale_setup(c)
        ask_price=round(float(p["price_usdt"])+0.8,6)
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
    await update.effective_message.reply_text(f"✅ Purchase successful\nOrder #{oid}\n{p['title']} × {qty}\nTotal paid: {total:g} USDT\nRemaining shared stock: {remaining_stock}\nRemaining USDT wallet: {balance_after_text(total,available)}")
    try:
        await ctx.bot.send_message(v4.ADMIN_ID,f"🛒 CENTRAL ORDER #{oid}\n{p['title']} × {qty}\nOwner: {update.effective_user.id} | Shop #{shop['id']}\nPaid: {total:g} USDT")
    except Exception: log.exception("Admin central order notification failed")

def balance_after_text(total, before):
    return f"{max(0.0, before-total):.6f}".rstrip("0").rstrip(".")

async def orders(update,ctx):
    if update.effective_user.id != v4.ADMIN_ID: return
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    c=v4.db()
    rows=c.execute("""SELECT o.id,o.buyer_owner_id,o.buyer_shop_id,o.quantity,o.total_usdt,o.status,o.created_at,p.title
      FROM central_orders o JOIN central_catalog p ON p.id=o.catalog_id ORDER BY o.id DESC LIMIT 50""").fetchall()
    c.close()
    orders_text="<b>📦 CENTRAL CATALOGUE ORDERS</b>\n━━━━━━━━━━━━━━━━━━\n"
    orders_text+=("<i>Orders are charged in USDT. Fulfil after arranging delivery; refunds restore the wallet and shared stock.</i>\n\n"+
      "\n\n".join(
        f"<b>#{r['id']} {html.escape(str(r['title']))} ×{r['quantity']}</b>\n"
        f"Owner: <code>{r['buyer_owner_id']}</code> • Shop: #{r['buyer_shop_id']}\n"
        f"Amount: <b>{r['total_usdt']:g} USDT</b> • {'🟢' if r['status']=='fulfilled' else '🔴' if r['status']=='refunded' else '🟡'} {html.escape(str(r['status']).upper())}\n"
        f"<code>{html.escape(str(r['created_at']))}</code>" for r in rows)
      if rows else "No central orders yet.")
    await update.effective_message.reply_text(orders_text[:3900],parse_mode="HTML")
    for r in rows:
        buttons=[]
        if r["status"]=="paid":
            buttons=[[InlineKeyboardButton("✅ Mark Fulfilled",callback_data=f"cc:fulfill:{r['id']}"),
                      InlineKeyboardButton("↩️ Refund + Restock",callback_data=f"cc:refund:{r['id']}")]]
        buttons.append([InlineKeyboardButton("⬅️ Central Catalogue",callback_data="adm:central"),
                        InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")])
        await update.effective_message.reply_text(
            f"Central order #{r['id']} — {r['status'].upper()}",
            reply_markup=InlineKeyboardMarkup(buttons))

async def admin_callback(update,ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    q=update.callback_query
    if q.from_user.id != v4.ADMIN_ID:
        await q.answer("Admin only",show_alert=True); return
    await q.answer()
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
           InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
        ])
        active_label="🟢 ACTIVE" if row["active"] else "🔴 OFF"
        await q.message.reply_text(
          f"<b>🛍 {html.escape(str(row['title']))}</b>\n━━━━━━━━━━━━━━━━━━\n"
          f"Price: <b>{float(row['price_usdt']):g} USDT</b>\nShared stock: <b>{row['stock']}</b>\nStatus: <b>{active_label}</b>",
          reply_markup=kb,parse_mode="HTML"); return
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
                c.execute("UPDATE central_orders SET status='refunded' WHERE id=? AND status='paid'",(oid,))
                if c.execute("SELECT changes()").fetchone()[0]!=1:
                    raise RuntimeError("Order already processed")
                c.execute("UPDATE central_catalog SET stock=stock+?,updated_at=? WHERE id=?",
                          (order["quantity"],v4.now(),order["catalog_id"]))
                c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
                  VALUES(?,?,?,'central_purchase_refund_credit',?,'USDT',?,?)""",
                  (oid,order["buyer_shop_id"],order["buyer_owner_id"],order["total_usdt"],f"Refund for central order #{oid}",v4.now()))
                label="refunded + stock restored"
                details=f"Central order #{oid} refunded {order['total_usdt']} USDT; stock restored"
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
    if not shop or shop["status"] != "approved" or int(shop["level"]) != 1:
        await update.callback_query.message.reply_text(
            "This central catalogue is available only to approved Level 1 shops.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data="m:dashboard")]]))
        return
    setup()
    c=v4.db()
    try:
        c.execute("""INSERT INTO catalogue_visits(owner_id,visits,last_seen) VALUES(?,1,?)
          ON CONFLICT(owner_id) DO UPDATE SET visits=visits+1,last_seen=excluded.last_seen""",
          (update.effective_user.id,v4.now()))
        total_visitors=c.execute("SELECT COUNT(*) FROM catalogue_visits").fetchone()[0]
        rows=c.execute("SELECT * FROM central_catalog WHERE active=1 AND level_required=1 ORDER BY id").fetchall()
        c.commit()
    finally: c.close()
    await update.callback_query.message.reply_text(
        f"<b>🛍 CENTRAL LEVEL 1 CATALOGUE</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"12 products • {total_visitors} unique visitors\n"
        "<i>Margin figures are references, not guaranteed income. Actual profit is recorded after a completed sale.</i>",
        parse_mode="HTML")
    for p in rows:
        price=float(p["price_usdt"] or 0); stock=int(p["stock"] or 0)
        caption=(f"<b>#{p['id']} {html.escape(str(p['title']))}</b>\n"
                 f"{html.escape(str(p['description'] or ''))}\n\n"
                 f"Price: <b>{price:g} USDT</b>\n"
                 f"Available stock: <b>{stock}</b>\n"+
                 ("Select a quantity below to buy." if price>0 and stock>0 else "🔴 Unavailable — Admin must set price and stock."))
        quantities=[n for n in (1,2,5) if n<=stock] or [1]
        buttons=[[InlineKeyboardButton(f"🛒 Buy × {n}",callback_data=f"ccbuy:{p['id']}:{n}")
                  for n in quantities]]
        buttons.append([InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")])
        kb=InlineKeyboardMarkup(buttons)
        image_path=Path(__file__).resolve().parent/(p["image_path"] or "")
        try:
            if image_path.is_file():
                with image_path.open("rb") as photo:
                    await ctx.bot.send_photo(update.effective_chat.id,photo=photo,caption=caption[:1024],reply_markup=kb,parse_mode="HTML")
            else:
                await ctx.bot.send_message(update.effective_chat.id,caption+"\n⚠️ Product photo unavailable.",reply_markup=kb,parse_mode="HTML")
        except Exception:
            log.exception("Could not send catalogue item")
            await ctx.bot.send_message(update.effective_chat.id,caption,reply_markup=kb,parse_mode="HTML")


async def owner_buy_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    try:
        _,pid_s,qty_s=q.data.split(":")
        ctx.args=[pid_s,qty_s]
        await buy(update,ctx)
    except Exception:
        log.exception("Catalogue button purchase failed")
        await q.message.reply_text("Purchase could not be completed. Please retry with /centralbuy PRODUCT_ID QUANTITY.")


# --- Owner resale listings and Admin direct-purchase market ---
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
    if update.effective_user.id==v4.ADMIN_ID: return
    shop=v4.shop_for(update.effective_user.id)
    if not shop or shop["status"]!="approved" or int(shop["level"])!=1:
        await update.effective_message.reply_text("<b>🔒 LEVEL 1 ACCESS REQUIRED</b>\nYour shop must be approved and active at Level 1.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML"); return
    resale_setup(); c=v4.db()
    rows=c.execute("""SELECT i.id,i.catalog_id,i.remaining_quantity,i.unit_cost_usdt,p.title,
      COALESCE((SELECT SUM(l.remaining_quantity) FROM resale_listings l WHERE l.inventory_id=i.id AND l.status='active'),0) listed
      FROM central_inventory i JOIN central_catalog p ON p.id=i.catalog_id
      WHERE i.owner_id=? AND i.remaining_quantity>0 ORDER BY i.id DESC""",(update.effective_user.id,)).fetchall(); c.close()
    if not rows:
        await update.effective_message.reply_text("<b>📦 LIST MY STOCK</b>\nNo available inventory to list right now.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML"); return
    for r in rows:
        free=max(0,int(r["remaining_quantity"])-int(r["listed"]))
        price=round(float(r["unit_cost_usdt"])+0.8,6)
        unit_cost=round(float(r["unit_cost_usdt"]),6)
        profit_each=round(price-unit_cost,6)
        listed_profit=round(profit_each*int(r["listed"]),6)
        free_profit=round(profit_each*free,6)
        kb=[[InlineKeyboardButton(f"🏷️ List {free} items",callback_data=f"rs:list:{r['id']}")] if free else []]
        kb.append([InlineKeyboardButton("⬅️ My Products",callback_data="m:products"),InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")])
        details=(
            f"<b>📦 {html.escape(str(r['title']))}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Owned: <b>{int(r['remaining_quantity'])}</b>  •  Already listed: <b>{int(r['listed'])}</b>\n"
            f"Free to list: <b>{free}</b>\n\n"
            "<b>PRICE & PROFIT</b>\n"
            f"Your cost: {unit_cost:g} USDT / item\n"
            f"Resale price: {price:g} USDT / item\n"
            f"💚 <b>Expected profit: +{profit_each:g} USDT / sold item</b>\n"
            f"🟢 Listed stock potential: <b>+{listed_profit:g} USDT</b>\n"
            f"🟢 Free stock potential: <b>+{free_profit:g} USDT</b>\n\n"
            "<i>Profit is credited after a sale is completed.</i>"
        )
        await update.effective_message.reply_text(details,reply_markup=InlineKeyboardMarkup([x for x in kb if x]),parse_mode="HTML")

async def resale_callback(update,ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    q=update.callback_query; resale_setup()
    parts=q.data.split(":"); action=parts[1]
    if action=="listmenu":
        await q.answer(); await list_inventory(update,ctx); return
    if action=="list":
        inv_id=int(parts[2]); c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            inv=c.execute("""SELECT i.*,p.title FROM central_inventory i JOIN central_catalog p ON p.id=i.catalog_id WHERE i.id=? AND i.owner_id=?""",(inv_id,q.from_user.id)).fetchone()
            if not inv: c.rollback(); await q.answer("Inventory not found",show_alert=True); return
            listed=c.execute("SELECT COALESCE(SUM(remaining_quantity),0) FROM resale_listings WHERE inventory_id=? AND status='active'",(inv_id,)).fetchone()[0]
            qty=int(inv["remaining_quantity"])-int(listed)
            if qty<=0: c.rollback(); await q.answer("No unlisted stock left",show_alert=True); return
            price=round(float(inv["unit_cost_usdt"])+0.8,6)
            c.execute("""INSERT INTO resale_listings(inventory_id,owner_id,shop_id,catalog_id,quantity,remaining_quantity,unit_cost_usdt,ask_price_usdt,status,created_at)
              VALUES(?,?,?,?,?,?,?,?, 'active',?)""",(inv_id,q.from_user.id,inv["shop_id"],inv["catalog_id"],qty,qty,inv["unit_cost_usdt"],price,v4.now()))
            lid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,q.from_user.id,"resale_listed",f"Listing #{lid}; {inv['title']}; qty {qty}; {price} USDT",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Resale listing failed"); await q.message.reply_text("Listing failed safely."); return
        finally: c.close()
        await q.answer("Listed")
        cost=float(inv["unit_cost_usdt"])
        expected_profit=round((price-cost)*qty,6)
        await q.message.reply_text(
            f"<b>✅ STOCK LISTED</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"<b>{html.escape(str(inv['title']))}</b> × {qty}\n"
            f"Cost: {cost:g} USDT / item\nAsking price: {price:g} USDT / item\n"
            f"💚 Expected profit if all sell: <b>+{expected_profit:g} USDT</b>\n\n"
            "Your listing is now available in the Level 1 resale market.",
            parse_mode="HTML")
        try: await ctx.bot.send_message(v4.ADMIN_ID,f"📣 NEW RESALE LISTING #{lid}\n{inv['title']} ×{qty}\nSeller: {q.from_user.id} | Shop #{inv['shop_id']}\nAsking: {price:g} USDT each",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Review / Buy",callback_data=f"rs:review:{lid}")],[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        except Exception: log.exception("Admin listing notification failed")
        return
    if action=="review":
        if q.from_user.id!=v4.ADMIN_ID: await q.answer("Admin only",show_alert=True); return
        lid=int(parts[2]); c=v4.db()
        r=c.execute("""SELECT l.*,p.title,s.shop_name FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id JOIN shops s ON s.id=l.shop_id WHERE l.id=?""",(lid,)).fetchone(); c.close()
        if not r: await q.answer("Listing not found",show_alert=True); return
        if r["status"]!="active" or int(r["remaining_quantity"])<1:
            await q.answer("This listing is no longer available",show_alert=True); return
        ctx.user_data["admin_resale_offer"]={"listing_id":lid}
        await q.answer()
        suggested_price=float(r["ask_price_usdt"])
        available_qty=int(r["remaining_quantity"])
        await q.message.reply_text(
            f"<b>🛒 RESALE LISTING #{lid}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"<b>{html.escape(str(r['title']))}</b>\n"
            f"Seller: {html.escape(str(r['shop_name']))}\n"
            f"Available: <b>{available_qty}</b> item(s)\n"
            f"Asking price: <b>{suggested_price:g} USDT / item</b>\n\n"
            "<b>ADMIN OFFER</b>\nSend: <code>PRICE QUANTITY</code>\n"
            f"Buy all at asking price: <code>{suggested_price:g} {available_qty}</code>\n"
            f"<i>Maximum quantity: {available_qty}. Type CANCEL to stop.</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]),parse_mode="HTML")
        return
    if action=="market":
        shop=v4.shop_for(q.from_user.id)
        if not shop or shop["status"]!="approved" or int(shop["level"])!=1:
            await q.answer("Approved Level 1 shop required",show_alert=True); return
        c=v4.db(); rows=c.execute("""SELECT l.id,l.remaining_quantity,l.ask_price_usdt,p.title,s.shop_name FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id JOIN shops s ON s.id=l.shop_id WHERE l.status='active' AND l.remaining_quantity>0 AND l.owner_id<>? AND s.status='approved' AND s.level=1 ORDER BY l.id DESC LIMIT 30""",(q.from_user.id,)).fetchall(); c.close()
        await q.answer()
        if not rows: await q.message.reply_text("<b>🛍 LEVEL 1 RESALE MARKET</b>\nNo merchant listings available right now.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML"); return
        for r in rows:
            buttons=[[InlineKeyboardButton("Buy 1",callback_data=f"rs:buy:{r['id']}:1")]]
            if int(r["remaining_quantity"])>=5:
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
        if not shop or shop["status"]!="approved" or int(shop["level"])!=1: await q.answer("Approved Level 1 shop required",show_alert=True); return
        lid,qty=int(parts[2]),int(parts[3])
        c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            l=c.execute("SELECT * FROM resale_listings WHERE id=? AND status='active'",(lid,)).fetchone()
            if not l or int(l["owner_id"])==q.from_user.id or qty<1 or qty>int(l["remaining_quantity"]): c.rollback(); await q.answer("Listing/quantity unavailable",show_alert=True); return
            seller_shop=c.execute("SELECT status,level FROM shops WHERE id=?",(l["shop_id"],)).fetchone()
            if not seller_shop or seller_shop["status"]!="approved" or int(seller_shop["level"])!=1:
                c.rollback(); await q.answer("Only approved Level 1 seller listings are available",show_alert=True); return
            total=round(float(l["ask_price_usdt"])*qty,6); bal=balance(c,q.from_user.id)
            if bal+1e-9<total: c.rollback(); await q.answer("Insufficient USDT balance",show_alert=True); return
            changed=c.execute("UPDATE resale_listings SET remaining_quantity=remaining_quantity-?,status=CASE WHEN remaining_quantity-?=0 THEN 'sold' ELSE status END WHERE id=? AND status='active' AND remaining_quantity>=?",(qty,qty,lid,qty))
            if changed.rowcount != 1:
                c.rollback(); await q.answer("Listing stock changed. Please refresh the market.",show_alert=True); return
            cur=c.execute("""INSERT INTO central_orders(catalog_id,buyer_shop_id,buyer_owner_id,quantity,unit_price_usdt,total_usdt,status,created_at) VALUES(?,?,?,?,?,?,'paid',?)""",(l["catalog_id"],shop["id"],q.from_user.id,qty,l["ask_price_usdt"],total,v4.now())); oid=cur.lastrowid
            changed=c.execute("UPDATE central_inventory SET remaining_quantity=remaining_quantity-? WHERE id=? AND owner_id=? AND remaining_quantity>=?",(qty,l["inventory_id"],l["owner_id"],qty))
            if changed.rowcount != 1:
                raise RuntimeError("Seller inventory changed; resale purchase rolled back")
            c.execute("""INSERT INTO central_inventory(owner_id,shop_id,catalog_id,order_id,quantity,remaining_quantity,unit_cost_usdt,status,created_at) VALUES(?,?,?,?,?,?,?,'owned',?)""",(q.from_user.id,shop["id"],l["catalog_id"],oid,qty,qty,l["ask_price_usdt"],v4.now()))
            c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_purchase_debit',?,'USDT',?,?)""",(oid,shop["id"],q.from_user.id,total,f"Resale purchase listing #{lid}",v4.now()))
            profit=round((float(l["ask_price_usdt"])-float(l["unit_cost_usdt"]))*qty,6)
            c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_sale_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],total,f"Resale sale listing #{lid}",v4.now()))
            if profit!=0: c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_profit_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],profit,f"Profit on resale listing #{lid}",v4.now()))
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,q.from_user.id,"resale_bought",f"Listing #{lid}; qty {qty}; total {total} USDT",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Resale purchase failed"); await q.message.reply_text("Purchase failed safely; no changes committed."); return
        finally: c.close()
        await q.answer("Purchased")
        await q.message.reply_text(
            f"<b>✅ PURCHASE CONFIRMED</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Quantity: <b>{qty}</b> item(s)\nTotal paid: <b>{total:g} USDT</b>\nOrder: <code>#{oid}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Marketplace",callback_data="rs:market")],[InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")]]),
            parse_mode="HTML")
        try: await ctx.bot.send_message(l["owner_id"],f"🎉 Your resale listing #{lid} sold {qty} item(s). Sale proceeds: {total:g} USDT; recorded profit: {max(0,profit):g} USDT.")
        except Exception: log.exception("Resale seller notification failed")
        return

async def admin_resale_text(update,ctx):
    data=ctx.user_data.get("admin_resale_offer")
    if not data or update.effective_user.id!=v4.ADMIN_ID: return False
    raw=(update.message.text or "").strip()
    if raw.lower()=="cancel":
        ctx.user_data.pop("admin_resale_offer",None); await update.message.reply_text("Offer cancelled.",reply_markup=__import__('telegram').InlineKeyboardMarkup([[__import__('telegram').InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]])); return True
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
            c.rollback(); await update.message.reply_text("Listing stock changed. Refresh the Admin Resale Market."); return True
        cur=c.execute("""INSERT INTO admin_resale_orders(listing_id,seller_owner_id,seller_shop_id,catalog_id,quantity,unit_price_usdt,total_usdt,status,created_at) VALUES(?,?,?,?,?,?,?,'purchased',?)""",(lid,l["owner_id"],l["shop_id"],l["catalog_id"],qty,price,total,v4.now())); oid=cur.lastrowid
        changed=c.execute("UPDATE central_inventory SET remaining_quantity=remaining_quantity-? WHERE id=? AND owner_id=? AND remaining_quantity>=?",(qty,l["inventory_id"],l["owner_id"],qty))
        if changed.rowcount != 1:
            raise RuntimeError("Seller inventory changed; Admin purchase rolled back")
        c.execute("""INSERT INTO admin_resale_inventory(admin_id,catalog_id,order_id,quantity,remaining_quantity,unit_cost_usdt,created_at) VALUES(?,?,?,?,?,?,?)""",(v4.ADMIN_ID,l["catalog_id"],oid,qty,qty,price,v4.now()))
        c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_sale_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],total,f"Admin purchase listing #{lid}",v4.now()))
        if profit>0: c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,'resale_profit_credit',?,'USDT',?,?)""",(oid,l["shop_id"],l["owner_id"],profit,f"Profit on Admin purchase #{oid}",v4.now()))
        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(v4.ADMIN_ID,l["owner_id"],"admin_resale_purchased",f"Order #{oid}; listing #{lid}; qty {qty}; offer {price:g}; total {total:g} USDT; seller margin {profit:g}",v4.now()))
        c.commit()
    except Exception:
        c.rollback(); log.exception("Admin resale purchase failed"); await update.message.reply_text("Admin purchase failed safely. No changes committed."); return True
    finally: c.close()
    ctx.user_data.pop("admin_resale_offer",None)
    await update.message.reply_text(f"✅ ADMIN PURCHASE COMPLETE\nOrder #{oid}\nQuantity: {qty}\nOffer: {price:g} USDT each\nSeller paid: {total:g} USDT\nSeller profit: {profit:g} USDT (negative means sold below cost).")
    try: await ctx.bot.send_message(l["owner_id"],f"✅ Customer purchased your listing #{lid}: {qty} item(s) at {price:g} USDT each. Total credited: {total:g} USDT. Margin vs your recorded cost: {profit:g} USDT.")
    except Exception: log.exception("Admin resale seller notification failed")
    return True

async def admin_resale_market(update,ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    if update.effective_user.id!=v4.ADMIN_ID: return
    resale_setup(); c=v4.db()
    rows=c.execute("""SELECT l.id,l.owner_id,l.shop_id,l.remaining_quantity,l.ask_price_usdt,p.title,s.shop_name FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id JOIN shops s ON s.id=l.shop_id WHERE l.status='active' AND l.remaining_quantity>0 ORDER BY l.id DESC LIMIT 50""").fetchall(); c.close()
    await update.effective_message.reply_text(
        "<b>🛒 ADMIN RESALE MARKET</b>\n━━━━━━━━━━━━━━━━━━\nReview merchant listings and make a USDT offer.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]),parse_mode="HTML")
    for r in rows:
        await update.effective_message.reply_text(
            f"<b>#{r['id']} {html.escape(str(r['title']))}</b>\n"
            f"Seller: {html.escape(str(r['shop_name']))} | Shop #{r['shop_id']}\n"
            f"Available: <b>{int(r['remaining_quantity'])}</b>\n"
            f"Asking: <b>{float(r['ask_price_usdt']):g} USDT / item</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Review / Buy",callback_data=f"rs:review:{r['id']}")],[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]),parse_mode="HTML")
    if not rows: await update.effective_message.reply_text("No active merchant resale listings.")
