"""V5 Telegram-chat UI: inline level selection, owner panel and deposits."""
import os, logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import bot_v3 as v3
import bot_v4 as v4

TOKEN=os.getenv("BOT_TOKEN")
ADMIN_ID=int(os.getenv("ADMIN_ID","0"))
logging.basicConfig(level=logging.INFO)
log=logging.getLogger("shop_v5")

def level_kb():
    c=v4.db(); rows=c.execute("SELECT level,activation_usdt,product_target FROM level_settings ORDER BY level").fetchall(); c.close()
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"L{r['level']} | {r['activation_usdt']:g} USDT | {r['product_target'] or 'Unlimited'} products",callback_data=f"lv:{r['level']}")] for r in rows])

def owner_kb():
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("🛍 Marketplace",callback_data="m:market"),InlineKeyboardButton("💳 Deposit",callback_data="m:deposit")],
      [InlineKeyboardButton("💸 Withdraw",callback_data="m:withdraw"),InlineKeyboardButton("👛 Wallet",callback_data="m:wallet")],
      [InlineKeyboardButton("📦 My Products",callback_data="m:products"),InlineKeyboardButton("🧾 My Orders",callback_data="m:orders")],
      [InlineKeyboardButton("📊 Profit & History",callback_data="m:history"),InlineKeyboardButton("👤 My Account",callback_data="m:profile")]
    ])

async def levels(bot,uid):
    await bot.send_message(uid,"🎯 CHOOSE YOUR SHOP LEVEL",reply_markup=level_kb())

async def start(update,ctx):
    ctx.user_data.clear(); s=v4.shop_for(update.effective_user.id)
    if not s:
        await update.message.reply_text("🌍 SHOP IN INDIA\nRegister or login:",reply_markup=ReplyKeyboardMarkup([["🏪 Register Shop"],["🔐 Login"]],resize_keyboard=True))
    elif s["status"]!="approved":
        await update.message.reply_text("⏳ Your shop application is waiting for Admin approval.")
    elif int(s["level"])<1:
        await levels(ctx.bot,update.effective_user.id)
    else:
        await update.message.reply_text(f"🏪 SHOP IN INDIA\n{s['shop_name']} | L{s['level']}\nChoose an option:",reply_markup=owner_kb())

async def approved_callback(update,ctx):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer("Admin only",show_alert=True); return
    action,sid=q.data.split(":"); c=v4.db(); row=c.execute("SELECT owner_id FROM shops WHERE id=?",(int(sid),)).fetchone(); c.close()
    await v3.approval(update,ctx)
    if action=="approve" and row: await levels(ctx.bot,row["owner_id"])

