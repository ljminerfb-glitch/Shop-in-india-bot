"""Shared Level-1 central catalogue paid from owners' USDT wallet."""
from pathlib import Path
import logging
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
        conn.execute("""CREATE TABLE IF NOT EXISTS central_orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL,
          buyer_shop_id INTEGER NOT NULL, buyer_owner_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL, unit_price_usdt REAL NOT NULL,
          total_usdt REAL NOT NULL, status TEXT NOT NULL DEFAULT 'paid',
          created_at TEXT NOT NULL, FOREIGN KEY(catalog_id) REFERENCES central_catalog(id))""")
        for key,title,desc,img in PRODUCTS:
            conn.execute("""INSERT OR IGNORE INTO central_catalog
              (product_key,title,description,price_usdt,stock,image_path,active,level_required,created_at,updated_at)
              VALUES(?,?,?,0,0,?,1,1,?,?)""",(key,title,desc,img,v4.now(),v4.now()))
        if own: conn.commit()
    finally:
        if own: conn.close()

def balance(c, owner_id):
    row=c.execute("""SELECT
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type='deposit_credit' THEN amount ELSE 0 END),0) deposits,
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type='central_purchase_debit' THEN amount ELSE 0 END),0) debits,
      COALESCE(SUM(CASE WHEN currency='USDT' AND entry_type='central_purchase_refund_credit' THEN amount ELSE 0 END),0) refunds
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
            "🛠 CENTRAL CATALOGUE ADMIN\nChoose a product to edit its USDT price, shared stock or active status.\nPrices are always USDT; INR conversion is only for deposits/withdrawals.",
            reply_markup=InlineKeyboardMarkup(keyboard))
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
    cur=c.execute("UPDATE central_catalog SET stock=?,updated_at=? WHERE id=?",(qty,v4.now(),pid))
    c.commit(); c.close()
    await update.effective_message.reply_text(f"Stock updated for product #{pid}." if cur.rowcount else "Central product not found.")

async def set_active(update,ctx):
    if update.effective_user.id != v4.ADMIN_ID: return
    if len(ctx.args)!=2 or ctx.args[1].lower() not in ("on","off"):
        await update.effective_message.reply_text("Usage: /centralactive PRODUCT_ID on|off"); return
    try: pid=int(ctx.args[0])
    except ValueError: await update.effective_message.reply_text("Invalid product ID."); return
    c=v4.db(); cur=c.execute("UPDATE central_catalog SET active=?,updated_at=? WHERE id=?",(1 if ctx.args[1].lower()=="on" else 0,v4.now(),pid)); c.commit(); c.close()
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
        setup(c)
        c.execute("BEGIN IMMEDIATE")
        p=c.execute("SELECT * FROM central_catalog WHERE id=? AND active=1 AND level_required=1",(pid,)).fetchone()
        if not p or float(p["price_usdt"])<=0:
            c.rollback(); await update.effective_message.reply_text("Product unavailable. Admin has not set its USDT price yet."); return
        if int(p["stock"])<qty:
            c.rollback(); await update.effective_message.reply_text(f"Insufficient central stock. Available: {p['stock']}"); return
        total=round(float(p["price_usdt"])*qty,6)
        available=balance(c,update.effective_user.id)
        if available+1e-9<total:
            c.rollback(); await update.effective_message.reply_text(f"Insufficient USDT wallet balance.\nRequired: {total:g} USDT\nAvailable: {available:g} USDT\nPlease deposit USDT first."); return
        c.execute("UPDATE central_catalog SET stock=stock-?,updated_at=? WHERE id=? AND stock>=?",(qty,v4.now(),pid,qty))
        if c.execute("SELECT changes()").fetchone()[0]!=1:
            c.rollback(); await update.effective_message.reply_text("Stock changed. Please retry."); return
        cur=c.execute("""INSERT INTO central_orders(catalog_id,buyer_shop_id,buyer_owner_id,quantity,unit_price_usdt,total_usdt,status,created_at)
          VALUES(?,?,?,?,?,?,'paid',?)""",(pid,shop["id"],update.effective_user.id,qty,p["price_usdt"],total,v4.now()))
        oid=cur.lastrowid
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
    await update.effective_message.reply_text("📦 CENTRAL CATALOGUE ORDERS\n"+(
      "Orders are charged in USDT. Fulfil only after arranging delivery; refund restores wallet and shared stock.\n\n"+
      "\n\n".join(f"#{r['id']} {r['title']} ×{r['quantity']}\nOwner {r['buyer_owner_id']} | Shop #{r['buyer_shop_id']}\n{r['total_usdt']:g} USDT | {r['status']} | {r['created_at']}" for r in rows)
      if rows else "No central orders yet."))
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
        await q.message.reply_text(
          f"#{pid} {row['title']}\nPrice: {float(row['price_usdt']):g} USDT\nShared stock: {row['stock']}\nActive: {'Yes' if row['active'] else 'No'}",
          reply_markup=kb); return
    if action in ("price","stock"):
        pid=int(parts[2]); ctx.user_data["central_admin_edit"]={"field":action,"product_id":pid}
        prompt="new price in USDT" if action=="price" else "new shared stock quantity"
        await q.message.reply_text(f"Product #{pid}: send the {prompt} as a number. Send /cancel to stop.")
        return
    if action=="toggle":
        pid=int(parts[2]); c=v4.db()
        c.execute("UPDATE central_catalog SET active=CASE WHEN active=1 THEN 0 ELSE 1 END,updated_at=? WHERE id=?",(v4.now(),pid))
        row=c.execute("SELECT title,active FROM central_catalog WHERE id=?",(pid,)).fetchone()
        c.commit(); c.close()
        await q.message.reply_text(f"{row['title']}: {'ACTIVE' if row['active'] else 'OFF'}",
          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Product",callback_data=f"cc:view:{pid}"),InlineKeyboardButton("⬅️ Catalogue",callback_data="adm:central")]])); return
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
        await q.message.reply_text(f"Central order #{oid}: {label.upper()}.")
        try:
            await ctx.bot.send_message(order["buyer_owner_id"],
              f"Central order #{oid} is {label.upper()}."+
              (f" {order['total_usdt']:g} USDT has been returned to your wallet and stock restored." if action=="refund" else ""))
        except Exception: log.exception("Central order owner notification failed")


async def owner_market(update,ctx,shop):
    if not shop or shop["status"] != "approved" or int(shop["level"]) != 1:
        await update.callback_query.message.reply_text(
            "This central catalogue is available only to approved Level 1 shops.",
            reply_markup=__import__("telegram").InlineKeyboardMarkup([[
                __import__("telegram").InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")
            ]]))
        return
    setup()
    c=v4.db()
    rows=c.execute("SELECT * FROM central_catalog WHERE active=1 AND stock>0 AND price_usdt>0 ORDER BY id").fetchall()
    c.close()
    if not rows:
        await update.callback_query.message.reply_text("🛍 CENTRAL LEVEL 1 CATALOGUE\nNo products are currently available. Please check again later.")
        return
    await update.callback_query.message.reply_text("🛍 CENTRAL LEVEL 1 CATALOGUE\nOnly approved Level 1 shop owners can buy here. All Level 1 owners share the same central stock. Prices are in USDT.")
    root=Path(__file__).resolve().parent
    for p in rows:
        caption=f"#{p['id']} {p['title']}\n{p['description'] or ''}\nPrice: {float(p['price_usdt']):g} USDT\nCentral stock: {p['stock']}\nBuy: /centralbuy {p['id']} QUANTITY"
        image_path=root / (p['image_path'] or "")
        try:
            if image_path.is_file():
                with image_path.open("rb") as photo:
                    await ctx.bot.send_photo(update.effective_chat.id,photo=photo,caption=caption[:1024])
            else:
                await ctx.bot.send_message(update.effective_chat.id,caption+"\n⚠️ Product photo file missing on bot server.")
        except Exception:
            log.exception("Could not send central catalogue item")
            await ctx.bot.send_message(update.effective_chat.id,caption)
    await update.callback_query.message.reply_text("⬅️ Back to Dashboard",reply_markup=__import__("telegram").InlineKeyboardMarkup([[__import__("telegram").InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]]))
