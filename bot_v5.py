"""V5 Telegram-chat UI: inline level selection, owner panel and deposits."""
import os, logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import bot_v3 as v3
import bot_v4 as v4

TOKEN=os.getenv("BOT_TOKEN")
ADMIN_ID=int(os.getenv("ADMIN_ID","0"))
logging.basicConfig(level=logging.INFO)
log=logging.getLogger("shop_v5")
v3.main_keyboard=lambda: ReplyKeyboardRemove()

def level_kb():
    c=v4.db()
    rows=c.execute("SELECT level,activation_usdt,product_target FROM level_settings ORDER BY level").fetchall()
    c.close()
    buttons=[]
    for r in rows:
        target = "Unlimited" if not r["product_target"] else f"{r['product_target']} products"
        buttons.append([InlineKeyboardButton(
            f"🔵 LEVEL {r['level']}  •  {r['activation_usdt']:g} USDT  •  {target}",
            callback_data=f"lv:{r['level']}")])
    return InlineKeyboardMarkup(buttons)

def owner_kb():
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("🛍 Marketplace",callback_data="m:market"),InlineKeyboardButton("💳 Deposit",callback_data="m:deposit")],
      [InlineKeyboardButton("💸 Withdraw",callback_data="m:withdraw"),InlineKeyboardButton("👛 Wallet",callback_data="m:wallet")],
      [InlineKeyboardButton("📦 My Products",callback_data="m:products"),InlineKeyboardButton("🧾 My Orders",callback_data="m:orders")],
      [InlineKeyboardButton("📊 Profit & History",callback_data="m:history"),InlineKeyboardButton("👤 My Account",callback_data="m:profile")],
      [InlineKeyboardButton("💳 DEPOSIT FUNDS",callback_data="m:deposit")]
    ])


async def edit_screen(ctx, update, text, reply_markup=None):
    """Keep navigation in one bot message; fall back safely if it cannot be edited."""
    q = getattr(update, "callback_query", None)
    if q:
        chat_id = q.message.chat_id
        message_id = q.message.message_id
    else:
        chat_id = update.effective_chat.id
        message_id = ctx.user_data.get("screen_message_id")
    if message_id:
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=text, reply_markup=reply_markup)
            return
        except Exception:
            log.exception("Could not edit the current menu message")
    sent = await ctx.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    ctx.user_data["screen_message_id"] = sent.message_id
    ctx.user_data["screen_chat_id"] = chat_id


def back_dashboard_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="m:dashboard")]])


async def levels(bot,uid):
    await bot.send_message(
        uid,
        "🔷 SHOP IN INDIA\n━━━━━━━━━━━━━━━━━━\n"
        "🎯 SELECT YOUR SHOP LEVEL\n\n"
        "Choose the level you want to activate.\n"
        "After selection, choose your USDT payment network.\n\n"
        "🔵 Blue buttons = available levels",
        reply_markup=level_kb())

async def start(update,ctx):
    ctx.user_data.clear()
    s=v4.shop_for(update.effective_user.id)
    if not s:
        await update.message.reply_text(
            "🔷 SHOP IN INDIA\n━━━━━━━━━━━━━━━━━━\n"
            "Welcome! Build and manage your online shop.\n\n"
            "Choose an option to continue:",
            reply_markup=ReplyKeyboardMarkup(
                [["🏪 Register Shop"],["🔐 Login"]],
                resize_keyboard=True))
    elif s["status"]!="approved":
        await update.message.reply_text(
            "🔷 SHOP IN INDIA\n━━━━━━━━━━━━━━━━━━\n"
            "⏳ Your shop application is waiting for Admin approval.\n"
            "You will receive a message when it is approved.")
    elif int(s["level"])<1:
        await levels(ctx.bot,update.effective_user.id)
    else:
        await update.message.reply_text(
            f"🔷 SHOP IN INDIA\n━━━━━━━━━━━━━━━━━━\n"
            f"🏪 {s['shop_name']}  •  LEVEL {s['level']}\n\n"
            "Welcome to your Owner Dashboard. Select an option:",
            reply_markup=owner_kb())