async def level_callback(update,ctx):
    q=update.callback_query; await q.answer()
    lv=int(q.data.split(":")[1]); s=v4.shop_for(q.from_user.id)
    if not s or s["status"]!="approved" or lv<=int(s["level"]): await q.edit_message_text("Approved shop and a higher level are required."); return
    c=v4.db(); cfg=c.execute("SELECT activation_usdt FROM level_settings WHERE level=?",(lv,)).fetchone()
    pending=c.execute("SELECT id FROM payment_requests WHERE owner_id=? AND payment_type='level_activation' AND level=? AND status='pending'",(q.from_user.id,lv)).fetchone()
    if pending: c.close(); await q.edit_message_text(f"Payment request #{pending['id']} is already pending."); return
    amount=float(cfg["activation_usdt"]); rate=v4.usdt_inr_rate(c)
    cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
      VALUES(?,?,'level_activation',?,?,'USDT','not_selected','pending',?)""",(q.from_user.id,s["id"],lv,amount,v4.now()))
    rid=cur.lastrowid; c.commit(); c.close()
    ctx.user_data.update({"state":"payment_proof","payment_request_id":rid})
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("USDT TRC20",callback_data=f"net:{rid}:trc"),InlineKeyboardButton("USDT BEP20",callback_data=f"net:{rid}:bep")]])
    await q.edit_message_text(f"L{lv} activation | {amount:g} USDT\nRate: 1 USDT = INR {rate:,.2f}\nINR reference: {amount*rate:,.2f}\nChoose network:",reply_markup=kb)

async def network_callback(update,ctx):
    q=update.callback_query; await q.answer(); _,rid_s,net=q.data.split(":"); rid=int(rid_s)
    network="USDT TRC20" if net=="trc" else "USDT BEP20"; address=v4.PAY_TRON if net=="trc" else v4.PAY_BSC
    c=v4.db(); r=c.execute("SELECT amount FROM payment_requests WHERE id=? AND owner_id=? AND status='pending'",(rid,q.from_user.id)).fetchone()
    if not r: c.close(); await q.edit_message_text("Payment request not found."); return
    c.execute("UPDATE payment_requests SET network=? WHERE id=?",(network,rid)); rate=v4.usdt_inr_rate(c); c.commit(); c.close()
    ctx.user_data.update({"state":"payment_proof","payment_request_id":rid})
    await q.edit_message_text(f"{network}\nAddress:\n{address}\nAmount: {float(r['amount']):g} USDT\nRate: 1 USDT = INR {rate:,.2f}\nSend the exact USDT amount, then upload payment screenshot here. Admin verifies manually.")

async def menu_callback(update,ctx):
    q=update.callback_query; await q.answer(); action=q.data[2:]; s=v4.shop_for(q.from_user.id)
    if not s or s["status"]!="approved" or int(s["level"])<1: await q.message.reply_text("Approve your shop and activate a level first."); return
    if action=="deposit":
        ctx.user_data["state"]="deposit_amount"; await q.message.reply_text("💳 Deposit: enter amount in USDT.")
    elif action=="withdraw":
        ctx.user_data["state"]="withdraw_amount"; await q.message.reply_text("💸 Withdraw: enter amount in INR. Only completed sale earnings are withdrawable.")
    elif action=="wallet":
        ctx.user_data["state"]="wallet_network"; await q.message.reply_text("Choose USDT TRC20 or USDT BEP20, then send your address.",reply_markup=ReplyKeyboardMarkup([["USDT TRC20","USDT BEP20"],["Cancel"]],resize_keyboard=True))
    elif action=="market":
        c=v4.db(); rows=c.execute("SELECT id,title,description,price,currency,stock FROM products WHERE active=1 AND stock>0 AND level_required<=? ORDER BY id DESC LIMIT 20",(s["level"],)).fetchall(); c.close()
        if not rows: await q.message.reply_text("Marketplace catalog is empty. Verified product listings must be loaded by Admin first.")
        else:
            for r in rows: await q.message.reply_text(f"🛍 {r['title']}\n{(r['description'] or '')[:500]}\nPrice: {r['currency']} {r['price']} | Stock: {r['stock']}\nProduct ID: #{r['id']} | Buy: /buy {r['id']} 1")
    elif action=="products": await v4.myproducts(q,ctx)
    elif action=="orders": await v4.myorders(q,ctx)
    elif action=="history": await v4.history(q,ctx)
    elif action=="profile":
        c=v4.db(); w=c.execute("SELECT network,verified FROM owner_wallets WHERE owner_id=?",(q.from_user.id,)).fetchone(); c.close()
        await q.message.reply_text(f"👤 {s['shop_name']}\nShop ID: {s['id']}\nLevel: L{s['level']}\nWallet: {w['network']} — {'Verified' if w['verified'] else 'Pending'}" if w else f"👤 {s['shop_name']} | L{s['level']}\nWallet: Not saved")

async def text_handler(update,ctx):
    state=ctx.user_data.get("state")
    if state=="deposit_amount":
        try: amount=round(float(update.message.text),6)
        except: await update.message.reply_text("Valid USDT amount enter karein."); return
        if amount<=0: await update.message.reply_text("Amount must be positive."); return
        s=v4.shop_for(update.effective_user.id); c=v4.db()
        cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
          VALUES(?,?,'wallet_deposit',0,?,'USDT','not_selected','pending',?)""",(update.effective_user.id,s["id"],amount,v4.now()))
        rid=cur.lastrowid; rate=v4.usdt_inr_rate(c); c.commit(); c.close()
        ctx.user_data.update({"state":"payment_proof","payment_request_id":rid})
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("USDT TRC20",callback_data=f"net:{rid}:trc"),InlineKeyboardButton("USDT BEP20",callback_data=f"net:{rid}:bep")]])
        await update.message.reply_text(f"Deposit #{rid}: {amount:g} USDT | Rate: 1 USDT = INR {rate:,.2f}\nChoose network:",reply_markup=kb)
    elif state=="withdraw_amount":
        try: amount=round(float(update.message.text),2)
        except: await update.message.reply_text("Valid INR amount enter karein."); return
        s=v4.shop_for(update.effective_user.id); c=v4.db()
        w=c.execute("SELECT * FROM owner_wallets WHERE owner_id=? AND verified=1",(update.effective_user.id,)).fetchone()
        earned=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND entry_type='sale_credit'",(update.effective_user.id,)).fetchone()[0])
        paid=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND entry_type='withdrawal_debit'",(update.effective_user.id,)).fetchone()[0])
        pending=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE owner_id=? AND status='pending'",(update.effective_user.id,)).fetchone()[0])
        if not w or amount<=0 or amount>earned-paid-pending: c.close(); await update.message.reply_text("Wallet must be verified and amount must be within available sale balance."); return
        rate=v4.usdt_inr_rate(c); usdt=round(amount/rate,6)
        cur=c.execute("""INSERT INTO withdrawals(owner_id,shop_id,amount,currency,network,wallet_address,status,created_at,amount_inr,exchange_rate)
          VALUES(?,?,?,'INR',?,?,'pending',?,?,?)""",(update.effective_user.id,s["id"],amount,w["network"],w["address"],v4.now(),amount,rate))
        wid=cur.lastrowid; c.commit(); c.close()
        await update.message.reply_text(f"Withdrawal #{wid} pending Admin review. INR ₹{amount:,.2f} | Rate ₹{rate:,.2f}/USDT | Equivalent {usdt:g} USDT.")
        await ctx.bot.send_message(ADMIN_ID,f"Withdrawal #{wid} from {update.effective_user.id}: INR ₹{amount:,.2f}, {usdt:g} USDT equivalent. Wallet {w['network']} {w['address']}. Send payout manually before marking paid.")
        ctx.user_data.clear()
    else: await v3.handle_message(update,ctx)

