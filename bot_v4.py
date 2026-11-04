"""Shop in India V4 add-on. Run migrate_v4.py, then python bot_v4.py.
Keeps V3 registration, approval, product submission and wallet flows.
"""
import os, sqlite3, logging, re, html
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
import bot_v3 as v3

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB = os.getenv("SHOP_DB", v3.DB)
v3.DB = DB
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shop_v4")

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c=sqlite3.connect(DB, timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c
def admin(uid): return uid == ADMIN_ID

def usdt_inr_rate(c=None):
    own = c is None
    conn = c or db()
    try:
        row = conn.execute("SELECT setting_value FROM platform_settings WHERE setting_key='usdt_inr_rate'").fetchone()
        return float(row['setting_value']) if row else 110.0
    finally:
        if own: conn.close()

def inr_to_usdt(amount_inr, rate):
    return round(float(amount_inr) / float(rate), 6)
def shop_for(uid):
    c=db(); r=c.execute("SELECT * FROM shops WHERE owner_id=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone(); c.close(); return r
def audit_product(c,p,shop,owner,actor,action,old,new):
    c.execute("""INSERT INTO product_audit(product_id,shop_id,owner_id,actor_id,action,old_value,new_value,created_at)
                  VALUES(?,?,?,?,?,?,?,?)""",(p,shop,owner,actor,action,str(old),str(new),now()))
def audit_shop(c,s,owner,actor,action,old,new):
    c.execute("""INSERT INTO shop_audit(shop_id,owner_id,actor_id,action,old_value,new_value,created_at)
                  VALUES(?,?,?,?,?,?,?)""",(s,owner,actor,action,str(old),str(new),now()))

async def dashboard(update,ctx):
    uid=update.effective_user.id
    if admin(uid):
        c=db()
        counts={t:c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"] for t in ["shops","products","v4_orders","payment_history","withdrawals"]}
        c.close()
        await update.message.reply_text(
          "🛡️ ADMIN V4 DASHBOARD\n"
          f"Shops: {counts['shops']} | Products: {counts['products']}\n"
          f"Orders: {counts['v4_orders']} | Payments: {counts['payment_history']} | Withdrawals: {counts['withdrawals']}\n"
          f"USDT conversion rate: 1 USDT = INR {usdt_inr_rate():,.2f}\n\n"
          "Commands:\n/levels — edit level settings\n/setlevel LEVEL ACTIVATION_USDT PRODUCT_TARGET PROFIT_MIN PROFIT_MAX\n"
          "/setfx INR_PER_USDT — change deposit/withdrawal conversion rate from Admin dashboard\n"
          "/products — latest products\n/setprice PRODUCT_ID INR_PRICE\n/setstock PRODUCT_ID QTY\n/setproductlevel PRODUCT_ID LEVEL\n"
          "/orders — pending orders\n/orderdone ORDER_ID — mark delivered/completed\n/orderreject ORDER_ID REASON\n"
          "/audit PRODUCT_ID — product audit\n/shopaudit SHOP_ID — shop audit\n/owner OWNER_ID — owner audit summary")
    else:
        s=shop_for(uid)
        if not s or s["status"]!="approved":
            await update.message.reply_text("Approved shop owner account required."); return
        c=db()
        stats=c.execute("""SELECT
          (SELECT COUNT(*) FROM products WHERE shop_id=?) products,
          (SELECT COUNT(*) FROM v4_orders WHERE buyer_shop_id=?) buys,
          (SELECT COUNT(*) FROM v4_orders WHERE seller_shop_id=?) sells,
          (SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND entry_type='sale_credit') profit""",(s["id"],s["id"],s["id"],uid)).fetchone()
        await update.message.reply_text(
          f"🏪 OWNER DASHBOARD — {s['shop_name']}\nShop ID: {s['id']} | Level: {s['level']}\n"
          f"Products: {stats['products']} | Buy orders: {stats['buys']} | Sell orders: {stats['sells']}\n"
          f"Recorded sale credits: INR {stats['profit']:.2f}\n\n"
          "Use /myproducts, /myorders, /history and /mylevels. Wallet: tap Withdrawal Wallet in the main menu.")
        c.close()

async def levels(update,ctx):
    c=db(); rows=c.execute("SELECT * FROM level_settings ORDER BY level").fetchall(); c.close()
    await update.message.reply_text("LEVEL SETTINGS (editable with /setlevel):\n"+
      "\n".join(f"L{r['level']}: activation {r['activation_usdt']} USDT | target {r['product_target'] or 'All'} | margin reference {r['profit_min']}-{r['profit_max']}" for r in rows))

async def setlevel(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=5: await update.message.reply_text("Usage: /setlevel LEVEL ACTIVATION_USDT PRODUCT_TARGET PROFIT_MIN PROFIT_MAX"); return
    try:
        lv,fee,target= int(ctx.args[0]),float(ctx.args[1]),int(ctx.args[2])
        lo,hi=float(ctx.args[3]),float(ctx.args[4])
        if lv not in range(1,6) or min(fee,target,lo)<0 or hi<lo: raise ValueError()
    except ValueError:
        await update.message.reply_text("Invalid values. Check level 1–5 and non-negative amounts."); return
    c=db(); c.execute("UPDATE level_settings SET activation_usdt=?,product_target=?,profit_min=?,profit_max=? WHERE level=?",(fee,target,lo,hi,lv))
    c.execute("INSERT INTO admin_audit(admin_id,action,details,created_at) VALUES(?,?,?,?)",(ADMIN_ID,"level_settings_updated",f"L{lv}: {fee},{target},{lo},{hi}",now()))
    c.commit(); c.close(); await update.message.reply_text(f"Saved L{lv} settings.")

async def products(update,ctx):
    if not admin(update.effective_user.id): return
    c=db(); rows=c.execute("""SELECT p.id,p.title,p.price,p.currency,p.stock,p.active,p.shop_id,s.shop_name
       FROM products p JOIN shops s ON s.id=p.shop_id ORDER BY p.id DESC LIMIT 30""").fetchall(); c.close()
    if not rows: await update.message.reply_text("No products yet."); return
    await update.message.reply_text("LATEST PRODUCTS\n"+"\n\n".join(f"#{r['id']} {r['title']}\nShop #{r['shop_id']} {r['shop_name']}\n{r['currency']} {r['price']} | stock {r['stock']} | active {r['active']}" for r in rows))

async def edit_product(update,ctx,field):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=2: await update.message.reply_text(f"Usage: /{field} PRODUCT_ID VALUE"); return
    try: pid=int(ctx.args[0]); val=float(ctx.args[1]) if field=="setprice" else int(ctx.args[1])
    except ValueError: await update.message.reply_text("Invalid product ID/value."); return
    if val<=0 if field=="setprice" else val<0: await update.message.reply_text("Value must be positive (stock may be zero)."); return
    c=db(); p=c.execute("SELECT p.*,s.owner_id FROM products p JOIN shops s ON s.id=p.shop_id WHERE p.id=?",(pid,)).fetchone()
    if not p: c.close(); await update.message.reply_text("Product not found."); return
    col="price" if field=="setprice" else "stock"; old=p[col]
    c.execute(f"UPDATE products SET {col}=?, updated_at=? WHERE id=?",(val,now(),pid))
    audit_product(c,pid,p["shop_id"],p["owner_id"],ADMIN_ID,col,old,val)
    c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(ADMIN_ID,p["owner_id"],"product_"+col,f"Product #{pid}: {old} -> {val}",now()))
    c.commit(); c.close(); await update.message.reply_text(f"Product #{pid} {col} updated: {old} → {val}.")

async def setproductlevel(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=2: await update.message.reply_text("Usage: /setproductlevel PRODUCT_ID LEVEL"); return
    try: pid,lv=int(ctx.args[0]),int(ctx.args[1]); assert lv in range(1,6)
    except Exception: await update.message.reply_text("Use product ID and level 1–5."); return
    c=db(); p=c.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
    if not p: c.close(); await update.message.reply_text("Product not found."); return
    old=p["level_required"] if "level_required" in p.keys() else 1
    c.execute("UPDATE products SET level_required=?,updated_at=? WHERE id=?",(lv,now(),pid))
    audit_product(c,pid,p["shop_id"],None,ADMIN_ID,"level_required",old,lv); c.commit(); c.close()
    await update.message.reply_text(f"Product #{pid} now requires L{lv}.")

async def myproducts(update,ctx):
    s=shop_for(update.effective_user.id)
    if not s: await update.message.reply_text("Shop not found."); return
    c=db(); rows=c.execute("SELECT id,title,price,currency,stock,active FROM products WHERE shop_id=? ORDER BY id DESC LIMIT 30",(s["id"],)).fetchall(); c.close()
    await update.message.reply_text("YOUR PRODUCTS\n"+("\n\n".join(f"#{r['id']} {r['title']} | {r['currency']} {r['price']} | stock {r['stock']} | active {r['active']}" for r in rows) if rows else "No listed products."))

async def myorders(update,ctx):
    s=shop_for(update.effective_user.id)
    if not s: await update.message.reply_text("Shop not found."); return
    c=db(); rows=c.execute("SELECT * FROM v4_orders WHERE buyer_shop_id=? OR seller_shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],s["id"])).fetchall(); c.close()
    await update.message.reply_text("YOUR ORDERS\n"+("\n\n".join(f"Order #{r['id']} product #{r['product_id']} qty {r['quantity']} | INR {r['total_amount']:.2f} | {r['status']}" for r in rows) if rows else "No orders yet."))

async def history(update,ctx):
    s=shop_for(update.effective_user.id)
    if not s: await update.message.reply_text("Shop not found."); return
    uid=update.effective_user.id; c=db()
    sections=[]
    for title,sql,args in [
      ("BUY", "SELECT id,total_amount,status,created_at FROM v4_orders WHERE buyer_owner_id=? ORDER BY id DESC LIMIT 8",(uid,)),
      ("SELL", "SELECT id,seller_amount,status,created_at FROM v4_orders WHERE seller_owner_id=? ORDER BY id DESC LIMIT 8",(uid,)),
      ("LEDGER", "SELECT entry_type,amount,currency,note,created_at FROM ledger WHERE owner_id=? ORDER BY id DESC LIMIT 10",(uid,)),
      ("LEVEL", "SELECT old_level,new_level,reason,created_at FROM level_history WHERE owner_id=? ORDER BY id DESC LIMIT 8",(uid,)),
      ("PAYMENTS", "SELECT payment_type,amount,currency,status,created_at FROM payment_history WHERE owner_id=? ORDER BY id DESC LIMIT 8",(uid,)),
      ("LEGACY PAYMENT REQUESTS", "SELECT payment_type,amount,currency,status,created_at FROM payment_requests WHERE owner_id=? ORDER BY id DESC LIMIT 8",(uid,)),
      ("WITHDRAWALS", "SELECT amount,currency,status,created_at FROM withdrawals WHERE owner_id=? ORDER BY id DESC LIMIT 8",(uid,))]:
        rows=c.execute(sql,args).fetchall()
        sections.append(title+": "+("; ".join(" | ".join(str(v) for v in r) for r in rows) if rows else "No records"))
    c.close(); await update.message.reply_text("\n\n".join(sections))

async def mylevels(update,ctx):
    s=shop_for(update.effective_user.id)
    if not s: await update.message.reply_text("Shop not found."); return
    c=db(); rows=c.execute("SELECT * FROM level_history WHERE shop_id=? ORDER BY id DESC LIMIT 15",(s["id"],)).fetchall(); c.close()
    await update.message.reply_text("LEVEL HISTORY\n"+("\n".join(f"L{r['old_level']} → L{r['new_level']} | {r['reason']} | {r['created_at']}" for r in rows) if rows else f"Current level: {s['level']} (no changes recorded yet)."))

async def audit(update,ctx,table):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=1 or not ctx.args[0].isdigit(): await update.message.reply_text("Provide numeric ID."); return
    ident=int(ctx.args[0]); c=db()
    if table=="product":
        rows=c.execute("SELECT * FROM product_audit WHERE product_id=? ORDER BY id DESC LIMIT 20",(ident,)).fetchall()
        out=[f"{r['action']}: {r['old_value']} → {r['new_value']} | actor {r['actor_id']} | {r['created_at']}" for r in rows]
    else:
        rows=c.execute("SELECT * FROM shop_audit WHERE shop_id=? ORDER BY id DESC LIMIT 20",(ident,)).fetchall()
        out=[f"{r['action']}: {r['old_value']} → {r['new_value']} | actor {r['actor_id']} | {r['created_at']}" for r in rows]
    c.close(); await update.message.reply_text(f"{table.upper()} AUDIT #{ident}\n"+("\n".join(out) if out else "No audit entries yet."))

async def owner_audit(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=1 or not ctx.args[0].isdigit(): await update.message.reply_text("Usage: /owner OWNER_ID"); return
    oid=int(ctx.args[0]); c=db()
    shops=c.execute("SELECT * FROM shops WHERE owner_id=?",(oid,)).fetchall()
    products=c.execute("SELECT p.id,p.title,p.price,p.stock,p.shop_id FROM products p JOIN shops s ON s.id=p.shop_id WHERE s.owner_id=?",(oid,)).fetchall()
    orders=c.execute("SELECT id,status,total_amount FROM v4_orders WHERE buyer_owner_id=? OR seller_owner_id=? ORDER BY id DESC LIMIT 20",(oid,oid)).fetchall()
    c.close()
    await update.message.reply_text(f"OWNER AUDIT {oid}\nShops: "+", ".join(f"#{s['id']} {s['shop_name']} L{s['level']} {s['status']}" for s in shops)+
      "\nProducts: "+", ".join(f"#{p['id']} {p['title']} ₹{p['price']} stock {p['stock']}" for p in products)+
      "\nOrders: "+", ".join(f"#{o['id']} {o['status']} ₹{o['total_amount']}" for o in orders))

async def buy(update,ctx):
    if len(ctx.args)!=2: await update.message.reply_text("Usage: /buy PRODUCT_ID QUANTITY"); return
    buyer=shop_for(update.effective_user.id)
    if not buyer or buyer["status"]!="approved": await update.message.reply_text("Approved buyer shop required."); return
    try: pid,qty=int(ctx.args[0]),int(ctx.args[1]); assert qty>0
    except Exception: await update.message.reply_text("Invalid product ID or quantity."); return
    c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        p=c.execute("""SELECT p.*,s.owner_id,s.status seller_status,s.level seller_level
                       FROM products p JOIN shops s ON s.id=p.shop_id WHERE p.id=? AND p.active=1""",(pid,)).fetchone()
        if not p or p["stock"]<qty or p["shop_id"]==buyer["id"]:
            c.rollback(); await update.message.reply_text("Product unavailable, insufficient stock, or buying from your own shop is not allowed."); return
        target=p["level_required"] if "level_required" in p.keys() else 1
        if int(buyer["level"])!=int(target):
            c.rollback(); await update.message.reply_text(f"This product requires buyer shop L{target}."); return
        total=round(float(p["price"])*qty,2)
        fee_pct=float(c.execute("SELECT setting_value FROM platform_settings WHERE setting_key='platform_fee_percent'").fetchone()[0])
        fee=round(total*fee_pct/100,2); seller=round(total-fee,2)
        c.execute("UPDATE products SET stock=stock-?,updated_at=? WHERE id=? AND stock>=?",(qty,now(),pid,qty))
        if c.total_changes<1: c.rollback(); await update.message.reply_text("Stock changed; retry."); return
        cur=c.execute("""INSERT INTO v4_orders(product_id,buyer_shop_id,seller_shop_id,buyer_owner_id,seller_owner_id,quantity,unit_price,total_amount,platform_fee,seller_amount,currency,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,'INR','pending_admin_fulfilment',?,?)""",
          (pid,buyer["id"],p["shop_id"],update.effective_user.id,p["owner_id"],qty,p["price"],total,fee,seller,now(),now()))
        oid=cur.lastrowid
        audit_product(c,pid,p["shop_id"],p["owner_id"],update.effective_user.id,"stock_reserved",p["stock"],p["stock"]-qty)
        c.commit()
    except Exception as e:
        c.rollback(); log.exception("Buy failed"); await update.message.reply_text("Order could not be created; no payment has been taken.")
        return
    finally: c.close()
    await update.message.reply_text(f"Order #{oid} created. Amount ₹{total:.2f}. Payment and fulfilment require Admin review; stock is reserved.")
    try: await ctx.bot.send_message(ADMIN_ID,f"🛒 ORDER #{oid}\nProduct #{pid} {p['title']}\nBuyer shop #{buyer['id']}\nSeller shop #{p['shop_id']}\nQty {qty} | Total ₹{total:.2f}\nFee ₹{fee:.2f} | Seller allocation ₹{seller:.2f}\nUse /orderdone {oid} after real delivery and payment verification, or /orderreject {oid} REASON.")
    except Exception: log.exception("Admin order alert failed")

async def orders(update,ctx):
    if not admin(update.effective_user.id): return
    c=db(); rows=c.execute("SELECT * FROM v4_orders WHERE status NOT IN ('completed','rejected') ORDER BY id DESC LIMIT 20").fetchall(); c.close()
    await update.message.reply_text("PENDING ORDERS\n"+("\n\n".join(f"#{r['id']} product #{r['product_id']} qty {r['quantity']} total ₹{r['total_amount']} status {r['status']}" for r in rows) if rows else "No pending orders."))

async def orderdone(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=1 or not ctx.args[0].isdigit(): await update.message.reply_text("Usage: /orderdone ORDER_ID"); return
    oid=int(ctx.args[0]); c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT * FROM v4_orders WHERE id=?",(oid,)).fetchone()
        if not r or r["status"] in ("completed","rejected"): c.rollback(); await update.message.reply_text("Order not found or already closed."); return
        c.execute("UPDATE v4_orders SET status='completed',completed_at=?,updated_at=? WHERE id=?",(now(),now(),oid))
        c.execute("INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(?,?,?,?,?,'INR',?,?)",(oid,r["seller_shop_id"],r["seller_owner_id"],"sale_credit",r["seller_amount"],f"Completed order #{oid}",now()))
        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(ADMIN_ID,r["seller_owner_id"],"order_completed",f"Order #{oid}",now()))
        c.commit()
    except Exception: c.rollback(); log.exception("Complete order failed"); await update.message.reply_text("Could not complete order."); return
    finally: c.close()
    await update.message.reply_text(f"✅ ORDER #{oid} COMPLETED. Seller credit recorded as INR {r['seller_amount']:.2f}.")
    for target in [r["buyer_owner_id"],r["seller_owner_id"]]:
        try: await ctx.bot.send_message(target,f"Order #{oid} marked completed by Admin.")
        except Exception: pass

async def orderreject(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)<2 or not ctx.args[0].isdigit(): await update.message.reply_text("Usage: /orderreject ORDER_ID REASON"); return
    oid=int(ctx.args[0]); reason=" ".join(ctx.args[1:]); c=db()
    try:
        c.execute("BEGIN IMMEDIATE"); r=c.execute("SELECT * FROM v4_orders WHERE id=?",(oid,)).fetchone()
        if not r or r["status"] in ("completed","rejected"): c.rollback(); await update.message.reply_text("Order not found or already closed."); return
        c.execute("UPDATE v4_orders SET status='rejected',admin_note=?,updated_at=? WHERE id=?",(reason,now(),oid))
        c.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE id=?",(r["quantity"],now(),r["product_id"]))
        audit_product(c,r["product_id"],r["seller_shop_id"],r["seller_owner_id"],ADMIN_ID,"stock_restored",None,r["quantity"])
        c.commit()
    except Exception: c.rollback(); await update.message.reply_text("Could not reject order."); return
    finally: c.close()
    await update.message.reply_text(f"❌ ORDER #{oid} REJECTED. Reserved stock restored.")



# ---- Payments, level activation and withdrawal controls ----
PAY_TRON = "TDrWh8zrWDgB3uGeyBsWym9ATNVNDPjD7v"
PAY_BSC = "0x62eb80614112d7b6bdeacf07b745755605c4b7e0"

async def activate(update, ctx):
    shop=shop_for(update.effective_user.id)
    if not shop or shop["status"]!="approved":
        await update.message.reply_text("Approved shop required."); return
    if len(ctx.args)!=1 or not ctx.args[0].isdigit() or int(ctx.args[0]) not in range(1,6):
        await update.message.reply_text("Usage: /activate LEVEL (1–5)"); return
    target=int(ctx.args[0])
    if target<=int(shop["level"]):
        await update.message.reply_text("Aapka current level is level ke barabar ya is se upar hai."); return
    c=db()
    setting=c.execute("SELECT activation_usdt FROM level_settings WHERE level=?",(target,)).fetchone()
    if not setting: c.close(); await update.message.reply_text("Level configuration missing."); return
    pending=c.execute("SELECT id FROM payment_requests WHERE owner_id=? AND payment_type='level_activation' AND level=? AND status='pending'",(update.effective_user.id,target)).fetchone()
    if pending: c.close(); await update.message.reply_text(f"Is level ki payment request #{pending['id']} pehle se pending hai."); return
    amount=float(setting["activation_usdt"])
    rate=usdt_inr_rate(c)
    amount_inr=round(amount*rate,2)
    cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
       VALUES(?,?,'level_activation',?,?,'USDT','not_selected','pending',?)""",(update.effective_user.id,shop["id"],target,amount,now()))
    rid=cur.lastrowid; c.commit(); c.close()
    ctx.user_data["payment_request_id"]=rid
    ctx.user_data["state"]="activation_network"
    await update.message.reply_text(
      f"Level L{target} activation request #{rid}\nAmount: {amount:g} USDT\n"
      f"Conversion: 1 USDT = INR {rate:,.2f}\nPayable equivalent: INR {amount_inr:,.2f}\n\n"
      "Network choose karein: TRC20 ya BEP20. Payment sirf tab karein jab aap address/network verify kar lein.",
      reply_markup=__import__("telegram").ReplyKeyboardMarkup([["USDT TRC20","USDT BEP20"],["Cancel"]],resize_keyboard=True))

async def payment_network(update,ctx):
    value=update.message.text.strip()
    rid=ctx.user_data.get("payment_request_id")
    if value=="Cancel":
        if rid:
            c=db(); c.execute("UPDATE payment_requests SET status='cancelled',reviewed_at=? WHERE id=? AND owner_id=? AND status='pending'",(now(),rid,update.effective_user.id)); c.commit(); c.close()
        ctx.user_data.clear(); await update.message.reply_text("Cancelled.",reply_markup=v3.main_keyboard()); return
    if value not in ("USDT TRC20","USDT BEP20") or not rid:
        await update.message.reply_text("TRC20 ya BEP20 choose karein, ya Cancel."); return
    address=PAY_TRON if value=="USDT TRC20" else PAY_BSC
    c=db(); c.execute("UPDATE payment_requests SET network=? WHERE id=? AND owner_id=? AND status='pending'",(value,rid,update.effective_user.id)); c.commit(); c.close()
    ctx.user_data["state"]="payment_proof"
    c=db(); pr=c.execute("SELECT amount,currency FROM payment_requests WHERE id=? AND owner_id=?",(rid,update.effective_user.id)).fetchone(); rate=usdt_inr_rate(c); c.close()
    inr_equiv=round(float(pr['amount'])*rate,2) if pr and pr['currency']=='USDT' else 0
    await update.message.reply_text(f"Payment address ({value}):\n{address}\n\n"
      f"Amount: {float(pr['amount']):g} USDT\nConversion: 1 USDT = INR {rate:,.2f}\nINR equivalent: ₹{inr_equiv:,.2f}\n\n"
      "USDT payment isi network par bhejein. INR figure sirf reference conversion hai; actual crypto transfer USDT mein hoga. Payment ke baad transaction screenshot/photo bhejein.")

async def payment_photo(update,ctx):
    rid=ctx.user_data.get("payment_request_id")
    if not rid or ctx.user_data.get("state")!="payment_proof": return
    photo=update.message.photo[-1].file_id
    c=db()
    row=c.execute("SELECT * FROM payment_requests WHERE id=? AND owner_id=? AND status='pending'",(rid,update.effective_user.id)).fetchone()
    if not row: c.close(); ctx.user_data.clear(); await update.message.reply_text("Pending request nahi mili."); return
    tx_hash=(ctx.user_data.get("deposit_tx_hash") or "").strip()
    try:
        c.execute("UPDATE payment_requests SET screenshot_file_id=?,transaction_hash=COALESCE(?,transaction_hash) WHERE id=?",(photo,tx_hash or None,rid))
    except Exception:
        c.execute("UPDATE payment_requests SET screenshot_file_id=? WHERE id=?",(photo,rid))
    saved_hash=tx_hash or (row["transaction_hash"] if "transaction_hash" in row.keys() else (row["tx_hash"] if "tx_hash" in row.keys() else None))
    c.commit(); c.close()
    ctx.user_data.clear()
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("Approve",callback_data=f"pay:approve:{rid}"),InlineKeyboardButton("Reject",callback_data=f"pay:reject:{rid}")],
        [InlineKeyboardButton("⬅️ Pending Deposits",callback_data="adm:payments"),InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
    ])
    try:
        rate=usdt_inr_rate()
        inr_equiv=round(float(row['amount'])*rate,2) if row['currency']=='USDT' else None
        conversion=f"\nRate: 1 USDT = INR {rate:,.2f}\nINR equivalent: ₹{inr_equiv:,.2f}" if inr_equiv is not None else ""
        await ctx.bot.send_photo(ADMIN_ID,photo=photo,caption=f"PAYMENT PROOF #{rid}\nOwner: {row['owner_id']}\nShop: {row['shop_id']}\nType: {row['payment_type']} L{row['level']}\nAmount: {row['amount']} {row['currency']}{conversion}\nNetwork: {row['network']}\nTransaction Hash: {saved_hash or 'Not provided'}\nVerify transaction independently before approving.",reply_markup=kb)
    except Exception:
        log.exception("Payment proof admin delivery failed")
        await update.message.reply_text("Proof save ho gaya, lekin Admin ko Telegram notification nahi ja saki. Admin se contact karein.")
        return
    await update.message.reply_text(f"Payment proof request #{rid} Admin review ke liye bhej di gayi hai.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]]))

async def payment_review(update,ctx):
    q=update.callback_query
    if not admin(q.from_user.id):
        await q.answer("Admin only",show_alert=True)
        return
    await q.answer()
    try:
        _,action,rid_text=q.data.split(":")
        rid=int(rid_text)
        if action not in ("approve","reject"):
            raise ValueError("Invalid payment action")
    except (ValueError,IndexError):
        await q.message.reply_text("Invalid payment review action.")
        return

    c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT * FROM payment_requests WHERE id=?",(rid,)).fetchone()
        if not r or r["status"]!="pending":
            c.rollback()
            await q.message.reply_text("Request missing or already reviewed.")
            return
        # A proof is mandatory for approval, but Admin must be able to reject
        # an incomplete/invalid request that has no screenshot.
        if action=="approve" and not r["screenshot_file_id"]:
            c.rollback()
            await q.message.reply_text("Cannot approve: payment screenshot is missing. You may reject this request.")
            return

        status="approved" if action=="approve" else "rejected"
        reviewed_at=now()
        cur=c.execute(
            "UPDATE payment_requests SET status=?,reviewed_at=?,admin_note=? "
            "WHERE id=? AND status='pending'",
            (status,reviewed_at,
             "Admin verified manually" if action=="approve" else ("This is fake screenshot" if r["payment_type"]=="wallet_deposit" else "Admin rejected proof"),
             rid))
        if cur.rowcount != 1:
            c.rollback()
            await q.message.reply_text("This payment was already reviewed. Refresh Pending Payments.")
            return

        c.execute("""INSERT INTO payment_history(owner_id,shop_id,payment_type,amount,currency,status,reference,admin_note,actor_id,created_at,reviewed_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (r["owner_id"],r["shop_id"],r["payment_type"],r["amount"],r["currency"],
           status,f"payment_request:{rid}","Manually reviewed by admin",ADMIN_ID,
           r["created_at"],reviewed_at))

        cashback_amount=0.0
        if action=="approve" and r["payment_type"]=="level_activation":
            shop=c.execute("SELECT level FROM shops WHERE id=? AND owner_id=?",
                           (r["shop_id"],r["owner_id"])).fetchone()
            if not shop:
                raise RuntimeError("Shop missing for payment request")
            old=int(shop["level"])
            new=int(r["level"])
            if new>old:
                c.execute("UPDATE shops SET level=? WHERE id=?",(new,r["shop_id"]))
                c.execute("INSERT INTO level_history(shop_id,owner_id,old_level,new_level,reason,actor_id,created_at) VALUES(?,?,?,?,?,?,?)",
                          (r["shop_id"],r["owner_id"],old,new,
                           f"Approved activation payment #{rid}",ADMIN_ID,reviewed_at))
                audit_shop(c,r["shop_id"],r["owner_id"],ADMIN_ID,"level_activated",old,new)
            # Credit 50% of the approved activation fee to the owner's USDT wallet.
            # The pending->approved transition above prevents duplicate credits.
            cashback_amount=round(float(r["amount"])*0.50,6)
            if cashback_amount>0:
                c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
                              VALUES(NULL,?,?, 'level_activation_wallet_credit',?,'USDT',?,?)""",
                          (r["shop_id"],r["owner_id"],cashback_amount,
                           f"50% wallet cashback for approved level activation #{rid}",reviewed_at))

        if action=="approve" and r["payment_type"]=="wallet_deposit":
            c.execute("INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at) VALUES(NULL,?,?, 'deposit_credit',?,'USDT',?,?)",
                      (r["shop_id"],r["owner_id"],float(r["amount"]),
                       f"Admin-approved deposit #{rid}",reviewed_at))

        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                  (ADMIN_ID,r["owner_id"],"payment_"+status,
                   f"Payment request #{rid}",reviewed_at))
        c.commit()
    except Exception:
        c.rollback()
        log.exception("Payment review failed")
        await q.message.reply_text("Review failed; no changes committed.")
        return
    finally:
        c.close()

    # Update the exact Telegram card used for the review so it no longer
    # misleadingly displays PENDING or keeps active review buttons.
    try:
        result_label = "✅ APPROVED" if status == "approved" else "❌ REJECTED"
        queue_action = "payments" if r["payment_type"] == "wallet_deposit" else "levelpayments"
        result_line=f"\n\nREVIEW RESULT: {result_label}"
        if q.message.photo:
            old_caption=q.message.caption or ""
            clean_caption=old_caption.split("\n\nREVIEW RESULT:",1)[0]
            await q.message.edit_caption(caption=clean_caption+result_line,
                                         reply_markup=InlineKeyboardMarkup([
                                             [InlineKeyboardButton("⬅️ Back to Payment Queue",callback_data=f"adm:{queue_action}")],
                                             [InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
                                         ]))
        else:
            old_text=q.message.text or ""
            clean_text=old_text.split("\n\nREVIEW RESULT:",1)[0]
            await q.message.edit_text(text=clean_text+result_line,
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("⬅️ Back to Payment Queue",callback_data=f"adm:{queue_action}")],
                                          [InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
                                      ]))
    except Exception:
        log.exception("Could not update reviewed payment Telegram card")
        await q.message.reply_text(f"Payment request #{rid}: {status.upper()}. Database status saved.",
                                   reply_markup=InlineKeyboardMarkup([
                                       [InlineKeyboardButton("⬅️ Back to Payment Queue",callback_data=f"adm:{queue_action}")],
                                       [InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
                                   ]))

    try:
        await ctx.bot.send_message(
            r["owner_id"],
            f"{'✅ APPROVED' if status == 'approved' else '❌ REJECTED'} — Payment request #{rid}."+
            (f" Your shop level is now L{r['level']}. {cashback_amount:g} USDT (50% of activation fee) has been credited to your wallet."
             if status=="approved" and r["payment_type"]=="level_activation" else ""))
    except Exception:
        log.exception("Payment review owner notification failed")

async def setshoplevel(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)<2 or not all(x.isdigit() for x in ctx.args[:2]):
        await update.message.reply_text("Usage: /setshoplevel SHOP_ID LEVEL [reason]"); return
    sid,lv=map(int,ctx.args[:2]); reason=" ".join(ctx.args[2:]) or "Admin adjustment"
    if lv not in range(0,6): await update.message.reply_text("Level 0–5 only."); return
    c=db(); r=c.execute("SELECT owner_id,level FROM shops WHERE id=?",(sid,)).fetchone()
    if not r: c.close(); await update.message.reply_text("Shop not found."); return
    old=int(r["level"])
    c.execute("UPDATE shops SET level=? WHERE id=?",(lv,sid))
    c.execute("INSERT INTO level_history(shop_id,owner_id,old_level,new_level,reason,actor_id,created_at) VALUES(?,?,?,?,?,?,?)",(sid,r["owner_id"],old,lv,reason,ADMIN_ID,now()))
    audit_shop(c,sid,r["owner_id"],ADMIN_ID,"level_changed",old,lv)
    c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",(ADMIN_ID,r["owner_id"],"shop_level_changed",f"Shop {sid}: {old}->{lv}; {reason}",now()))
    c.commit(); c.close(); await update.message.reply_text(f"Shop #{sid} level changed L{old} → L{lv}; audit saved.")

async def withdraw(update,ctx):
    """Legacy command compatibility: /withdraw AMOUNT_USDT TRC20_ADDRESS."""
    shop=shop_for(update.effective_user.id)
    if not shop or shop["status"]!="approved":
        await update.message.reply_text("Approved shop required."); return
    if len(ctx.args)!=2:
        await update.message.reply_text("Usage: /withdraw AMOUNT_USDT TRC20_ADDRESS"); return
    try:
        amount=round(float(ctx.args[0]),6)
        address=ctx.args[1].strip()
        if amount<=0 or not re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}",address):
            raise ValueError()
    except (TypeError,ValueError):
        await update.message.reply_text("Enter a positive USDT amount and a valid USDT TRC20 address."); return
    c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        earned=float(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='resale_sale_credit'",
            (update.effective_user.id,)).fetchone()[0])
        paid=float(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='withdrawal_debit'",
            (update.effective_user.id,)).fetchone()[0])
        reserved=float(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE owner_id=? AND currency='USDT' AND status='pending'",
            (update.effective_user.id,)).fetchone()[0])
        available=max(0.0,round(earned-paid-reserved,6))
        if amount>available+1e-9:
            c.rollback()
            await update.message.reply_text(f"Insufficient available sale earnings. Available: {available:.6f} USDT."); return
        cur=c.execute("""INSERT INTO withdrawals
            (owner_id,shop_id,amount,currency,network,wallet_address,status,created_at)
            VALUES(?,?,?,'USDT','USDT TRC20',?,'pending',?)""",
            (update.effective_user.id,shop["id"],amount,address,now()))
        wid=cur.lastrowid
        c.commit()
    except Exception:
        c.rollback(); log.exception("USDT withdrawal request failed")
        await update.message.reply_text("Withdrawal request failed safely; no balance was deducted."); return
    finally:
        c.close()
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ MARK PAID",callback_data=f"wd:paid:{wid}"),
                              InlineKeyboardButton("❌ REJECT",callback_data=f"wd:reject:{wid}")]])
    await update.message.reply_text(
        f"Withdrawal request #{wid} submitted. Amount: {amount:.6f} USDT. Network: USDT TRC20. Status: pending Admin review.")
    try:
        await ctx.bot.send_message(ADMIN_ID,
            f"USDT WITHDRAWAL #{wid}\nOwner: {update.effective_user.id}\nAmount: {amount:.6f} USDT\n"
            f"Network: USDT TRC20\nTRC20 address: {address}\n"
            "Send the USDT manually on TRON before marking paid.",reply_markup=kb)
    except Exception:
        log.exception("Withdrawal notification failed")


async def withdrawal_review(update,ctx):
    q=update.callback_query
    if not admin(q.from_user.id):
        await q.answer("Admin only",show_alert=True); return
    await q.answer()
    try:
        _,action,wid_text=q.data.split(":")
        wid=int(wid_text)
        if action not in ("paid","reject"): raise ValueError()
    except (ValueError,IndexError):
        await q.message.reply_text("Invalid withdrawal review action."); return
    c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT * FROM withdrawals WHERE id=?",(wid,)).fetchone()
        if not r or r["status"]!="pending":
            c.rollback(); await q.message.reply_text("Withdrawal missing or already reviewed."); return
        currency=str(r["currency"] or "USDT").upper()
        if action=="paid":
            cur=c.execute("UPDATE withdrawals SET status='paid',reviewed_at=?,admin_note='Marked paid after manual payout verification' WHERE id=? AND status='pending'",(now(),wid))
            if cur.rowcount != 1:
                c.rollback(); await q.message.reply_text("This withdrawal was already reviewed. Refresh Pending Withdrawals."); return
            c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
                         VALUES(NULL,?,?, 'withdrawal_debit',?,?,?,?)""",
                      (r["shop_id"],r["owner_id"],r["amount"],currency,f"Withdrawal #{wid} paid",now()))
            status="paid"
        else:
            cur=c.execute("UPDATE withdrawals SET status='rejected',reviewed_at=?,admin_note='Rejected by admin' WHERE id=? AND status='pending'",(now(),wid))
            if cur.rowcount != 1:
                c.rollback(); await q.message.reply_text("This withdrawal was already reviewed. Refresh Pending Withdrawals."); return
            status="rejected"
        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                  (ADMIN_ID,r["owner_id"],"withdrawal_"+status,f"Withdrawal #{wid}",now()))
        c.commit()
    except Exception:
        c.rollback(); log.exception("Withdrawal review failed")
        await q.message.reply_text("Withdrawal review failed; no changes were committed."); return
    finally:
        c.close()
    result_label="✅ PAID" if status=="paid" else "❌ REJECTED"
    result_text=(f"<b>WITHDRAWAL {result_label}</b>\n"
                 f"Amount: <b>{float(r['amount']):.6f} {html.escape(currency)}</b>\n"
                 f"Network: <b>{html.escape(str(r['network'] or ''))}</b>")
    try:
        old_text=q.message.text or q.message.caption or f"WITHDRAWAL #{wid}"
        clean_text=old_text.split("\n\nREVIEW RESULT:",1)[0]
        await q.message.edit_text(clean_text+f"\n\nREVIEW RESULT: {result_label}",reply_markup=None)
    except Exception:
        try:
            old_caption=q.message.caption or f"WITHDRAWAL #{wid}"
            clean_caption=old_caption.split("\n\nREVIEW RESULT:",1)[0]
            await q.message.edit_caption(clean_caption+f"\n\nREVIEW RESULT: {result_label}",reply_markup=None)
        except Exception:
            log.exception("Could not update reviewed withdrawal card")
    await q.message.reply_text(result_text,parse_mode="HTML")
    try:
        await ctx.bot.send_message(r["owner_id"],result_text,parse_mode="HTML")
    except Exception:
        log.exception("Withdrawal owner notification failed")


async def myproductaudit(update,ctx):
    s=shop_for(update.effective_user.id)
    if not s: await update.message.reply_text("Shop not found."); return
    c=db(); rows=c.execute("SELECT * FROM product_audit WHERE shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],)).fetchall(); c.close()
    await update.message.reply_text("YOUR PRODUCT AUDIT\n"+("\n".join(f"Product #{r['product_id']} {r['action']}: {r['old_value']} → {r['new_value']} | {r['created_at']}" for r in rows) if rows else "Abhi koi product changes record nahi hain."))

async def setfx(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=1:
        await update.message.reply_text("Usage: /setfx INR_PER_USDT (e.g. /setfx 110)"); return
    try:
        rate=float(ctx.args[0])
        if not 0.000001 <= rate <= 1000000: raise ValueError()
    except ValueError:
        await update.message.reply_text("Valid positive conversion rate enter karein."); return
    c=db(); old=usdt_inr_rate(c)
    c.execute("INSERT INTO platform_settings(setting_key,setting_value,updated_by,updated_at) VALUES('usdt_inr_rate',?,?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_by=excluded.updated_by,updated_at=excluded.updated_at",(str(rate),ADMIN_ID,now()))
    c.execute("INSERT INTO admin_audit(admin_id,action,details,created_at) VALUES(?,?,?,?)",(ADMIN_ID,"usdt_inr_rate_changed",f"{old} -> {rate}",now()))
    c.commit(); c.close()
    await update.message.reply_text(f"Conversion rate updated: 1 USDT = ₹{rate:,.2f} INR.")

async def setfee(update,ctx):
    if not admin(update.effective_user.id): return
    if len(ctx.args)!=1: await update.message.reply_text("Usage: /setfee PERCENT (0–30)"); return
    try: pct=float(ctx.args[0]); assert 0<=pct<=30
    except Exception: await update.message.reply_text("Fee 0–30% ke darmiyan honi chahiye."); return
    c=db(); old=c.execute("SELECT setting_value FROM platform_settings WHERE setting_key='platform_fee_percent'").fetchone()
    c.execute("UPDATE platform_settings SET setting_value=?,updated_by=?,updated_at=? WHERE setting_key='platform_fee_percent'",(str(pct),ADMIN_ID,now()))
    c.execute("INSERT INTO admin_audit(admin_id,action,details,created_at) VALUES(?,?,?,?)",(ADMIN_ID,"platform_fee_changed",f"{old['setting_value'] if old else 'unset'} -> {pct}",now()))
    c.commit(); c.close(); await update.message.reply_text(f"Platform fee set to {pct:g}%.")


def main():
    if not TOKEN or not ADMIN_ID: raise RuntimeError("BOT_TOKEN and ADMIN_ID environment variables are required.")
    v3.setup(); v3.ensure_catalog_schema()
    app=ApplicationBuilder().token(TOKEN).build()
    for cmd,fn in [
      ("start",v3.start),("help",v3.help_command),("admin",v3.admin),("verifywallet",v3.verify_wallet),
      ("dashboard",dashboard),("levels",levels),("setlevel",setlevel),("products",products),
      ("setprice",lambda u,c:edit_product(u,c,"setprice")),("setstock",lambda u,c:edit_product(u,c,"setstock")),
      ("setproductlevel",setproductlevel),("myproducts",myproducts),("myorders",myorders),("history",history),
      ("mylevels",mylevels),("audit",lambda u,c:audit(u,c,"product")),("shopaudit",lambda u,c:audit(u,c,"shop")),
      ("owner",owner_audit),("buy",buy),("orders",orders),("orderdone",orderdone),("orderreject",orderreject),
      ("activate",activate),("setshoplevel",setshoplevel),("withdraw",withdraw),("myproductaudit",myproductaudit),("setfee",setfee),("setfx",setfx)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(payment_review,pattern=r"^pay:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(withdrawal_review,pattern=r"^wd:(paid|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v3.product_review,pattern=r"^product:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v3.approval))
    async def v4_text(update,ctx):
        if ctx.user_data.get("state")=="activation_network":
            await payment_network(update,ctx)
        else:
            await v3.handle_message(update,ctx)
    app.add_handler(MessageHandler(filters.PHOTO,payment_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,v4_text))
    print("SHOP IN INDIA V4 ready; polling starts now.")
    app.run_polling()

if __name__=="__main__": main()