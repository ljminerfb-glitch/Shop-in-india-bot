"""Shop in India V4 add-on. Run migrate_v4.py, then python bot_v4.py.
Keeps V3 registration, approval, product submission and wallet flows.
"""
import os, sqlite3, logging
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
import bot_v3 as v3

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB = os.getenv("SHOP_DB", v3.DB)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shop_v4")

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c=sqlite3.connect(DB, timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c
def admin(uid): return uid == ADMIN_ID
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
          f"Orders: {counts['v4_orders']} | Payments: {counts['payment_history']} | Withdrawals: {counts['withdrawals']}\n\n"
          "Commands:\n/levels — edit level settings\n/setlevel LEVEL ACTIVATION_USDT PRODUCT_TARGET PROFIT_MIN PROFIT_MAX\n"
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
          "Use /myproducts, /myorders, /history, /mylevels and /mywallet.")
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
        if buyer["level"]<target:
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
    await update.message.reply_text(f"Order #{oid} marked completed. Seller credit recorded as INR {r['seller_amount']:.2f}.")
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
    await update.message.reply_text(f"Order #{oid} rejected and reserved stock restored.")

async def main():
    if not TOKEN or not ADMIN_ID: raise RuntimeError("BOT_TOKEN and ADMIN_ID environment variables are required.")
    v3.setup(); v3.ensure_catalog_schema()
    app=ApplicationBuilder().token(TOKEN).build()
    for cmd,fn in [
      ("start",v3.start),("help",v3.help_command),("admin",v3.admin),("verifywallet",v3.verify_wallet),
      ("dashboard",dashboard),("levels",levels),("setlevel",setlevel),("products",products),
      ("setprice",lambda u,c:edit_product(u,c,"setprice")),("setstock",lambda u,c:edit_product(u,c,"setstock")),
      ("setproductlevel",setproductlevel),("myproducts",myproducts),("myorders",myorders),("history",history),
      ("mylevels",mylevels),("audit",lambda u,c:audit(u,c,"product")),("shopaudit",lambda u,c:audit(u,c,"shop")),
      ("owner",owner_audit),("buy",buy),("orders",orders),("orderdone",orderdone),("orderreject",orderreject)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(v3.product_review,pattern=r"^product:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v3.approval))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,v3.handle_message))
    print("SHOP IN INDIA V4 ready; polling starts now.")
    app.run_polling()

if __name__=="__main__": import asyncio; asyncio.run(main())