async def approved_callback(update,ctx):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer("Admin only",show_alert=True); return
    action,sid=q.data.split(":"); c=v4.db(); row=c.execute("SELECT owner_id,level FROM shops WHERE id=?",(int(sid),)).fetchone(); c.close()
    await v3.approval(update,ctx)
    if action=="approve" and row:
        if int(row["level"])<1: await levels(ctx.bot,row["owner_id"])
        else: await ctx.bot.send_message(row["owner_id"],"🏪 Your shop is approved and level is active. Open /start for your owner panel.",reply_markup=owner_kb())

async def level_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    try:
        lv=int(q.data.split(":")[1])
    except (ValueError,IndexError):
        await q.edit_message_text("Invalid level. Please open /start and try again.")
        return

    s=v4.shop_for(q.from_user.id)
    if not s:
        await q.edit_message_text("Please register your shop first.")
        return
    if s["status"]!="approved":
        await q.edit_message_text("Your shop must be approved before selecting a level.")
        return
    if lv<=int(s["level"]):
        await q.edit_message_text("This level is already active. Use /start to open your dashboard.")
        return

    c=v4.db()
    try:
        cfg=c.execute(
            "SELECT level,activation_usdt,product_target,profit_min,profit_max "
            "FROM level_settings WHERE level=?",(lv,)).fetchone()
    finally:
        c.close()
    if not cfg:
        await q.edit_message_text("This level is not configured. Please contact Admin.")
        return

    target="Unlimited product access" if not cfg["product_target"] else f"Access to up to {cfg['product_target']} products"
    benefits=(
        f"📦 Product access: {target}\n"
        f"📈 Profit/margin reference: {float(cfg['profit_min']):g}%–{float(cfg['profit_max']):g}%\n"
        "🔓 Marketplace access according to this level"
    )
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💳 Continue to payment • {float(cfg['activation_usdt']):g} USDT",callback_data=f"lvpay:{lv}")],
        [InlineKeyboardButton("⬅️ Back to all levels",callback_data="lvback")]
    ])
    await q.edit_message_text(
        f"🔷 SHOP IN INDIA — LEVEL {lv}\n━━━━━━━━━━━━━━━━━━\n"
        f"💰 Activation price: {float(cfg['activation_usdt']):g} USDT\n"
        f"🇮🇳 INR reference: ₹{float(cfg['activation_usdt'])*v4.usdt_inr_rate():,.2f}\n\n"
        f"🎁 LEVEL BENEFITS\n{benefits}\n\n"
        "Note: Profit/margin figures are references, not guaranteed earnings. "
        "Level activates only after Admin verifies your payment.",
        reply_markup=kb)

