"""V5 Telegram-chat UI: inline level selection, owner panel and deposits."""
import os, logging, re, html
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, CopyTextButton
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
      [InlineKeyboardButton("🧾 Deposits",callback_data="m:deposits")],
      [InlineKeyboardButton("💬 Support",callback_data="m:support")]
    ])


async def edit_screen(ctx, update, text, reply_markup=None):
    """Append a new screen; never overwrite earlier chat messages."""
    sent = await ctx.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    ctx.user_data["screen_message_id"] = sent.message_id
    ctx.user_data["screen_chat_id"] = sent.chat_id
    return sent


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
                [["🏪 Register Shop"],["🔐 Login"],["💬 Support"]],
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
        await q.message.reply_text("Invalid level. Please open /start and try again.")
        return

    s=v4.shop_for(q.from_user.id)
    if not s:
        await q.message.reply_text("Please register your shop first.")
        return
    if s["status"]!="approved":
        await q.message.reply_text("Your shop must be approved before selecting a level.")
        return
    if lv<=int(s["level"]):
        await q.message.reply_text("This level is already active. Use /start to open your dashboard.")
        return

    c=v4.db()
    try:
        cfg=c.execute(
            "SELECT level,activation_usdt,product_target,profit_min,profit_max "
            "FROM level_settings WHERE level=?",(lv,)).fetchone()
    finally:
        c.close()
    if not cfg:
        await q.message.reply_text("This level is not configured. Please contact Admin.")
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
    await q.message.reply_text(
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
        await q.message.reply_text("Invalid level. Please open /start and try again.")
        return

    s=v4.shop_for(q.from_user.id)
    if not s or s["status"]!="approved":
        await q.message.reply_text("An approved shop is required before payment.")
        return
    if lv<=int(s["level"]):
        await q.message.reply_text("This level is already active. Use /start to open your dashboard.")
        return

    c=v4.db()
    try:
        cfg=c.execute("SELECT activation_usdt FROM level_settings WHERE level=?",(lv,)).fetchone()
        if not cfg:
            await q.message.reply_text("This level is not configured. Please contact Admin.")
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
    await q.message.reply_text(
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
    await q.message.reply_text(
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
    r=c.execute("SELECT amount,payment_type FROM payment_requests WHERE id=? AND owner_id=? AND status='pending'",(rid,q.from_user.id)).fetchone()
    if not r:
        c.close()
        await q.message.reply_text("Payment request not found.",reply_markup=back_dashboard_kb())
        return
    c.execute("UPDATE payment_requests SET network=? WHERE id=?",(network,rid))
    rate=v4.usdt_inr_rate(c)
    c.commit()
    c.close()
    is_wallet_deposit=(r["payment_type"]=="wallet_deposit")
    ctx.user_data.update({"state":"await_deposit_done" if is_wallet_deposit else "payment_proof","payment_request_id":rid,
                          "screen_message_id":q.message.message_id,"screen_chat_id":q.message.chat_id})
    rows=[[InlineKeyboardButton("📋 Copy Deposit Address",copy_text=CopyTextButton(text=address))]]
    if is_wallet_deposit:
        rows.append([InlineKeyboardButton("✅ I HAVE DONE DEPOSIT",callback_data="m:depositdone")])
    rows.append([InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")])
    kb=InlineKeyboardMarkup(rows)
    await q.message.reply_text(
        f"💳 <b>{network} DEPOSIT ADDRESS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"<b><i>Address:</i></b>\n\n<code>{address}</code>\n\n"
        f"<b>Amount:</b> {float(r['amount']):g} USDT\n"
        f"<b>Rate:</b> 1 USDT = INR {rate:,.2f}\n\n"
        "<i>Send USDT only on the selected network. After payment, upload your transaction screenshot. Admin will verify it manually.</i>",
        reply_markup=kb,parse_mode="HTML")



async def menu_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    action=q.data[2:]
    ctx.user_data["screen_message_id"]=q.message.message_id
    ctx.user_data["screen_chat_id"]=q.message.chat_id
    s=v4.shop_for(q.from_user.id)

    if action=="dashboard":
        if not s or s["status"]!="approved" or int(s["level"])<1:
            await q.message.reply_text("Approved shop aur active level zaroori hai.")
            return
        ctx.user_data.clear()
        ctx.user_data["screen_message_id"]=q.message.message_id
        ctx.user_data["screen_chat_id"]=q.message.chat_id
        await q.message.reply_text(
            f"🔷 SHOP IN INDIA\n━━━━━━━━━━━━━━━━━━\n🏪 {s['shop_name']} • LEVEL {s['level']}\n\nOWNER DASHBOARD\nSelect an option:",
            reply_markup=owner_kb())
        return

    if not s or s["status"]!="approved" or int(s["level"])<1:
        await q.message.reply_text("Approve your shop and activate a level first.",
                                  reply_markup=back_dashboard_kb())
        return

    if action=="deposits":
        c=v4.db()
        try:
            rows=c.execute("""SELECT id,amount,currency,status,created_at,admin_note FROM payment_requests
                WHERE owner_id=? AND payment_type='wallet_deposit' ORDER BY id DESC LIMIT 30""",(q.from_user.id,)).fetchall()
        finally: c.close()
        body="<b>🧾 MY DEPOSITS</b>\\n━━━━━━━━━━━━━━━━━━\\n"
        if not rows: body+="No deposit records found."
        else:
            from datetime import datetime
            for r in rows:
                status=str(r["status"] or "pending").lower()
                label="🟢 ✅ Approved" if status=="approved" else ("🔴 ❌ Declined" if status=="rejected" else ("⚪ Cancelled" if status=="cancelled" else "🟡 Pending"))
                date_text=str(r["created_at"] or "Date unavailable")
                try: date_text=datetime.fromisoformat(date_text.replace("Z","+00:00")).strftime("%A, %d %B %Y")
                except Exception: pass
                body+=f"\\n<b>{float(r['amount']):g} {html.escape(str(r['currency'] or 'USDT'))}</b> — {label} ({html.escape(date_text)})"
                if status=="rejected": body+=f"\\n  Reason: {html.escape(str(r['admin_note'] or 'No reason provided'))}"
                body+="\\n"
        await q.message.reply_text(body,reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="depositdone":
        rid=ctx.user_data.get("payment_request_id")
        if not rid or ctx.user_data.get("state")!="await_deposit_done":
            await q.message.reply_text("Please complete the deposit amount and network selection first.",reply_markup=back_dashboard_kb()); return
        c=v4.db()
        try: row=c.execute("SELECT id FROM payment_requests WHERE id=? AND owner_id=? AND payment_type='wallet_deposit' AND status='pending'",(rid,q.from_user.id)).fetchone()
        finally: c.close()
        if not row:
            await q.message.reply_text("This deposit request is no longer pending.",reply_markup=back_dashboard_kb()); return
        ctx.user_data["state"]="deposit_tx_hash"
        await q.message.reply_text("📸 <b>Send Payment Screenshot with Transaction Hash</b>\\n━━━━━━━━━━━━━━━━━━\\nFirst send your transaction hash (TXID), then upload the payment screenshot/photo.",reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="deposit":
        ctx.user_data["state"]="deposit_amount"
        await q.message.reply_text(
            "💳 DEPOSIT FUNDS\n━━━━━━━━━━━━━━━━━━\n"
            "Enter the amount of USDT you want to deposit.\n"
            "Minimum amount must be greater than zero.\n\n"
            "Payment network and address will appear after amount entry. Admin manually verifies the screenshot.",
            reply_markup=back_dashboard_kb())
    elif action=="withdraw":
        ctx.user_data["state"]="withdraw_amount"
        await q.message.reply_text(
            "💸 WITHDRAW FUNDS\n━━━━━━━━━━━━━━━━━━\n"
            "Enter withdrawal amount in INR. Only completed sale earnings are withdrawable.",
            reply_markup=back_dashboard_kb())
    elif action=="wallet":
        ctx.user_data["state"]="wallet_network"
        await q.message.reply_text(
            "👛 WALLET SETTINGS\nWallet setup is managed through the wallet verification flow. Use /verifywallet or contact Admin.",
            reply_markup=back_dashboard_kb())
    elif action=="market":
        c=v4.db()
        rows=c.execute("SELECT id,title,description,price,currency,stock FROM products WHERE active=1 AND stock>0 AND level_required=? ORDER BY id DESC LIMIT 20",(s["level"],)).fetchall()
        c.close()
        body="🛍 MARKETPLACE\n━━━━━━━━━━━━━━━━━━\n"
        body += ("\n\n".join(f"#{r['id']} {r['title']}\n{(r['description'] or '')[:250]}\nPrice: {r['currency']} {r['price']} | Stock: {r['stock']}\nBuy: /buy {r['id']} 1" for r in rows)
                 if rows else "Catalog is empty. Admin must add verified products first.")
        await q.message.reply_text(body[:4000],reply_markup=back_dashboard_kb())
    elif action=="products":
        c=v4.db()
        rows=c.execute("SELECT id,title,price,currency,stock,active FROM products WHERE shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],)).fetchall()
        c.close()
        body="📦 MY PRODUCTS\n━━━━━━━━━━━━━━━━━━\n"+("\n".join(f"#{r['id']} {r['title']} — {r['currency']} {r['price']} | stock {r['stock']} | active {r['active']}" for r in rows) if rows else "No products assigned to your shop.")
        await q.message.reply_text(body[:4000],reply_markup=back_dashboard_kb())
    elif action=="orders":
        c=v4.db()
        rows=c.execute("SELECT id,product_id,quantity,total_amount,status FROM v4_orders WHERE buyer_shop_id=? OR seller_shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],s["id"])).fetchall()
        c.close()
        body="🧾 MY ORDERS\n━━━━━━━━━━━━━━━━━━\n"+("\n".join(f"#{r['id']} Product #{r['product_id']} ×{r['quantity']} — ₹{r['total_amount']:.2f} — {r['status']}" for r in rows) if rows else "No orders yet.")
        await q.message.reply_text(body,reply_markup=back_dashboard_kb())
    elif action=="history":
        c=v4.db()
        rows=c.execute("SELECT entry_type,amount,currency,note,created_at FROM ledger WHERE owner_id=? ORDER BY id DESC LIMIT 20",(q.from_user.id,)).fetchall()
        c.close()
        body="📊 PROFIT & HISTORY\n━━━━━━━━━━━━━━━━━━\n"+("\n".join(f"{r['entry_type']} — {r['currency']} {r['amount']} — {r['note'] or ''} — {r['created_at']}" for r in rows) if rows else "No history yet.")
        await q.message.reply_text(body[:4000],reply_markup=back_dashboard_kb())
    elif action=="profile":
        c=v4.db()
        w=c.execute("SELECT network,verified FROM owner_wallets WHERE owner_id=?",(q.from_user.id,)).fetchone()
        c.close()
        body=(f"👤 {s['shop_name']}\nShop ID: {s['id']}\nLevel: L{s['level']}\nWallet: {w['network']} — {'Verified' if w['verified'] else 'Pending'}"
              if w else f"👤 {s['shop_name']} | L{s['level']}\nWallet: Not saved")
        await q.message.reply_text(body,reply_markup=back_dashboard_kb())
    elif action=="support":
        await q.message.reply_text("💬 SUPPORT\nFor help with registration, payments, shop approval or account issues, contact the Shop in India administrator.",reply_markup=back_dashboard_kb())


async def text_handler(update,ctx):
    state=ctx.user_data.get("state")
    if state=="deposit_tx_hash":
        tx_hash=(update.message.text or "").strip()
        if len(tx_hash)<12 or len(tx_hash)>160 or not re.fullmatch(r"[A-Za-z0-9xX:_-]+",tx_hash):
            await update.message.reply_text("❌ Invalid transaction hash. Send the complete TXID/hash."); return
        rid=ctx.user_data.get("payment_request_id")
        c=v4.db()
        try:
            cur=c.execute("""UPDATE payment_requests SET transaction_hash=? WHERE id=? AND owner_id=? AND payment_type='wallet_deposit' AND status='pending'""",(tx_hash,rid,update.effective_user.id))
            c.commit()
        finally: c.close()
        if cur.rowcount!=1:
            await update.message.reply_text("Deposit request not found or already reviewed.",reply_markup=back_dashboard_kb()); ctx.user_data.pop("state",None); return
        ctx.user_data["deposit_tx_hash"]=tx_hash
        ctx.user_data["state"]="payment_proof"
        await update.message.reply_text("📸 <b>Now upload your payment screenshot/photo.</b>\\nYour transaction hash has been saved.",parse_mode="HTML",reply_markup=back_dashboard_kb())
        return
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


async def admin_panel(update,ctx):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin only."); return
    await send_admin_panel(ctx.bot, update.effective_chat.id)

async def send_admin_panel(bot, chat_id):
    c=v4.db()
    counts={}
    for table in ("shops","products","v4_orders","withdrawals"):
        try: counts[table]=c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception: counts[table]="N/A"
    try:
        pending_deposits=c.execute("SELECT COUNT(*) FROM payment_requests WHERE status='pending' AND payment_type='wallet_deposit'").fetchone()[0]
        pending_levels=c.execute("SELECT COUNT(*) FROM payment_requests WHERE status='pending' AND payment_type='level_activation'").fetchone()[0]
    except Exception:
        pending_deposits=pending_levels="N/A"
    try: pending_wd=c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone()[0]
    except Exception: pending_wd="N/A"
    c.close()
    kb=InlineKeyboardMarkup([
      [InlineKeyboardButton("👥 Owner Control Panel",callback_data="adm:owners")],
      [InlineKeyboardButton("🏪 All Shops",callback_data="adm:shops"),InlineKeyboardButton("🛍 Products",callback_data="adm:products")],
      [InlineKeyboardButton(f"💳 Pending Deposits ({pending_deposits})",callback_data="adm:payments")],
      [InlineKeyboardButton(f"🎚 Pending Level Payments ({pending_levels})",callback_data="adm:levelpayments")],
      [InlineKeyboardButton(f"💸 Pending Withdrawals ({pending_wd})",callback_data="adm:withdrawals")],
      [InlineKeyboardButton(f"📦 Orders ({counts['v4_orders']})",callback_data="adm:orders"),InlineKeyboardButton("🎚 Levels",callback_data="adm:levels")],
      [InlineKeyboardButton("⚙️ Settings & Commands",callback_data="adm:settings"),InlineKeyboardButton("🔄 Refresh",callback_data="adm:home")]
    ])
    await bot.send_message(chat_id,
      "🛡️ SHOP IN INDIA — ADMIN CONTROL PANEL\n━━━━━━━━━━━━━━━━━━\n"
      f"🏪 Shops: {counts['shops']}\n🛍 Products: {counts['products']}\n"
      f"📦 Orders: {counts['v4_orders']}\n💳 Pending deposits: {pending_deposits}\n"
      f"🎚 Pending level payments: {pending_levels}\n"
      f"💸 Withdrawals: {counts['withdrawals']} (Pending: {pending_wd})\n"
      f"💱 Rate: 1 USDT = INR {v4.usdt_inr_rate():,.2f}\n\n"
      "Select a control below. All screens are sent as new messages; previous messages remain.",
      reply_markup=kb)

async def admin_panel_callback(update,ctx):
    q=update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Admin only",show_alert=True); return
    await q.answer()
    action=q.data.split(":",1)[1]
    if action in ("home","back"):
        await send_admin_panel(ctx.bot,q.message.chat_id); return
    c=v4.db()
    try:
        if action=="owners":
            rows=c.execute("""SELECT s.id,s.shop_name,s.owner_id,s.status,s.level,
                (SELECT COUNT(*) FROM products p WHERE p.shop_id=s.id) products,
                (SELECT COUNT(*) FROM v4_orders o WHERE o.buyer_shop_id=s.id) buys,
                (SELECT COUNT(*) FROM v4_orders o WHERE o.seller_shop_id=s.id) sells
                FROM shops s ORDER BY s.id DESC LIMIT 40""").fetchall()
            await q.message.reply_text("👥 OWNER CONTROL PANEL\n\n"+("\n\n".join(
              f"🏪 {r['shop_name']} | Shop #{r['id']}\nOwner ID: {r['owner_id']}\nStatus: {r['status']} | Level: L{r['level']}\nProducts: {r['products']} | Buys: {r['buys']} | Sells: {r['sells']}\nDetails: /owner {r['owner_id']}"
              for r in rows) if rows else "No owner shops found."),
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="shops":
            rows=c.execute("SELECT id,shop_name,owner_id,status,level FROM shops ORDER BY id DESC LIMIT 40").fetchall()
            await q.message.reply_text("🏪 SHOPS\n\n"+("\n".join(f"#{r['id']} {r['shop_name']} | Owner {r['owner_id']} | {r['status']} | L{r['level']}" for r in rows) if rows else "No shops."),
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action in ("payments","levelpayments"):
            payment_type="wallet_deposit" if action=="payments" else "level_activation"
            section_title="PENDING DEPOSITS" if action=="payments" else "PENDING LEVEL PAYMENTS"
            rows=c.execute("SELECT id,owner_id,payment_type,amount,currency,network,status,screenshot_file_id,shop_id FROM payment_requests WHERE status='pending' AND payment_type=? ORDER BY id DESC LIMIT 25",(payment_type,)).fetchall()
            await q.message.reply_text(f"💳 {section_title}\nEach request is reviewed separately. Verify the transaction on the blockchain before approving.")
            if not rows:
                await q.message.reply_text(f"No pending { 'deposits' if action=='payments' else 'level payments' }.")
            for r in rows:
                caption=(f"💳 PAYMENT REQUEST #{r['id']}\nOwner: {r['owner_id']} | Shop: {r['shop_id']}\nType: {r['payment_type']}\nAmount: {r['amount']} {r['currency']}\nNetwork: {r['network'] or 'Not selected'}\nStatus: PENDING")
                back_label="⬅️ Deposits" if action=="payments" else "⬅️ Level Payments"
                kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ APPROVE",callback_data=f"pay:approve:{r['id']}"),InlineKeyboardButton("❌ REJECT",callback_data=f"pay:reject:{r['id']}")],[InlineKeyboardButton(back_label,callback_data=f"adm:{action}"),InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]])
                if r['screenshot_file_id']:
                    await ctx.bot.send_photo(q.message.chat_id,r['screenshot_file_id'],caption=caption,reply_markup=kb)
                else:
                    await q.message.reply_text(caption+"\n⚠️ No screenshot attached.",reply_markup=kb)
            await q.message.reply_text("⬅️ Back options:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="withdrawals":
            rows=c.execute("SELECT id,owner_id,amount,currency,network,wallet_address,status,amount_inr,exchange_rate FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 25").fetchall()
            await q.message.reply_text("💸 WITHDRAWAL REVIEW\nMark PAID only after you have actually sent the payout.")
            if not rows:
                await q.message.reply_text("No pending withdrawals.")
            for r in rows:
                rate=float(r['exchange_rate'] or 0)
                caption=(f"💸 WITHDRAWAL #{r['id']}\nOwner: {r['owner_id']}\nAmount: ₹{float(r['amount_inr'] or r['amount']):,.2f}\nNetwork: {r['network']}\nWallet: {r['wallet_address']}\nRate: ₹{rate:,.2f} per USDT\nStatus: PENDING")
                kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ MARK PAID",callback_data=f"wd:paid:{r['id']}"),InlineKeyboardButton("❌ REJECT",callback_data=f"wd:reject:{r['id']}")],[InlineKeyboardButton("⬅️ Withdrawals",callback_data="adm:withdrawals"),InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]])
                await q.message.reply_text(caption,reply_markup=kb)
            await q.message.reply_text("⬅️ Back options:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="products":
            rows=c.execute("""SELECT id,title,price,currency,stock,active,shop_id,
                                     COALESCE(level_required,1) AS level_required
                              FROM products ORDER BY level_required,id DESC LIMIT 50""").fetchall()
            level_counts=c.execute("""SELECT COALESCE(level_required,1) AS lv,COUNT(*) AS total
                                      FROM products GROUP BY COALESCE(level_required,1)
                                      ORDER BY lv""").fetchall()
            summary="\n".join(f"L{r['lv']}: {r['total']} products" for r in level_counts)
            details="\n\n".join(
                f"#{r['id']} {r['title']} | L{r['level_required']} | Shop #{r['shop_id']}\n"
                f"{r['currency']} {r['price']} | Stock {r['stock']} | Active {r['active']}\n"
                f"Change level: /setproductlevel {r['id']} LEVEL"
                for r in rows)
            await q.message.reply_text(
                "🛍 PRODUCTS BY LEVEL\n\n"+(summary or "No products assigned.")+
                "\n\nLATEST PRODUCTS\n"+(details or "No products."),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="orders":
            await q.message.reply_text("📦 ORDER MANAGEMENT\nUse /orders to view pending orders and Mark Delivered controls.\nUse /orderdone ORDER_ID or /orderreject ORDER_ID REASON when appropriate.",
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="levels":
            level_rows=c.execute("SELECT level,activation_usdt,product_target,profit_min,profit_max FROM level_settings ORDER BY level").fetchall()
            body="🎚 LEVEL SETTINGS\n\n"+("\n".join(
              f"L{r['level']}: {r['activation_usdt']} USDT | target {r['product_target'] or 'Unlimited'} | margin reference {r['profit_min']}–{r['profit_max']}%"
              for r in level_rows) if level_rows else "No level settings found.")
            body+="\n\nEdit: /setlevel LEVEL ACTIVATION_USDT PRODUCT_TARGET PROFIT_MIN PROFIT_MAX"
            await q.message.reply_text(body,
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="settings":
            await q.message.reply_text("⚙️ ADMIN COMMANDS\n/addproduct SHOP_ID PRICE STOCK LEVEL Title | Description\n/setprice PRODUCT_ID INR_PRICE\n/setstock PRODUCT_ID QTY\n/setproductlevel PRODUCT_ID LEVEL\n/setfx INR_PER_USDT\n/setfee PERCENT\n/setlevel LEVEL ACTIVATION_USDT PRODUCT_TARGET PROFIT_MIN PROFIT_MAX\n/orders\n/owner OWNER_ID\n/shopaudit SHOP_ID\n/verifywallet OWNER_ID NETWORK ADDRESS",
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
    except Exception:
        log.exception("Admin panel section failed")
        await q.message.reply_text("Could not load this section. Check database migration and logs.",
          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
    finally:
        c.close()

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
        await q.message.reply_text("Invalid order.")
        return
    c=v4.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        order=c.execute("SELECT * FROM v4_orders WHERE id=?",(oid,)).fetchone()
        if not order:
            c.rollback()
            await q.message.reply_text("Order not found.")
            return
        if order["status"] in ("delivered","completed","rejected"):
            c.rollback()
            await q.message.reply_text(f"Order #{oid} is already {order['status']}.")
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
        await q.message.reply_text("Could not update order. No delivery credit was recorded.")
        return
    finally:
        c.close()
    await q.message.reply_text(
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

async def support_command(update,ctx):
    await update.message.reply_text(
        "💬 SHOP IN INDIA SUPPORT\n\n"
        "Registration, login, shop approval, deposits, withdrawals ya account issue ke liye administrator se rabta karein."
    )

async def help_command(update,ctx):
    if update.effective_user.id == ADMIN_ID:
        body = (
            "🛠 ADMIN COMMANDS\n"
            "/admin — Admin control panel\n"
            "/orders — Pending orders\n"
            "/products — Product list\n"
            "/addproduct — Add product\n"
            "/setlevel — Configure levels\n"
            "/setfx — Update USDT/INR rate\n"
            "/setfee — Update platform fee\n"
            "/orderdone — Mark an order delivered\n"
            "/orderreject — Reject an order\n"
            "/verifywallet — Verify owner wallet\n"
            "/help — This help"
        )
    else:
        body = (
            "🔷 SHOP IN INDIA — HELP\n"
            "/start — Open registration or owner dashboard\n"
            "/dashboard — Open your dashboard\n"
            "/levels — View shop levels\n"
            "/buy PRODUCT_ID QUANTITY — Place a product order\n"
            "/verifywallet — Wallet verification help\n"
            "/support — Contact administrator\n"
            "/help — Show this help"
        )
    await update.message.reply_text(body)

async def dashboard(update,ctx):
    if update.effective_user.id==ADMIN_ID:
        await send_admin_panel(ctx.bot,update.effective_chat.id); return
    s=v4.shop_for(update.effective_user.id)
    if not s: await start(update,ctx)
    elif s["status"]!="approved": await update.message.reply_text("Awaiting Admin approval.")
    elif int(s["level"])<1: await levels(ctx.bot,update.effective_user.id)
    else: await update.message.reply_text(f"🏪 {s['shop_name']} | L{s['level']}",reply_markup=owner_kb())

def main():
    if not TOKEN or not ADMIN_ID: raise RuntimeError("BOT_TOKEN and ADMIN_ID are required.")
    v3.setup(); v3.ensure_catalog_schema()
    c=v4.db()
    try:
        columns={row["name"] for row in c.execute("PRAGMA table_info(payment_requests)").fetchall()}
        if "transaction_hash" not in columns:
            c.execute("ALTER TABLE payment_requests ADD COLUMN transaction_hash TEXT")
            c.commit()
    finally: c.close()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("dashboard",dashboard))
    for cmd,fn in [("help",help_command),("support",support_command),("admin",admin_panel),("verifywallet",v3.verify_wallet),
      ("buy",v4.buy),("addproduct",addproduct),("setproductlevel",v4.setproductlevel),("setlevel",v4.setlevel),("setfx",v4.setfx),("setfee",v4.setfee),
      ("products",v4.products),("setprice",lambda u,c:v4.edit_product(u,c,"setprice")),("setstock",lambda u,c:v4.edit_product(u,c,"setstock")),
      ("orders",admin_orders),("orderdone",v4.orderdone),("orderreject",v4.orderreject),("myproducts",v4.myproducts),("myorders",v4.myorders),("history",v4.history),("levels",v4.levels)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(approved_callback,pattern=r"^(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(level_callback,pattern=r"^lv:[1-5]$"))
    app.add_handler(CallbackQueryHandler(level_pay_callback,pattern=r"^lvpay:[1-5]$"))
    app.add_handler(CallbackQueryHandler(level_back_callback,pattern=r"^lvback$"))
    app.add_handler(CallbackQueryHandler(network_callback,pattern=r"^net:[0-9]+:(trc|bep)$"))
    app.add_handler(CallbackQueryHandler(menu_callback,pattern=r"^m:(market|deposit|deposits|depositdone|withdraw|wallet|products|orders|history|profile|dashboard|support)$"))
    app.add_handler(CallbackQueryHandler(admin_panel_callback,pattern=r"^adm:(home|owners|shops|payments|levelpayments|withdrawals|products|orders|levels|settings|back)$"))
    app.add_handler(CallbackQueryHandler(delivered_callback,pattern=r"^order:delivered:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v4.payment_review,pattern=r"^pay:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v4.withdrawal_review,pattern=r"^wd:(paid|reject):[0-9]+$"))
    app.add_handler(MessageHandler(filters.PHOTO,v4.payment_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    print("SHOP IN INDIA V5 Telegram UI started.")
    app.run_polling()

if __name__=="__main__": main()