async def dashboard(update,ctx):
    if update.effective_user.id==ADMIN_ID: await v4.dashboard(update,ctx); return
    s=v4.shop_for(update.effective_user.id)
    if not s: await start(update,ctx)
    elif s["status"]!="approved": await update.message.reply_text("Awaiting Admin approval.")
    elif int(s["level"])<1: await levels(ctx.bot,update.effective_user.id)
    else: await update.message.reply_text(f"🏪 {s['shop_name']} | L{s['level']}",reply_markup=owner_kb())

def main():
    if not TOKEN or not ADMIN_ID: raise RuntimeError("BOT_TOKEN and ADMIN_ID are required.")
    v3.setup(); v3.ensure_catalog_schema()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("dashboard",dashboard))
    for cmd,fn in [("help",v3.help_command),("admin",v3.admin),("verifywallet",v3.verify_wallet),
      ("buy",v4.buy),("setlevel",v4.setlevel),("setfx",v4.setfx),("setfee",v4.setfee),
      ("products",v4.products),("setprice",lambda u,c:v4.edit_product(u,c,"setprice")),("setstock",lambda u,c:v4.edit_product(u,c,"setstock")),
      ("orders",v4.orders),("orderdone",v4.orderdone),("orderreject",v4.orderreject),("myproducts",v4.myproducts),("myorders",v4.myorders),("history",v4.history),("levels",v4.levels)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(approved_callback,pattern=r"^(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(level_callback,pattern=r"^lv:[1-5]$"))
    app.add_handler(CallbackQueryHandler(network_callback,pattern=r"^net:[0-9]+:(trc|bep)$"))
    app.add_handler(CallbackQueryHandler(menu_callback,pattern=r"^m:(market|deposit|withdraw|wallet|products|orders|history|profile)$"))
    app.add_handler(CallbackQueryHandler(v4.payment_review,pattern=r"^pay:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v4.withdrawal_review,pattern=r"^wd:(paid|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v3.product_review,pattern=r"^product:(approve|reject):[0-9]+$"))
    app.add_handler(MessageHandler(filters.PHOTO,v4.payment_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    print("SHOP IN INDIA V5 Telegram UI started.")
    app.run_polling()

if __name__=="__main__": main()