async def level_pay_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    try:
        lv=int(q.data.split(":")[1])
    except (ValueError,IndexError):
        await q.edit_message_text("Invalid level. Please open /start and try again.")
        return

    s=v4.shop_for(q.from_user.id)
    if not s or s["status"]!="approved":
        await q.edit_message_text("An approved shop is required before payment.")
        return
    if lv<=int(s["level"]):
        await q.edit_message_text("This level is already active. Use /start to open your dashboard.")
        return

    c=v4.db()
    try:
        cfg=c.execute("SELECT activation_usdt FROM level_settings WHERE level=?",(lv,)).fetchone()
        if not cfg:
            await q.edit_message_text("This level is not configured. Please contact Admin.")
            return
        pending=c.execute(
            "SELECT id,amount FROM payment_requests WHERE owner_id=? "
            "AND payment_type='level_activation' AND level=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1",(q.from_user.id,lv)).fetchone()
        if pending:
            rid=int(pending["id"])
            amount=float(pending["amount"])
        else:
            amount=float(cfg["activation_usdt"])
            cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
              VALUES(?,?,'level_activation',?,?,'USDT','not_selected','pending',?)""",
              (q.from_user.id,s["id"],lv,amount,v4.now()))
            rid=cur.lastrowid
        rate=v4.usdt_inr_rate(c)
        c.commit()
    finally:
        c.close()

    ctx.user_data.update({"state":"payment_proof","payment_request_id":rid})
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton("🔵 USDT TRC20",callback_data=f"net:{rid}:trc"),
        InlineKeyboardButton("🔵 USDT BEP20",callback_data=f"net:{rid}:bep")
    ]])
    await q.edit_message_text(
        f"🔷 LEVEL {lv} PAYMENT\n━━━━━━━━━━━━━━━━━━\n"
        f"Payment request: #{rid}\n"
        f"Amount: {amount:g} USDT\n"
        f"Rate: 1 USDT = INR {rate:,.2f}\n"
        f"INR reference: ₹{amount*rate:,.2f}\n\n"
        "Choose your payment network:",
        reply_markup=kb)

async def level_back_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔷 SHOP IN INDIA\n━━━━━━━━━━━━━━━━━━\n"
        "🎯 SELECT YOUR SHOP LEVEL\n\nChoose a level to view its price and benefits.",
        reply_markup=level_kb())

async def network_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    _,rid_s,net=q.data.split(":")
    rid=int(rid_s)
    network="USDT TRC20" if net=="trc" else "USDT BEP20"
    address=v4.PAY_TRON if net=="trc" else v4.PAY_BSC
    c=v4.db()
    r=c.execute("SELECT amount FROM payment_requests WHERE id=? AND owner_id=? AND status='pending'",(rid,q.from_user.id)).fetchone()
    if not r:
        c.close()
        await q.edit_message_text("Payment request not found.",reply_markup=back_dashboard_kb())
        return
    c.execute("UPDATE payment_requests SET network=? WHERE id=?",(network,rid))
    rate=v4.usdt_inr_rate(c)
    c.commit()
    c.close()
    ctx.user_data.update({"state":"payment_proof","payment_request_id":rid,
                          "screen_message_id":q.message.message_id,"screen_chat_id":q.message.chat_id})
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]])
    await q.edit_message_text(
        f"{network}\n━━━━━━━━━━━━━━━━━━\nAddress:\n{address}\n"
        f"Amount: {float(r['amount']):g} USDT\nRate: 1 USDT = INR {rate:,.2f}\n\n"
        "Send the exact USDT amount on this network, then upload the payment screenshot here. Admin verifies manually.",
        reply_markup=kb)


async def menu_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    action=q.data[2:]
    ctx.user_data["screen_message_id"]=q.message.message_id
    ctx.user_data["screen_chat_id"]=q.message.chat_id
    s=v4.shop_for(q.from_user.id)

    if action=="dashboard":
        if not s or s["status"]!="approved" or int(s["level"])<1:
            await q.edit_message_text("Approved shop aur active level zaroori hai.")
            return
        ctx.user_data.clear()
        ctx.user_data["screen_message_id"]=q.message.message_id
        ctx.user_data["screen_chat_id"]=q.message.chat_id
        await q.edit_message_text(
            f"🔷 SHOP IN INDIA\n━━━━━━━━━━━━━━━━━━\n🏪 {s['shop_name']} • LEVEL {s['level']}\n\nOWNER DASHBOARD\nSelect an option:",
            reply_markup=owner_kb())
        return

    if not s or s["status"]!="approved" or int(s["level"])<1:
        await q.edit_message_text("Approve your shop and activate a level first.",
                                  reply_markup=back_dashboard_kb())
        return

    if action=="deposit":
        ctx.user_data["state"]="deposit_amount"
        await q.edit_message_text(
            "💳 DEPOSIT FUNDS\n━━━━━━━━━━━━━━━━━━\n"
            "Enter the amount of USDT you want to deposit.\n"
            "Minimum amount must be greater than zero.\n\n"
            "Payment network and address will appear after amount entry. Admin manually verifies the screenshot.",
            reply_markup=back_dashboard_kb())
    elif action=="withdraw":
        ctx.user_data["state"]="withdraw_amount"
        await q.edit_message_text(
            "💸 WITHDRAW FUNDS\n━━━━━━━━━━━━━━━━━━\n"
            "Enter withdrawal amount in INR. Only completed sale earnings are withdrawable.",
            reply_markup=back_dashboard_kb())
    elif action=="wallet":
        ctx.user_data["state"]="wallet_network"
        await q.edit_message_text(
            "👛 WALLET SETTINGS\nWallet setup is managed through the wallet verification flow. Use /verifywallet or contact Admin.",
            reply_markup=back_dashboard_kb())
    elif action=="market":
        c=v4.db()
        rows=c.execute("SELECT id,title,description,price,currency,stock FROM products WHERE active=1 AND stock>0 AND level_required<=? ORDER BY id DESC LIMIT 20",(s["level"],)).fetchall()
        c.close()
        body="🛍 MARKETPLACE\n━━━━━━━━━━━━━━━━━━\n"
        body += ("\n\n".join(f"#{r['id']} {r['title']}\n{(r['description'] or '')[:250]}\nPrice: {r['currency']} {r['price']} | Stock: {r['stock']}\nBuy: /buy {r['id']} 1" for r in rows)
                 if rows else "Catalog is empty. Admin must add verified products first.")
        await q.edit_message_text(body[:4000],reply_markup=back_dashboard_kb())
    elif action=="products":
        c=v4.db()
        rows=c.execute("SELECT id,title,price,currency,stock,active FROM products WHERE shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],)).fetchall()
        c.close()
        body="📦 MY PRODUCTS\n━━━━━━━━━━━━━━━━━━\n"+("\n".join(f"#{r['id']} {r['title']} — {r['currency']} {r['price']} | stock {r['stock']} | active {r['active']}" for r in rows) if rows else "No products assigned to your shop.")
        await q.edit_message_text(body[:4000],reply_markup=back_dashboard_kb())
    elif action=="orders":
        c=v4.db()
        rows=c.execute("SELECT id,product_id,quantity,total_amount,status FROM v4_orders WHERE buyer_shop_id=? OR seller_shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],s["id"])).fetchall()
        c.close()
        body="🧾 MY ORDERS\n━━━━━━━━━━━━━━━━━━\n"+("\n".join(f"#{r['id']} Product #{r['product_id']} ×{r['quantity']} — ₹{r['total_amount']:.2f} — {r['status']}" for r in rows) if rows else "No orders yet.")
        await q.edit_message_text(body,reply_markup=back_dashboard_kb())
    elif action=="history":
        c=v4.db()
        rows=c.execute("SELECT entry_type,amount,currency,note,created_at FROM ledger WHERE owner_id=? ORDER BY id DESC LIMIT 20",(q.from_user.id,)).fetchall()
        c.close()
        body="📊 PROFIT & HISTORY\n━━━━━━━━━━━━━━━━━━\n"+("\n".join(f"{r['entry_type']} — {r['currency']} {r['amount']} — {r['note'] or ''} — {r['created_at']}" for r in rows) if rows else "No history yet.")
        await q.edit_message_text(body[:4000],reply_markup=back_dashboard_kb())
    elif action=="profile":
        c=v4.db()
        w=c.execute("SELECT network,verified FROM owner_wallets WHERE owner_id=?",(q.from_user.id,)).fetchone()
        c.close()
        body=(f"👤 {s['shop_name']}\nShop ID: {s['id']}\nLevel: L{s['level']}\nWallet: {w['network']} — {'Verified' if w['verified'] else 'Pending'}"
              if w else f"👤 {s['shop_name']} | L{s['level']}\nWallet: Not saved")
        await q.edit_message_text(body,reply_markup=back_dashboard_kb())


async def text_handler(update,ctx):
    state=ctx.user_data.get("state")
    if state=="deposit_amount":
        try:
            amount=round(float(update.message.text),6)
        except (TypeError,ValueError):
            await edit_screen(ctx,update,"Valid USDT amount enter karein.",reply_markup=back_dashboard_kb())
            return
        if amount<=0:
            await edit_screen(ctx,update,"Amount must be greater than zero.",reply_markup=back_dashboard_kb())
            return
        s=v4.shop_for(update.effective_user.id)
        if not s or s["status"]!="approved":
            await edit_screen(ctx,update,"Approved shop required.",reply_markup=back_dashboard_kb())
            return
        c=v4.db()
        cur=c.execute("""INSERT INTO payment_requests(owner_id,shop_id,payment_type,level,amount,currency,network,status,created_at)
          VALUES(?,?,'wallet_deposit',0,?,'USDT','not_selected','pending',?)""",(update.effective_user.id,s["id"],amount,v4.now()))
        rid=cur.lastrowid
        rate=v4.usdt_inr_rate(c)
        c.commit()
        c.close()
        ctx.user_data.update({"state":"payment_proof","payment_request_id":rid})
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔵 USDT TRC20",callback_data=f"net:{rid}:trc"),
             InlineKeyboardButton("🔵 USDT BEP20",callback_data=f"net:{rid}:bep")],
            [InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]
        ])
        await edit_screen(ctx,update,
            f"💳 DEPOSIT REQUEST #{rid}\nAmount: {amount:g} USDT\nRate: 1 USDT = INR {rate:,.2f}\n\nChoose payment network:",
            reply_markup=kb)
    elif state=="withdraw_amount":
        try:
            amount=round(float(update.message.text),2)
        except (TypeError,ValueError):
            await edit_screen(ctx,update,"Valid INR amount enter karein.",reply_markup=back_dashboard_kb())
            return
        s=v4.shop_for(update.effective_user.id)
        c=v4.db()
        w=c.execute("SELECT * FROM owner_wallets WHERE owner_id=? AND verified=1",(update.effective_user.id,)).fetchone()
        earned=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND entry_type='sale_credit'",(update.effective_user.id,)).fetchone()[0])
        paid=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND entry_type='withdrawal_debit'",(update.effective_user.id,)).fetchone()[0])
        pending=float(c.execute("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE owner_id=? AND status='pending'",(update.effective_user.id,)).fetchone()[0])
        if not s or not w or amount<=0 or amount>earned-paid-pending:
            c.close()
            await edit_screen(ctx,update,"Wallet must be verified and amount must be within available sale balance.",reply_markup=back_dashboard_kb())
            return
        rate=v4.usdt_inr_rate(c)
        usdt=round(amount/rate,6)
        cur=c.execute("""INSERT INTO withdrawals(owner_id,shop_id,amount,currency,network,wallet_address,status,created_at,amount_inr,exchange_rate)
          VALUES(?,?,?,'INR',?,?,'pending',?,?,?)""",(update.effective_user.id,s["id"],amount,w["network"],w["address"],v4.now(),amount,rate))
        wid=cur.lastrowid
        c.commit()
        c.close()
        await edit_screen(ctx,update,
            f"Withdrawal #{wid} pending Admin review.\nINR ₹{amount:,.2f} | Rate ₹{rate:,.2f}/USDT | Equivalent {usdt:g} USDT.",
            reply_markup=back_dashboard_kb())
        await ctx.bot.send_message(ADMIN_ID,f"Withdrawal #{wid} from {update.effective_user.id}: INR ₹{amount:,.2f}, {usdt:g} USDT equivalent. Wallet {w['network']} {w['address']}. Send payout manually before marking paid.")
        ctx.user_data.pop("state",None)
    else:
        was_registering=state=="shop_password"
        await v3.handle_message(update,ctx)
        if was_registering and v4.shop_for(update.effective_user.id):
            await levels(ctx.bot,update.effective_user.id)


async def admin_orders(update,ctx):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin only.")
        return
    c=v4.db()
    rows=c.execute("""SELECT o.id,o.product_id,o.quantity,o.total_amount,o.status,
                             p.title,o.buyer_owner_id,o.seller_owner_id,
                             bs.shop_name AS buyer_shop,ss.shop_name AS seller_shop
                      FROM v4_orders o
                      JOIN products p ON p.id=o.product_id
                      JOIN shops bs ON bs.id=o.buyer_shop_id
                      JOIN shops ss ON ss.id=o.seller_shop_id
                      WHERE o.status NOT IN ('delivered','completed','rejected')
                      ORDER BY o.id DESC LIMIT 25""").fetchall()
    c.close()
    if not rows:
        await update.message.reply_text("No orders awaiting delivery.")
        return
    for r in rows:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Mark Delivered",callback_data=f"order:delivered:{r['id']}")]])
        await update.message.reply_text(
            f"🛒 ORDER #{r['id']}\n{r['title']} × {r['quantity']}\n"
            f"Buyer: {r['buyer_shop']} (ID {r['buyer_owner_id']})\n"
            f"Seller: {r['seller_shop']} (ID {r['seller_owner_id']})\n"
            f"Total: INR ₹{r['total_amount']:.2f}\nStatus: {r['status']}\n\n"
            "Supplier se delivery confirm hone ke baad hi Delivered click karein.",
            reply_markup=kb)

async def delivered_callback(update,ctx):
    q=update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Admin only",show_alert=True)
        return
    await q.answer()
    try:
        oid=int(q.data.rsplit(":",1)[1])
    except (ValueError,IndexError):
        await q.edit_message_text("Invalid order.")
        return
    c=v4.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        order=c.execute("SELECT * FROM v4_orders WHERE id=?",(oid,)).fetchone()
        if not order:
            c.rollback()
            await q.edit_message_text("Order not found.")
            return
        if order["status"] in ("delivered","completed","rejected"):
            c.rollback()
            await q.edit_message_text(f"Order #{oid} is already {order['status']}.")
            return
        c.execute("UPDATE v4_orders SET status='delivered',completed_at=?,updated_at=? WHERE id=?",
                  (v4.now(),v4.now(),oid))
        c.execute("""INSERT INTO ledger(transaction_id,shop_id,owner_id,entry_type,amount,currency,note,created_at)
                     VALUES(?,?,?,'sale_credit',?,'INR',?,?)""",
                  (oid,order["seller_shop_id"],order["seller_owner_id"],order["seller_amount"],
                   f"Delivered order #{oid}",v4.now()))
        c.execute("""INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at)
                     VALUES(?,?,?,?,?)""",
                  (ADMIN_ID,order["seller_owner_id"],"order_delivered",f"Order #{oid}",v4.now()))
        c.commit()
    except Exception:
        c.rollback()
        log.exception("Mark delivered failed")
        await q.edit_message_text("Could not update order. No delivery credit was recorded.")
        return
    finally:
        c.close()
    await q.edit_message_text(
        f"✅ Order #{oid} marked DELIVERED.\n"
        f"Seller credit recorded: INR ₹{float(order['seller_amount']):.2f}")
    for uid in {int(order["buyer_owner_id"]),int(order["seller_owner_id"])}:
        try:
            await ctx.bot.send_message(uid,
                f"📦 Order #{oid} status: DELIVERED. "
                + (f"Your sale credit is INR ₹{float(order['seller_amount']):.2f}."
                   if uid==int(order["seller_owner_id"]) else ""))
        except Exception:
            log.exception("Delivered notification failed")

async def addproduct(update,ctx):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text("Admin only."); return
    raw=" ".join(ctx.args)
    # Syntax: /addproduct SHOP_ID PRICE STOCK LEVEL Title | Description
    try:
        head,body=raw.split(" ",4)[0:4],None
        parts=raw.split(maxsplit=4)
        if len(parts)!=5: raise ValueError()
        shop_id,price,stock,level=int(parts[0]),float(parts[1]),int(parts[2]),int(parts[3])
        title_desc=parts[4]
        if "|" not in title_desc: raise ValueError()
        title,description=[x.strip() for x in title_desc.split("|",1)]
        if not title or not description or price<=0 or stock<0 or level not in range(1,6): raise ValueError()
    except Exception:
        await update.message.reply_text("Usage:\n/addproduct SHOP_ID PRICE STOCK LEVEL Title | Full description"); return
    c=v4.db()
    shop=c.execute("SELECT id,owner_id,status FROM shops WHERE id=?",(shop_id,)).fetchone()
    if not shop or shop["status"]!="approved":
        c.close(); await update.message.reply_text("Approved seller shop ID not found."); return
    cols={r["name"] for r in c.execute("PRAGMA table_info(products)").fetchall()}
    required={"shop_id","title","description","price","currency","active","stock"}
    if not required.issubset(cols):
        c.close(); await update.message.reply_text("Products table schema mismatch; no product added."); return
    fields=["shop_id","title","description","price","currency","active","stock"]
    vals=[shop_id,title,description,price,"INR",1,stock]
    if "level_required" in cols: fields.append("level_required"); vals.append(level)
    if "created_at" in cols: fields.append("created_at"); vals.append(v4.now())
    if "updated_at" in cols: fields.append("updated_at"); vals.append(v4.now())
    marks=",".join("?" for _ in fields)
    cur=c.execute(f"INSERT INTO products ({','.join(fields)}) VALUES ({marks})",vals)
    pid=cur.lastrowid
    c.commit(); c.close()
    await update.message.reply_text(f"Product published by Admin. ID #{pid}\n{title}\nINR {price:,.2f} | Stock {stock} | Required level L{level}")
    try: await ctx.bot.send_message(shop["owner_id"],f"Admin added a marketplace product to your shop: #{pid} {title}")
    except Exception: pass

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
      ("buy",v4.buy),("addproduct",addproduct),("setlevel",v4.setlevel),("setfx",v4.setfx),("setfee",v4.setfee),
      ("products",v4.products),("setprice",lambda u,c:v4.edit_product(u,c,"setprice")),("setstock",lambda u,c:v4.edit_product(u,c,"setstock")),
      ("orders",admin_orders),("orderdone",v4.orderdone),("orderreject",v4.orderreject),("myproducts",v4.myproducts),("myorders",v4.myorders),("history",v4.history),("levels",v4.levels)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(approved_callback,pattern=r"^(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(level_callback,pattern=r"^lv:[1-5]$"))
    app.add_handler(CallbackQueryHandler(level_pay_callback,pattern=r"^lvpay:[1-5]$"))
    app.add_handler(CallbackQueryHandler(level_back_callback,pattern=r"^lvback$"))
    app.add_handler(CallbackQueryHandler(network_callback,pattern=r"^net:[0-9]+:(trc|bep)$"))
    app.add_handler(CallbackQueryHandler(menu_callback,pattern=r"^m:(market|deposit|withdraw|wallet|products|orders|history|profile|dashboard)$"))
    app.add_handler(CallbackQueryHandler(delivered_callback,pattern=r"^order:delivered:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v4.payment_review,pattern=r"^pay:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v4.withdrawal_review,pattern=r"^wd:(paid|reject):[0-9]+$"))
    app.add_handler(MessageHandler(filters.PHOTO,v4.payment_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    print("SHOP IN INDIA V5 Telegram UI started.")
    app.run_polling()

if __name__=="__main__": main()
