"""V5 Telegram-chat UI: inline level selection, owner panel and deposits."""
import os, logging, asyncio, html, re
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, CopyTextButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import bot_v3 as v3
import bot_v4 as v4
import central_catalog as central
import audit_dashboard as audit
import runtime_schema

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

def upgrade_level_kb(current_level):
    """Offer only levels higher than the merchant's currently active level."""
    c=v4.db()
    try:
        rows=c.execute(
            "SELECT level,activation_usdt,product_target FROM level_settings WHERE level>? ORDER BY level",
            (int(current_level),)
        ).fetchall()
    finally:
        c.close()
    buttons=[]
    for row in rows:
        target="Unlimited" if not row["product_target"] else f"{int(row['product_target'])} products"
        buttons.append([InlineKeyboardButton(
            f"⬆️ Upgrade to Level {int(row['level'])} • {float(row['activation_usdt']):g} USDT",
            callback_data=f"lv:{int(row['level'])}")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")])
    return InlineKeyboardMarkup(buttons)


def owner_kb():
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("🛍 My Level Catalogue",callback_data="m:market")],
      [InlineKeyboardButton("🚀 Upgrade Shop Level",callback_data="m:upgrade")],
      [InlineKeyboardButton("🏬 Seller Marketplace",callback_data="m:sellermarket"),InlineKeyboardButton("💳 Deposit",callback_data="m:deposit")],
      [InlineKeyboardButton("💸 Withdraw",callback_data="m:withdraw"),InlineKeyboardButton("👛 Wallet",callback_data="m:wallet")],
      [InlineKeyboardButton("📦 My Products",callback_data="m:products"),InlineKeyboardButton("🧾 My Orders",callback_data="m:orders")],
      [InlineKeyboardButton("📊 Profit & History",callback_data="m:history"),InlineKeyboardButton("👤 My Account",callback_data="m:profile")],
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
        "<b>🔷 SHOP IN INDIA</b>\n━━━━━━━━━━━━━━━━━━\n"
        "<b>SELECT YOUR SHOP LEVEL</b>\n"
        "Choose a level to see its price and benefits. Payment is made in USDT.",
        reply_markup=level_kb(),parse_mode="HTML")

async def start(update,ctx):
    ctx.user_data.clear()
    s=v4.shop_for(update.effective_user.id)
    if not s:
        await update.message.reply_text(
            "<b>🔷 SHOP IN INDIA</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Welcome! Manage your shop, products and orders from one place.\n"
            "<b>Choose an option to continue:</b>",
            reply_markup=ReplyKeyboardMarkup(
                [["🏪 Register Shop"],["🔐 Login"],["💬 Support"]],
                resize_keyboard=True),parse_mode="HTML")
    elif s["status"]!="approved":
        await update.message.reply_text(
            "<b>🟡 APPLICATION PENDING</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Your shop is waiting for Admin review. You will receive a message when a decision is made.",
            parse_mode="HTML")
    elif int(s["level"])<1:
        await levels(ctx.bot,update.effective_user.id)
    else:
        await update.message.reply_text(
            f"<b>🔷 SHOP IN INDIA</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🏪 <b>{html.escape(str(s['shop_name']))}</b> • <b>LEVEL {s['level']}</b>\n\n"
            "<b>OWNER DASHBOARD</b>\nWelcome back! Choose a section below.",
            reply_markup=owner_kb(),parse_mode="HTML")

async def approved_callback(update,ctx):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer("Admin only",show_alert=True); return
    action,sid=q.data.split(":"); c=v4.db(); row=c.execute("SELECT owner_id,level FROM shops WHERE id=?",(int(sid),)).fetchone(); c.close()
    await v3.approval(update,ctx)
    if action=="approve" and row:
        if int(row["level"])<1: await levels(ctx.bot,row["owner_id"])
        else: await ctx.bot.send_message(row["owner_id"],"<b>✅ SHOP APPROVED</b>\nYour shop is approved and its level is active. Open /start to access your owner dashboard.",reply_markup=owner_kb(),parse_mode="HTML")

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
    back_label="⬅️ Back to upgrade options" if int(s["level"])>=1 else "⬅️ Back to all levels"
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💳 Continue to payment • {float(cfg['activation_usdt']):g} USDT",callback_data=f"lvpay:{lv}")],
        [InlineKeyboardButton(back_label,callback_data="lvback")]
    ])
    await q.message.reply_text(
        f"<b>🔷 SHOP LEVEL {lv}</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Activation price: <b>{float(cfg['activation_usdt']):g} USDT</b>\n"
        f"INR reference: ₹{float(cfg['activation_usdt'])*v4.usdt_inr_rate():,.2f}\n\n"
        f"<b>LEVEL BENEFITS</b>\n{html.escape(benefits)}\n\n"
        "<i>Profit figures are references, not guaranteed earnings. Level activates after Admin verifies payment.</i>",
        reply_markup=kb,parse_mode="HTML")

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

    ctx.user_data.update({"state":"await_deposit_done","payment_request_id":rid})
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 USDT TRC20",callback_data=f"net:{rid}:trc"),
         InlineKeyboardButton("🔵 USDT BEP20",callback_data=f"net:{rid}:bep")],
        [InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]
    ])
    await q.message.reply_text(
        f"<b>💳 LEVEL {lv} PAYMENT</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Request: <code>#{rid}</code>\n"
        f"Amount: <b>{amount:g} USDT</b>\n"
        f"INR reference: ₹{amount*rate:,.2f} (rate ₹{rate:,.2f}/USDT)\n\n"
        "<b>Choose your payment network:</b>",
        reply_markup=kb,parse_mode="HTML")

async def level_back_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    shop=v4.shop_for(q.from_user.id)
    if shop and shop["status"]=="approved" and int(shop["level"])>=1:
        current=int(shop["level"])
        c=v4.db()
        try:
            available=c.execute("SELECT COUNT(*) FROM level_settings WHERE level>?",(current,)).fetchone()[0]
        finally:
            c.close()
        if available:
            await q.message.reply_text(
                f"<b>🚀 UPGRADE YOUR SHOP</b>\nCurrent active level: <b>LEVEL {current}</b>\n"
                "Choose a higher level to continue.",
                reply_markup=upgrade_level_kb(current),parse_mode="HTML")
        else:
            await q.message.reply_text(
                f"<b>🏆 Highest level already active: LEVEL {current}</b>",
                reply_markup=back_dashboard_kb(),parse_mode="HTML")
        return
    await q.message.reply_text(
        "<b>🔷 SELECT YOUR SHOP LEVEL</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Choose a level to view its price and benefits.",
        reply_markup=level_kb(),parse_mode="HTML")

async def network_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    _,rid_s,net=q.data.split(":")
    rid=int(rid_s)
    network="USDT TRC20" if net=="trc" else "USDT BEP20"
    address=v4.PAY_TRON if net=="trc" else v4.PAY_BSC
    c=v4.db()
    try:
        r=c.execute("SELECT amount,payment_type FROM payment_requests WHERE id=? AND owner_id=? AND status='pending'",(rid,q.from_user.id)).fetchone()
        if not r:
            await q.message.reply_text("Payment request not found.",reply_markup=back_dashboard_kb())
            return
        c.execute("UPDATE payment_requests SET network=? WHERE id=? AND owner_id=? AND status='pending'",(network,rid,q.from_user.id))
        rate=v4.usdt_inr_rate(c)
        c.commit()
    finally:
        c.close()
    is_wallet_deposit=(r["payment_type"]=="wallet_deposit")
    ctx.user_data.update({"state":"await_deposit_done","payment_request_id":rid,
                          "screen_message_id":q.message.message_id,"screen_chat_id":q.message.chat_id})
    rows=[[InlineKeyboardButton("📋 Copy Deposit Address",copy_text=CopyTextButton(text=address))],
          [InlineKeyboardButton("➕ Add TXID (optional)",callback_data=f"txid:{rid}")],
          [InlineKeyboardButton("✅ I HAVE DEPOSITED",callback_data="m:depositdone")],
          [InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]]
    kb=InlineKeyboardMarkup(rows)
    address_title="DEPOSIT ADDRESS" if is_wallet_deposit else "LEVEL UPGRADE PAYMENT ADDRESS"
    await q.message.reply_text(
        f"💳 <b>{network} {address_title}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"<b><i>Address:</i></b>\n\n<code>{address}</code>\n\n"
        f"<b>Amount:</b> {float(r['amount']):g} USDT\n"
        f"<b>Rate:</b> 1 USDT = INR {rate:,.2f}\n\n"
        "<i>Send USDT only on the selected network. After payment, tap I HAVE DEPOSITED below. "
        "You may add the TXID first (optional), then upload your transaction screenshot. "
        "Admin will verify the payment manually before crediting your wallet or activating your level.</i>",
        reply_markup=kb,parse_mode="HTML")


async def txid_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    try:
        rid=int(q.data.split(":",1)[1])
    except (ValueError,IndexError):
        await q.message.reply_text("Invalid payment request.",reply_markup=back_dashboard_kb())
        return
    c=v4.db()
    try:
        row=c.execute("""SELECT id FROM payment_requests
            WHERE id=? AND owner_id=? AND payment_type IN ('wallet_deposit','level_activation')
              AND status='pending' AND network IN ('USDT TRC20','USDT BEP20')""",
            (rid,q.from_user.id)).fetchone()
    finally:
        c.close()
    if not row:
        await q.message.reply_text("Pending payment request with selected network was not found.",
                                   reply_markup=back_dashboard_kb())
        return
    ctx.user_data.update({"state":"deposit_tx_hash","payment_request_id":rid})
    await q.message.reply_text(
        "<b>🔎 ENTER TRANSACTION HASH / TXID</b>\n"
        "Send the full transaction hash from your wallet or blockchain explorer. "
        "This step is optional; you can also upload your screenshot directly.",
        reply_markup=back_dashboard_kb(),parse_mode="HTML")


async def menu_callback(update,ctx):
    q=update.callback_query
    await q.answer()
    action=q.data[2:]
    ctx.user_data["screen_message_id"]=q.message.message_id
    ctx.user_data["screen_chat_id"]=q.message.chat_id
    s=v4.shop_for(q.from_user.id)

    if action=="upgrade":
        if not s or s["status"]!="approved" or int(s["level"])<1:
            await q.message.reply_text(
                "Approved shop with an active level is required before upgrading.",
                reply_markup=back_dashboard_kb())
            return
        current_level=int(s["level"])
        c=v4.db()
        try:
            available=c.execute(
                "SELECT COUNT(*) FROM level_settings WHERE level>?",(current_level,)
            ).fetchone()[0]
        finally:
            c.close()
        if not available:
            await q.message.reply_text(
                f"<b>🏆 YOU ARE ALREADY AT THE HIGHEST LEVEL</b>\nCurrent level: <b>{current_level}</b>",
                reply_markup=back_dashboard_kb(),parse_mode="HTML")
            return
        await q.message.reply_text(
            f"<b>🚀 UPGRADE YOUR SHOP</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Current active level: <b>LEVEL {current_level}</b>\n"
            "Select the next level you want to activate. Admin verifies the payment before the upgrade takes effect.",
            reply_markup=upgrade_level_kb(current_level),parse_mode="HTML")
        return

    if action=="dashboard":
        if not s or s["status"]!="approved" or int(s["level"])<1:
            await q.message.reply_text("Approved shop aur active level zaroori hai.")
            return
        ctx.user_data.clear()
        ctx.user_data["screen_message_id"]=q.message.message_id
        ctx.user_data["screen_chat_id"]=q.message.chat_id
        await q.message.reply_text(
            f"<b>🔷 SHOP IN INDIA</b>\n━━━━━━━━━━━━━━━━━━\n🏪 <b>{html.escape(str(s['shop_name']))}</b> • <b>LEVEL {s['level']}</b>\n\n<b>OWNER DASHBOARD</b>\nChoose a section below.",
            reply_markup=owner_kb(),parse_mode="HTML")
        return

    if not s or s["status"]!="approved" or int(s["level"])<1:
        await q.message.reply_text("Approve your shop and activate a level first.",
                                  reply_markup=back_dashboard_kb())
        return

    if action=="deposit":
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Make Deposit",callback_data="m:depositstart")],
            [InlineKeyboardButton("🧾 Deposit History",callback_data="m:deposits")],
            [InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]
        ])
        await q.message.reply_text(
            "<b>💳 DEPOSIT CENTER</b>\n━━━━━━━━━━━━━━━━━━\n\nChoose an option below:",
            reply_markup=kb,parse_mode="HTML")
    elif action=="deposits":
        c=v4.db()
        try:
            rows=c.execute("""SELECT id,amount,currency,status,created_at,admin_note FROM payment_requests
                WHERE owner_id=? AND payment_type='wallet_deposit' ORDER BY id DESC LIMIT 30""",(q.from_user.id,)).fetchall()
        finally: c.close()
        body="<b>🧾 MY DEPOSITS</b>\n━━━━━━━━━━━━━━━━━━\n"
        if not rows: body+="No deposit records found."
        else:
            from datetime import datetime
            for r in rows:
                status=str(r["status"] or "pending").lower()
                label="🟢 ✅ Approved" if status=="approved" else ("🔴 ❌ Declined" if status=="rejected" else ("⚪ Cancelled" if status=="cancelled" else "🟡 Pending"))
                date_text=str(r["created_at"] or "Date unavailable")
                try: date_text=datetime.fromisoformat(date_text.replace("Z","+00:00")).strftime("%A, %d %B %Y")
                except Exception: pass
                body+=f"\n<b>{float(r['amount']):g} {html.escape(str(r['currency'] or 'USDT'))}</b> — {label} ({html.escape(date_text)})"
                if status=="rejected": body+=f"\n  Reason: {html.escape(str(r['admin_note'] or 'No reason provided'))}"
                body+="\n"
        await q.message.reply_text(body,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML")
    # Merchant confirms the transfer before the screenshot is accepted for Admin review.
    elif action=="depositdone":
        rid=ctx.user_data.get("payment_request_id")
        if not rid or ctx.user_data.get("state")!="await_deposit_done":
            await q.message.reply_text("Please complete the payment request and network selection first.",reply_markup=back_dashboard_kb()); return
        c=v4.db()
        try:
            row=c.execute(
                "SELECT id,payment_type FROM payment_requests WHERE id=? AND owner_id=? "
                "AND payment_type IN ('wallet_deposit','level_activation') "
                "AND status='pending' AND network IN ('USDT TRC20','USDT BEP20')",
                (rid,q.from_user.id)).fetchone()
        finally:
            c.close()
        if not row:
            await q.message.reply_text("This payment request is no longer pending or its network is not selected.",
                                       reply_markup=back_dashboard_kb()); return
        ctx.user_data["state"]="payment_proof"
        await q.message.reply_text(
            "<b>📸 UPLOAD YOUR PAYMENT SCREENSHOT</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Send a clear screenshot of your completed USDT transfer. "
            "Your proof will be sent to Admin for manual verification. "
            "Wallet deposits are credited and level upgrades are activated only after Admin approves the payment.",
            reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="depositstart" or action in ("depositfund","deposit_fund","deposit-fund"):
        ctx.user_data["state"]="deposit_amount"
        await q.message.reply_text(
            "<b>💳 DEPOSIT USDT</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Enter the amount you want to add to your merchant wallet.\n"
            "<i>Minimum amount must be greater than zero.</i>\n\n"
            "Next, select TRC20 or BEP20. After sending payment, tap I HAVE DEPOSITED and upload your screenshot.",
            reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="withdrawsetup":
        c=v4.db()
        try:
            wallet=c.execute("SELECT network,address,updated_at FROM owner_wallets WHERE owner_id=?",
                             (q.from_user.id,)).fetchone()
        finally:
            c.close()
        configured=bool(wallet and wallet["network"]=="USDT TRC20" and
                        re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}",str(wallet["address"] or "")))
        if configured:
            body=("<b>🔐 WITHDRAW SETUP</b>\n━━━━━━━━━━━━━━━━━━\n"
                  "🟢 <b>USDT TRC20 address saved</b>\n"
                  f"<code>{html.escape(str(wallet['address']))}</code>\n"
                  f"<i>Last updated: {html.escape(str(wallet['updated_at'] or ''))}</i>\n\n"
                  "Withdrawal requests will use this saved address. You can update it anytime.")
            rows=[[InlineKeyboardButton("📋 COPY MY ADDRESS",copy_text=CopyTextButton(text=str(wallet["address"])))],
                  [InlineKeyboardButton("✏️ UPDATE ADDRESS",callback_data="m:withdrawsetupedit")],
                  [InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]
        else:
            body=("<b>🔐 WITHDRAW SETUP</b>\n━━━━━━━━━━━━━━━━━━\n"
                  "🟡 <b>No payout address saved</b>\n\n"
                  "Save your USDT TRC20 address once before requesting a withdrawal. "
                  "Only TRON/TRC20 addresses are accepted.")
            rows=[[InlineKeyboardButton("➕ SET TRC20 ADDRESS",callback_data="m:withdrawsetupedit")],
                  [InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]
        await q.message.reply_text(body,reply_markup=InlineKeyboardMarkup(rows),parse_mode="HTML")
    elif action=="withdrawsetupedit":
        ctx.user_data["state"]="withdraw_setup_address"
        await q.message.reply_text(
            "<b>🔐 SET USDT TRC20 ADDRESS</b>\n━━━━━━━━━━━━━━━━━━\n"
            "Send your USDT TRC20 (TRON) wallet address.\n"
            "<i>Address must begin with T. BEP20/ERC20 addresses are not accepted.</i>",
            reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="withdraw":
        c=v4.db()
        try:
            wallet=c.execute("SELECT network,address FROM owner_wallets WHERE owner_id=?",
                             (q.from_user.id,)).fetchone()
        finally:
            c.close()
        address=str(wallet["address"] or "") if wallet and wallet["network"]=="USDT TRC20" else ""
        if not re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}",address):
            ctx.user_data["state"]="withdraw_setup_address"
            await q.message.reply_text(
                "<b>🔐 WITHDRAW SETUP REQUIRED</b>\n━━━━━━━━━━━━━━━━━━\n"
                "Pehle apna USDT TRC20 withdrawal address save karein.\n"
                "<i>Address T se start hona chahiye. Sirf TRC20 accepted hai.</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔐 OPEN WITHDRAW SETUP",callback_data="m:withdrawsetup")],
                    [InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]]),
                parse_mode="HTML")
            return
        available=available_withdrawal_usdt(q.from_user.id)
        ctx.user_data["state"]="withdraw_amount"
        await q.message.reply_text(
            "<b>💸 WITHDRAW USDT</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Available sale earnings: <b>{available:.6f} USDT</b>\n"
            f"Payout address: <code>{html.escape(address)}</code>\n"
            "Enter the amount you want to withdraw in <b>USDT</b>.\n"
            "<i>Only completed USDT resale sale proceeds are withdrawable. Payout is TRC20 only.</i>",
            reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="wallet":
        c=v4.db()
        try:
            summary=usdt_wallet_summary(c,q.from_user.id)
            bal=float(summary["ledger_balance"])
            available_wallet=max(0.0,round(bal-float(summary["pending"]),6))
            invested=c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='central_purchase_debit'",(q.from_user.id,)).fetchone()[0]
            profit=c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type IN ('resale_profit_credit','central_sale_profit')",(q.from_user.id,)).fetchone()[0]
            dep=c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='deposit_credit'",(q.from_user.id,)).fetchone()[0]
        finally: c.close()
        balance_line=(f"Available balance: <b>{available_wallet:.6f} USDT</b>" if bal>=-1e-9 else
                      f"⚠️ <b>Wallet shortfall: {abs(bal):.6f} USDT</b>")
        await q.message.reply_text(
            "<b>👛 USDT WALLET</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"{balance_line}\n"
            f"Total deposits: {float(dep):.6f} USDT\n"
            f"Catalogue investment: {float(invested):.6f} USDT\n"
            f"Total resale proceeds: {float(summary['earned']):.6f} USDT\n"
            f"Paid withdrawals: {float(summary['paid']):.6f} USDT\n"
            f"Pending withdrawals: {float(summary['pending']):.6f} USDT\n"
            f"Sale proceeds remaining: {float(summary['sale_remaining']):.6f} USDT\n"
            f"Available to withdraw: <b>{float(summary['available']):.6f} USDT</b>\n"
            f"💚 <b>Recorded profit: +{float(profit):.6f} USDT</b>\n\n"
            "<b>DEPOSIT NETWORKS</b>\n"
            f"TRC20: <code>{html.escape(str(v4.PAY_TRON))}</code>\n"
            f"BEP20: <code>{html.escape(str(v4.PAY_BSC))}</code>\n\n"
            "Send USDT only on the selected network and upload payment proof for verification.",
            reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="market":
        await central.owner_market(update,ctx,s)
    elif action=="level1":
        await central.owner_market(update,ctx,s)
    elif action=="sellermarket":
        central.resale_setup()
        c=v4.db()
        try:
            rows=c.execute("""SELECT p.id,p.title,p.description,p.price,p.currency,p.stock,s.shop_name
              FROM products p JOIN shops s ON s.id=p.shop_id
              WHERE p.active=1 AND p.stock>0 AND s.status='approved' AND p.shop_id<>?
              ORDER BY p.id DESC LIMIT 30""",(s["id"],)).fetchall()
            own_resale=c.execute("""SELECT l.id,l.remaining_quantity,l.ask_price_usdt,p.title
              FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id
              JOIN shops sh ON sh.id=l.shop_id
              WHERE l.status='active' AND l.remaining_quantity>0 AND l.owner_id=?
                AND sh.status='approved' AND sh.level>=p.level_required AND p.level_required<=?
              ORDER BY l.id DESC LIMIT 30""",(q.from_user.id,int(s["level"]))).fetchall()
            resale=c.execute("""SELECT l.id,l.remaining_quantity,l.ask_price_usdt,p.title,sh.shop_name
              FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id
              JOIN shops sh ON sh.id=l.shop_id
              WHERE l.status='active' AND l.remaining_quantity>0 AND l.owner_id<>?
                AND sh.status='approved' AND sh.level>=p.level_required AND p.level_required<=?
              ORDER BY l.id DESC LIMIT 30""",(q.from_user.id,int(s["level"]))).fetchall()
        finally: c.close()
        body="<b>🏬 SELLER MARKETPLACE</b>\n━━━━━━━━━━━━━━━━━━\n<b>MY ACTIVE USDT LISTINGS</b>\n"
        if own_resale:
            body+="\n\n".join(
                f"<b>#{r['id']} {html.escape(str(r['title']))}</b>\n"
                f"Your available stock: <b>{r['remaining_quantity']}</b>\n"
                f"Asking price: <b>{float(r['ask_price_usdt']):g} USDT / item</b>"
                for r in own_resale)
        else:
            body+="No active USDT listings yet. Approved catalogue purchases from your available levels are listed automatically."
        body+=f"\n\n<b>OTHER ELIGIBLE MERCHANT LISTINGS (LEVEL 1–{int(s['level'])})</b>\n"
        if resale:
            body+="\n\n".join(
                f"<b>#{r['id']} {html.escape(str(r['title']))}</b>\n"
                f"Seller: {html.escape(str(r['shop_name']))}\n"
                f"Price: <b>{float(r['ask_price_usdt']):g} USDT / item</b> | Stock: {r['remaining_quantity']}"
                for r in resale)
        else:
            body+="No other merchant listings available in your level range right now."
        await q.message.reply_text(body[:4000],reply_markup=InlineKeyboardMarkup(
            [([InlineKeyboardButton(f"Buy #{r['id']} ×1",callback_data=f"rs:buy:{r['id']}:1"),
               InlineKeyboardButton("Buy ×5",callback_data=f"rs:buy:{r['id']}:5")]
              if int(r["remaining_quantity"])>=5 else
              [InlineKeyboardButton(f"Buy #{r['id']} ×1",callback_data=f"rs:buy:{r['id']}:1")])
             for r in resale]+
            [[InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML")
        legacy_body="<b>LEGACY INR MARKETPLACE</b>\n<i>Separate from USDT resale listings</i>"
        legacy_body+="\n\n".join(
            f"#{r['id']} {html.escape(str(r['title']))}\nShop: {html.escape(str(r['shop_name']))}\n"
            f"{html.escape(str(r['currency']))} {float(r['price']):g} | Stock: {r['stock']}\n"
            f"<code>/buy {r['id']} QUANTITY</code>"
            for r in rows) if rows else "\nNo INR products are published. Your USDT listings appear above."
        await q.message.reply_text(legacy_body[:4000],reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="products":
        central.resale_setup()
        c=v4.db()
        try:
            rows=c.execute("""SELECT i.catalog_id,i.quantity,i.remaining_quantity,i.unit_cost_usdt,i.status,
                p.title,p.level_required,
                (SELECT l.ask_price_usdt FROM resale_listings l
                 WHERE l.inventory_id=i.id AND l.status='active' AND l.remaining_quantity>0
                 ORDER BY l.id DESC LIMIT 1) current_ask
                FROM central_inventory i JOIN central_catalog p ON p.id=i.catalog_id
                WHERE i.owner_id=? ORDER BY i.id DESC LIMIT 30""",(q.from_user.id,)).fetchall()
            level2_margin=c.execute(
                "SELECT profit_min,profit_max FROM level_settings WHERE level=2"
            ).fetchone()
            legacy=c.execute("SELECT id,title,price,currency,stock,active FROM products WHERE shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],)).fetchall()
        finally: c.close()
        body="<b>📦 MY PRODUCTS & INVENTORY</b>\n━━━━━━━━━━━━━━━━━━\n"
        if rows:
            total_remaining=sum(int(r["remaining_quantity"]) for r in rows)
            body+=f"Available units: <b>{total_remaining}</b> across {len(rows)} inventory record(s).\n"
            for r in rows:
                remaining=int(r["remaining_quantity"])
                cost=float(r["unit_cost_usdt"])
                state="🟢 IN STOCK" if remaining>0 else "🔴 SOLD OUT"
                item_level=int(r["level_required"])
                if remaining>0 and item_level==1:
                    expected=round(remaining*0.8,6)
                    margin=(f"💚 Expected margin: <b>+0.8 USDT / item</b> | Potential: <b>+{expected:g} USDT</b>\n")
                elif remaining>0 and item_level==2:
                    ask=float(r["current_ask"] or 0)
                    if ask>0 and cost>0:
                        actual_profit=round(ask-cost,6)
                        actual_pct=actual_profit/cost*100
                        potential=round(actual_profit*remaining,6)
                        margin=(f"💚 Resale price: <b>{ask:g} USDT / item</b> | Margin: <b>{actual_pct:.2f}%</b>\n"
                                f"Potential profit if all remaining units sell: <b>+{potential:g} USDT</b>\n")
                    else:
                        low=float(level2_margin["profit_min"]) if level2_margin else 2.0
                        high=float(level2_margin["profit_max"]) if level2_margin else 6.5
                        margin=(f"💚 Resale margin reference: <b>{low:g}%–{high:g}%</b>\n")
                else:
                    margin=""
                body+=(
                    f"\n<b>{html.escape(str(r['title']))}</b> • L{item_level}\n"
                    f"Available quantity: <b>{remaining}</b>\n"
                    f"Purchase cost: {cost:g} USDT / item\n"
                    f"{margin}Status: {state}\n"
                )
        else:
            body+="No central catalogue purchases recorded yet."
        if legacy:
            body+="\n<b>LEGACY INR PRODUCTS</b>\n"+"\n".join(
                f"#{r['id']} {html.escape(str(r['title']))} — {html.escape(str(r['currency']))} {r['price']} | Stock {r['stock']}"
                for r in legacy)
        await q.message.reply_text(body[:4000],reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍 MY LEVEL PRODUCTS / CATALOGUE",callback_data="m:level1")],
            [InlineKeyboardButton("⬅️ Back",callback_data="m:dashboard"),
             InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")]]),parse_mode="HTML")
        await q.message.reply_text(
            "<b>USDT RESALE</b>\nChoose an action:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏷️ List My Stock",callback_data="rs:listmenu")],
                [InlineKeyboardButton("🛍 USDT Resale Market",callback_data="rs:market")],
                [InlineKeyboardButton("⬅️ Back to Products",callback_data="m:products"),
                 InlineKeyboardButton("🏠 Dashboard",callback_data="m:dashboard")]]),
            parse_mode="HTML")
    elif action=="orders":
        c=v4.db()
        rows=c.execute("SELECT id,product_id,quantity,total_amount,status FROM v4_orders WHERE buyer_shop_id=? OR seller_shop_id=? ORDER BY id DESC LIMIT 20",(s["id"],s["id"])).fetchall()
        central_rows=c.execute("""SELECT o.id,o.quantity,o.total_usdt,o.status,p.title FROM central_orders o
          JOIN central_catalog p ON p.id=o.catalog_id WHERE o.buyer_shop_id=? ORDER BY o.id DESC LIMIT 20""",(s["id"],)).fetchall()
        c.close()
        seller_body="\n────────────────────\n".join(
            f"<b>#{r['id']}  Product #{r['product_id']}</b>\n"
            f"     Qty: <b>{r['quantity']}</b>    Total: <b>INR {r['total_amount']:.2f}</b>\n"
            f"     Status: {'🟢' if r['status'] in ('delivered','completed') else '🔴' if r['status']=='rejected' else '🟡'} "
            f"<b>{html.escape(str(r['status']).upper())}</b>"
            for r in rows)
        central_body="\n────────────────────\n".join(
            f"<b>#{r['id']}  {html.escape(str(r['title']))}</b>\n"
            f"     Qty: <b>{r['quantity']}</b>    Total: <b>{r['total_usdt']:g} USDT</b>\n"
            f"     Status: {'🟢' if r['status'] in ('paid','completed','delivered') else '🔴' if r['status']=='rejected' else '🟡'} "
            f"<b>{html.escape(str(r['status']).upper())}</b>"
            for r in central_rows)
        body=("<b>🧾 MY ORDERS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
              "<b>INR SELLER MARKETPLACE</b>\n\n"+(seller_body or "No INR orders.")+
              "\n\n━━━━━━━━━━━━━━━━━━\n\n<b>USDT CATALOGUE / RESALE</b>\n\n"+
              (central_body or "No USDT orders."))
        await q.message.reply_text(body[:4000],reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="history":
        c=v4.db()
        rows=c.execute("SELECT entry_type,amount,currency,note,created_at FROM ledger WHERE owner_id=? ORDER BY id DESC LIMIT 20",(q.from_user.id,)).fetchall()
        c.close()
        body="<b>📊 PROFIT & TRANSACTION HISTORY</b>\n━━━━━━━━━━━━━━━━━━\n"
        if rows:
            items=[]
            for r in rows:
                kind=str(r["entry_type"])
                amount=float(r["amount"])
                if kind in ("resale_profit_credit","central_sale_profit","sale_credit"):
                    marker="💚"; label="PROFIT / SALE CREDIT"; signed=f"+{amount:g}"
                elif "debit" in kind or "withdrawal" in kind:
                    marker="🔴"; label="PAYMENT / DEBIT"; signed=f"{amount:g}"
                elif "credit" in kind or "deposit" in kind:
                    marker="🟢"; label="CREDIT"; signed=f"+{amount:g}"
                else:
                    marker="🔹"; label=kind.replace("_"," ").upper(); signed=f"{amount:g}"
                items.append(
                    f"{marker} <b>{label}</b>\n"
                    f"{html.escape(kind.replace('_',' ').title())}: <b>{html.escape(str(r['currency']))} {signed}</b>\n"
                    f"<i>{html.escape(str(r['note'] or ''))}</i>\n"
                    f"<code>{html.escape(str(r['created_at']))}</code>"
                )
            body+="\n\n".join(items)
        else:
            body+="No wallet or order activity has been recorded yet."
        await q.message.reply_text(body[:4000],reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="profile":
        body=(
            "<b>👤 MY ACCOUNT</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Shop: <b>{html.escape(str(s['shop_name']))}</b>\n"
            f"Shop ID: <code>{s['id']}</code> • Level: <b>L{s['level']}</b>\n"
            "Withdrawal network: <b>USDT TRC20 only</b>\n"
            "Enter your TRC20 address with each withdrawal request.\n\n"
            "<b>DEPOSIT NETWORKS</b>\n"
            f"TRC20: <code>{html.escape(str(v4.PAY_TRON))}</code>\n"
            f"BEP20: <code>{html.escape(str(v4.PAY_BSC))}</code>\n\n"
            "<i>Withdrawal address is requested separately for every payout.</i>"
        )
        await q.message.reply_text(body,reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="support":
        await q.message.reply_text(
            "<b>💬 SUPPORT</b>\n━━━━━━━━━━━━━━━━━━\n"
            "For help with registration, payments, shop approval or account issues, contact the Shop in India administrator.",
            reply_markup=back_dashboard_kb(),parse_mode="HTML")


def usdt_wallet_summary(c, owner_id):
    """One source of truth for resale proceeds, withdrawals and spendable wallet balance."""
    earned=float(c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='resale_sale_credit'",
        (owner_id,)).fetchone()[0])
    paid=float(c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='withdrawal_debit'",
        (owner_id,)).fetchone()[0])
    pending=float(c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE owner_id=? AND currency='USDT' AND status='pending'",
        (owner_id,)).fetchone()[0])
    ledger_balance=float(central.balance(c,owner_id))
    sale_remaining=max(0.0,round(earned-paid-pending,6))
    spendable_after_reservations=round(ledger_balance-pending,6)
    available=max(0.0,round(min(sale_remaining,spendable_after_reservations),6))
    return {
        "earned":round(earned,6),
        "paid":round(paid,6),
        "pending":round(pending,6),
        "ledger_balance":round(ledger_balance,6),
        "sale_remaining":sale_remaining,
        "available":available,
        "shortfall":max(0.0,round(-ledger_balance,6)),
    }


def available_withdrawal_usdt(owner_id):
    """Only unwithdrawn resale proceeds backed by the merchant's net USDT wallet."""
    c=v4.db()
    try:
        return usdt_wallet_summary(c,owner_id)["available"]
    finally:
        c.close()


async def withdrawal_review_callback(update,ctx):
    """Apply Admin review, then show the same post-payout figures to Admin and merchant."""
    q=update.callback_query
    try:
        _,action,wid_text=q.data.split(":")
        wid=int(wid_text)
    except (ValueError,IndexError):
        await v4.withdrawal_review(update,ctx)
        return
    c=v4.db()
    try:
        before=c.execute("SELECT owner_id,status FROM withdrawals WHERE id=?",(wid,)).fetchone()
        owner_id=int(before["owner_id"]) if before else None
        was_pending=bool(before and before["status"]=="pending")
    finally:
        c.close()
    await v4.withdrawal_review(update,ctx)
    if action!="paid" or not was_pending or owner_id is None:
        return
    c=v4.db()
    try:
        summary=usdt_wallet_summary(c,owner_id)
    finally:
        c.close()
    if summary["ledger_balance"] < -1e-9:
        wallet_line=(f"⚠️ Wallet shortfall: <b>{summary['shortfall']:.6f} USDT</b>")
    else:
        wallet_line=(f"Available wallet balance: <b>{max(0.0,summary['ledger_balance']-summary['pending']):.6f} USDT</b>")
    details=(
        "<b>📊 POST-WITHDRAWAL WALLET SUMMARY</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Total resale proceeds: <b>{summary['earned']:.6f} USDT</b>\n"
        f"Paid withdrawals: <b>{summary['paid']:.6f} USDT</b>\n"
        f"Pending withdrawals: <b>{summary['pending']:.6f} USDT</b>\n"
        f"Sale proceeds remaining: <b>{summary['sale_remaining']:.6f} USDT</b>\n"
        f"{wallet_line}\n"
        f"Available for another withdrawal: <b>{summary['available']:.6f} USDT</b>"
    )
    await q.message.reply_text(details,parse_mode="HTML")
    try:
        await ctx.bot.send_message(owner_id,details,parse_mode="HTML")
    except Exception:
        log.exception("Post-withdrawal wallet summary notification failed")


async def text_handler(update,ctx):
    if await central.admin_resale_text(update,ctx): return
    edit=ctx.user_data.get("central_admin_edit")
    if edit:
        if update.effective_user.id!=ADMIN_ID:
            ctx.user_data.pop("central_admin_edit",None); return
        raw=(update.message.text or "").strip()
        if raw.lower()=="cancel":
            ctx.user_data.pop("central_admin_edit",None)
            await update.message.reply_text("Central catalogue edit cancelled.",reply_markup=back_dashboard_kb()); return
        try:
            if edit["field"]=="price":
                value=round(float(raw),6)
                if value<0: raise ValueError()
                await central.set_price(update, type("Args",(),{"args":[str(edit["product_id"]),str(value)]})())
            else:
                value=int(raw)
                if value<0: raise ValueError()
                await central.set_stock(update, type("Args",(),{"args":[str(edit["product_id"]),str(value)]})())
        except (ValueError,TypeError):
            await update.message.reply_text("Invalid value. Enter a non-negative number, or type CANCEL.")
            return
        ctx.user_data.pop("central_admin_edit",None)
        await central.admin_catalog(update,ctx)
        return
    state=ctx.user_data.get("state")
    if state=="withdraw_setup_address":
        address=(update.message.text or "").strip()
        if not re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}",address):
            await edit_screen(ctx,update,
                "❌ Invalid TRC20 address. Enter a valid USDT TRC20 address beginning with T.",
                reply_markup=back_dashboard_kb())
            return
        shop=v4.shop_for(update.effective_user.id)
        if not shop or shop["status"]!="approved":
            ctx.user_data.pop("state",None)
            await edit_screen(ctx,update,"Approved shop required.",reply_markup=back_dashboard_kb())
            return
        c=v4.db()
        try:
            c.execute("""INSERT INTO owner_wallets(owner_id,network,address,updated_at,verified)
                VALUES(?,'USDT TRC20',?,?,0)
                ON CONFLICT(owner_id) DO UPDATE SET
                  network='USDT TRC20',address=excluded.address,
                  updated_at=excluded.updated_at,verified=0""",
                (update.effective_user.id,address,v4.now()))
            c.execute("""INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at)
                VALUES(?,?,?,?,?)""",
                (ADMIN_ID,update.effective_user.id,"withdrawal_wallet_saved",
                 f"Merchant saved/updated USDT TRC20 withdrawal address for shop #{shop['id']}",v4.now()))
            c.commit()
        except Exception:
            c.rollback()
            log.exception("Saving merchant withdrawal address failed")
            await edit_screen(ctx,update,"Address could not be saved. Please try again.",
                              reply_markup=back_dashboard_kb())
            return
        finally:
            c.close()
        available=available_withdrawal_usdt(update.effective_user.id)
        ctx.user_data.pop("state",None)
        await edit_screen(ctx,update,
            "<b>✅ WITHDRAW ADDRESS SAVED</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Address: <code>{html.escape(address)}</code>\n\n"
            f"Available sale earnings: <b>{available:.6f} USDT</b>\n"
            "Ab withdrawal amount USDT mein send karein.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 WITHDRAW NOW",callback_data="m:withdraw")],
                [InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]))
        try:
            await ctx.bot.send_message(ADMIN_ID,
                f"<b>🔐 MERCHANT WITHDRAWAL ADDRESS SAVED</b>\n"
                f"Merchant: <code>{update.effective_user.id}</code>\n"
                f"Shop: <b>{html.escape(str(shop['shop_name']))}</b> • #{shop['id']}\n"
                f"Network: <b>USDT TRC20</b>\nAddress: <code>{html.escape(address)}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 COPY ADDRESS",copy_text=CopyTextButton(text=address))],
                    [InlineKeyboardButton("🔐 All Merchant Wallets",callback_data="adm:withdrawwallets")]]),
                parse_mode="HTML")
        except Exception:
            log.exception("Admin merchant withdrawal wallet notification failed")
        return
    if state=="deposit_tx_hash":
        tx_hash=(update.message.text or "").strip()
        if len(tx_hash)<12 or len(tx_hash)>160 or not re.fullmatch(r"[A-Za-z0-9xX:_-]+",tx_hash):
            await update.message.reply_text("❌ Invalid transaction hash. Send the full TXID/hash from your wallet or blockchain explorer."); return
        rid=ctx.user_data.get("payment_request_id")
        c=v4.db()
        try:
            cur=c.execute("""UPDATE payment_requests SET transaction_hash=? WHERE id=? AND owner_id=?
                AND payment_type IN ('wallet_deposit','level_activation') AND status='pending'""",
                (tx_hash,rid,update.effective_user.id))
            c.commit()
        finally: c.close()
        if cur.rowcount!=1:
            await update.message.reply_text("Payment request not found or already reviewed.",
                                            reply_markup=back_dashboard_kb())
            ctx.user_data.pop("state",None)
            return
        ctx.user_data["deposit_tx_hash"]=tx_hash
        ctx.user_data["state"]="payment_proof"
        await update.message.reply_text(
            "📸 <b>Now send your payment screenshot/photo.</b>\n"
            "Transaction hash saved. Please send a clear screenshot showing the transfer details.",
            parse_mode="HTML",reply_markup=back_dashboard_kb())
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
        ctx.user_data.update({"state":"await_deposit_done","payment_request_id":rid})
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
            amount=round(float(update.message.text),6)
            if amount<=0: raise ValueError()
        except (TypeError,ValueError):
            await edit_screen(ctx,update,"Please enter a valid positive USDT amount.",reply_markup=back_dashboard_kb())
            return
        shop=v4.shop_for(update.effective_user.id)
        if not shop or shop["status"]!="approved":
            ctx.user_data.pop("state",None)
            await edit_screen(ctx,update,"Approved shop required.",reply_markup=back_dashboard_kb())
            return
        c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            wallet=c.execute("SELECT network,address FROM owner_wallets WHERE owner_id=?",
                             (update.effective_user.id,)).fetchone()
            address=str(wallet["address"] or "") if wallet and wallet["network"]=="USDT TRC20" else ""
            if not re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}",address):
                c.rollback()
                ctx.user_data.pop("state",None)
                await edit_screen(ctx,update,
                    "Withdrawal address is not set. Open Withdraw Setup and save your USDT TRC20 address first.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔐 OPEN WITHDRAW SETUP",callback_data="m:withdrawsetup")],
                        [InlineKeyboardButton("⬅️ Dashboard",callback_data="m:dashboard")]]))
                return
            available=usdt_wallet_summary(c,update.effective_user.id)["available"]
            if amount>available+1e-9:
                c.rollback()
                await edit_screen(ctx,update,
                    f"Your available sale earnings changed. Current available amount: {max(0,available):.6f} USDT.",
                    reply_markup=back_dashboard_kb())
                ctx.user_data.pop("state",None)
                return
            cur=c.execute("""INSERT INTO withdrawals
                (owner_id,shop_id,amount,currency,network,wallet_address,status,created_at)
                VALUES(?,?,?,'USDT','USDT TRC20',?,'pending',?)""",
                (update.effective_user.id,shop["id"],amount,address,v4.now()))
            wid=cur.lastrowid
            c.execute("""INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at)
                VALUES(?,?,?,?,?)""",
                (ADMIN_ID,update.effective_user.id,"withdrawal_requested",
                 f"Withdrawal #{wid}: {amount:g} USDT to saved TRC20 address",v4.now()))
            c.commit()
        except Exception:
            c.rollback()
            log.exception("USDT withdrawal request failed")
            await edit_screen(ctx,update,"Withdrawal request could not be saved. No balance was deducted.",
                              reply_markup=back_dashboard_kb())
            return
        finally:
            c.close()
        await edit_screen(ctx,update,
            f"<b>🟡 WITHDRAWAL REQUEST SUBMITTED</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Request: <code>#{wid}</code>\nAmount: <b>{amount:.6f} USDT</b>\n"
            "Network: <b>USDT TRC20</b>\n"
            f"Payout address: <code>{html.escape(address)}</code>\n"
            "Status: <b>PENDING ADMIN REVIEW</b>",
            reply_markup=back_dashboard_kb())
        wd_kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 COPY TRC20 ADDRESS",copy_text=CopyTextButton(text=address))],
            [InlineKeyboardButton("✅ MARK PAID",callback_data=f"wd:paid:{wid}"),
             InlineKeyboardButton("❌ REJECT",callback_data=f"wd:reject:{wid}")],
            [InlineKeyboardButton("💸 All Pending Withdrawals",callback_data="adm:withdrawals"),
             InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
        ])
        await ctx.bot.send_message(ADMIN_ID,
            f"<b>💸 USDT WITHDRAWAL REQUEST #{wid}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Owner: <code>{update.effective_user.id}</code>\n"
            f"Shop: <b>{html.escape(str(shop['shop_name']))}</b> • #{shop['id']}\n"
            f"Amount: <b>{amount:.6f} USDT</b>\nNetwork: <b>USDT TRC20</b>\n"
            f"Address: <code>{html.escape(address)}</code>\n\n"
            "<i>Manually send the exact USDT amount on TRON, then tap MARK PAID. Reject only when declining the request.</i>",
            reply_markup=wd_kb,parse_mode="HTML")
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
      [InlineKeyboardButton("🛒 Central Catalogue (USDT)",callback_data="adm:central")],
      [InlineKeyboardButton("🏷️ Admin Resale Market",callback_data="adm:resale")],
      [InlineKeyboardButton("🧾 FULL AUDIT LOG",callback_data="adm:audit")],
      [InlineKeyboardButton("🏪 All Shops",callback_data="adm:shops"),InlineKeyboardButton("🛍 Products",callback_data="adm:products")],
      [InlineKeyboardButton(f"💳 Pending Deposits ({pending_deposits})",callback_data="adm:payments")],
      [InlineKeyboardButton(f"🎚 Pending Level Payments ({pending_levels})",callback_data="adm:levelpayments")],
      [InlineKeyboardButton(f"💸 Pending Withdrawals ({pending_wd})",callback_data="adm:withdrawals")],
      [InlineKeyboardButton("🔐 Merchant Withdrawal Wallets",callback_data="adm:withdrawwallets")],
      [InlineKeyboardButton(f"📦 Orders ({counts['v4_orders']})",callback_data="adm:orders"),InlineKeyboardButton("🎚 Levels",callback_data="adm:levels")],
      [InlineKeyboardButton("⚙️ Settings & Commands",callback_data="adm:settings"),InlineKeyboardButton("🔄 Refresh",callback_data="adm:home")]
    ])
    await bot.send_message(chat_id,
      "<b>🛡️ SHOP IN INDIA — ADMIN CONTROL PANEL</b>\n━━━━━━━━━━━━━━━━━━\n"
      f"🏪 Shops: <b>{counts['shops']}</b>\n🛍 Products: <b>{counts['products']}</b>\n"
      f"📦 Orders: <b>{counts['v4_orders']}</b>\n💳 Pending deposits: <b>{pending_deposits}</b>\n"
      f"🎚 Pending level payments: <b>{pending_levels}</b>\n"
      f"💸 Withdrawals: <b>{counts['withdrawals']}</b> (Pending: <b>{pending_wd}</b>)\n"
      f"💱 Rate: 1 USDT = INR {v4.usdt_inr_rate():,.2f}\n\n"
      "<b>CONTROL CENTER</b>\nChoose a section below.",
      reply_markup=kb,parse_mode="HTML")

async def admin_panel_callback(update,ctx):
    q=update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Admin only",show_alert=True); return
    await q.answer()
    action=q.data.split(":",1)[1]
    if action=="audit":
        await audit.show(update,"all",0); return
    if action in ("home","back"):
        await send_admin_panel(ctx.bot,q.message.chat_id); return
    c=v4.db()
    try:
        if action=="central":
            await central.admin_catalog(update,ctx)
        elif action=="resale":
            await central.admin_resale_market(update,ctx)
        elif action=="owners":
            rows=c.execute("""SELECT s.id,s.shop_name,s.owner_id,s.status,s.level,
                (SELECT COUNT(*) FROM products p WHERE p.shop_id=s.id) products,
                (SELECT COUNT(*) FROM v4_orders o WHERE o.buyer_shop_id=s.id) buys,
                (SELECT COUNT(*) FROM v4_orders o WHERE o.seller_shop_id=s.id) sells
                FROM shops s ORDER BY s.id DESC LIMIT 40""").fetchall()
            owner_body="<b>👥 OWNER CONTROL PANEL</b>\n━━━━━━━━━━━━━━━━━━\n"
            owner_body+="\n\n".join(
              f"<b>🏪 {html.escape(str(r['shop_name']))}</b> • Shop #{r['id']}\n"
              f"Owner: <code>{r['owner_id']}</code>\n"
              f"Status: {'✅' if r['status']=='approved' else '❌' if r['status']=='rejected' else '🟡'} <b>{html.escape(str(r['status']).upper())}</b> • Level {r['level']}\n"
              f"Products: {r['products']} • Buys: {r['buys']} • Sells: {r['sells']}\n"
              f"<code>/owner {r['owner_id']}</code>"
              for r in rows) if rows else "No owner shops found."
            await q.message.reply_text(owner_body[:4000],
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]),parse_mode="HTML")
        elif action=="shops":
            rows=c.execute("SELECT id,shop_name,owner_id,status,level FROM shops ORDER BY id DESC LIMIT 40").fetchall()
            shops_body="<b>🏪 ALL SHOPS</b>\n━━━━━━━━━━━━━━━━━━\n"
            shops_body+="\n".join(
              f"#{r['id']} <b>{html.escape(str(r['shop_name']))}</b> • Owner <code>{r['owner_id']}</code>\n"
              f"{'✅' if r['status']=='approved' else '❌' if r['status']=='rejected' else '🟡'} {html.escape(str(r['status']).upper())} • Level {r['level']}"
              for r in rows) if rows else "No shops registered."
            await q.message.reply_text(shops_body[:4000],
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]),parse_mode="HTML")
        elif action in ("payments","levelpayments"):
            payment_type="wallet_deposit" if action=="payments" else "level_activation"
            section_title="PENDING DEPOSITS" if action=="payments" else "PENDING LEVEL PAYMENTS"
            rows=c.execute("SELECT id,owner_id,payment_type,amount,currency,network,status,screenshot_file_id,shop_id,transaction_hash FROM payment_requests WHERE status='pending' AND payment_type=? ORDER BY id DESC LIMIT 25",(payment_type,)).fetchall()
            await q.message.reply_text(f"<b>💳 {section_title}</b>\n━━━━━━━━━━━━━━━━━━\nReview every request separately. Verify the transaction before approving.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]),parse_mode="HTML")
            if not rows:
                await q.message.reply_text(f"No pending { 'deposits' if action=='payments' else 'level payments' }.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
            for r in rows:
                caption=(f"<b>💳 PAYMENT REQUEST #{r['id']}</b>\n━━━━━━━━━━━━━━━━━━\n"
                         f"Owner: <code>{r['owner_id']}</code> • Shop: <code>{r['shop_id']}</code>\n"
                         f"Type: {html.escape(str(r['payment_type']))}\n"
                         f"Amount: <b>{float(r['amount']):g} {html.escape(str(r['currency']))}</b>\n"
                         f"Network: {html.escape(str(r['network'] or 'Not selected'))}\nTransaction Hash: <code>{html.escape(str(r['transaction_hash'] or 'Not provided'))}</code>\n🟡 <b>STATUS: PENDING</b>")
                back_label="⬅️ Deposits" if action=="payments" else "⬅️ Level Payments"
                kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ APPROVE",callback_data=f"pay:approve:{r['id']}"),InlineKeyboardButton("❌ REJECT",callback_data=f"pay:reject:{r['id']}")],[InlineKeyboardButton(back_label,callback_data=f"adm:{action}"),InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]])
                if r['screenshot_file_id']:
                    await ctx.bot.send_photo(q.message.chat_id,r['screenshot_file_id'],caption=caption,reply_markup=kb,parse_mode="HTML")
                else:
                    await q.message.reply_text(caption+"\n⚠️ No screenshot attached.",reply_markup=kb,parse_mode="HTML")
            await q.message.reply_text("⬅️ Back options:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="withdrawwallets":
            rows=c.execute("""SELECT w.owner_id,w.network,w.address,w.updated_at,
                s.id AS shop_id,s.shop_name
                FROM owner_wallets w
                LEFT JOIN shops s ON s.id=(SELECT id FROM shops WHERE owner_id=w.owner_id ORDER BY id DESC LIMIT 1)
                WHERE w.network='USDT TRC20'
                ORDER BY w.updated_at DESC LIMIT 50""").fetchall()
            await q.message.reply_text(
                "<b>🔐 MERCHANT WITHDRAWAL WALLETS</b>\n━━━━━━━━━━━━━━━━━━\n"
                "Saved USDT TRC20 payout addresses. Copy carefully before sending funds.",
                parse_mode="HTML")
            if not rows:
                await q.message.reply_text("No merchants have saved a TRC20 payout address yet.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
            for r in rows:
                address=str(r["address"] or "")
                caption=(f"<b>🏪 {html.escape(str(r['shop_name'] or 'Shop not found'))}</b>\n"
                         f"Shop: <code>{r['shop_id'] if r['shop_id'] is not None else 'N/A'}</code>\n"
                         f"Merchant ID: <code>{r['owner_id']}</code>\n"
                         f"Network: <b>USDT TRC20</b>\n"
                         f"Withdrawal address:\n<code>{html.escape(address)}</code>\n"
                         f"<i>Saved: {html.escape(str(r['updated_at'] or ''))}</i>")
                kb=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 COPY TRC20 ADDRESS",copy_text=CopyTextButton(text=address))],
                    [InlineKeyboardButton("⬅️ Merchant Wallets",callback_data="adm:withdrawwallets"),
                     InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
                ])
                await q.message.reply_text(caption,reply_markup=kb,parse_mode="HTML")
        elif action=="withdrawals":
            rows=c.execute("SELECT id,owner_id,amount,currency,network,wallet_address,status FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 25").fetchall()
            await q.message.reply_text("<b>💸 USDT WITHDRAWAL REVIEW</b>\n━━━━━━━━━━━━━━━━━━\n"
                "Check the TRC20 address carefully. Send the payout manually on TRON; mark paid only after the transfer succeeds.",
                parse_mode="HTML")
            if not rows:
                await q.message.reply_text("No pending withdrawals.")
            for r in rows:
                currency=str(r["currency"] or "USDT").upper()
                amount=float(r["amount"])
                address=str(r["wallet_address"] or "")
                caption=(f"<b>💸 WITHDRAWAL REQUEST #{r['id']}</b>\n━━━━━━━━━━━━━━━━━━\n"
                         f"Owner: <code>{r['owner_id']}</code>\n"
                         f"Amount: <b>{amount:.6f} {html.escape(currency)}</b>\n"
                         f"Network: <b>{html.escape(str(r['network'] or 'USDT TRC20'))}</b>\n"
                         f"TRC20 address: <code>{html.escape(address)}</code>\n"
                         "🟡 <b>STATUS: PENDING</b>")
                kb=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 COPY TRC20 ADDRESS",copy_text=CopyTextButton(text=address))],
                    [InlineKeyboardButton("✅ MARK PAID",callback_data=f"wd:paid:{r['id']}"),
                     InlineKeyboardButton("❌ REJECT",callback_data=f"wd:reject:{r['id']}")],
                    [InlineKeyboardButton("⬅️ Withdrawals",callback_data="adm:withdrawals"),
                     InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
                ])
                await q.message.reply_text(caption,reply_markup=kb,parse_mode="HTML")
            await q.message.reply_text("⬅️ Back options:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        elif action=="products":
            # Replace the old plain-text Products by Level summary with the level-picker catalogue UI.
            await central.admin_catalog(update,ctx)
            return
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

async def audit_callback(update,ctx):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID:
        await q.answer("Admin only",show_alert=True); return
    await q.answer()
    try:
        _,category,page=q.data.split(":")
        await audit.show(update,category,int(page))
    except Exception:
        log.exception("Audit screen failed")
        await q.message.reply_text("Audit log could not be loaded.")

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
    v3.setup()
    runtime_schema.ensure_schema(v4.DB)
    v3.ensure_catalog_schema()
    central.setup()
    central.resale_setup()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("dashboard",dashboard))
    for cmd,fn in [("help",help_command),("support",support_command),("admin",admin_panel),("verifywallet",v3.verify_wallet),
      ("buy",v4.buy),("addproduct",addproduct),("setproductlevel",v4.setproductlevel),("setlevel",v4.setlevel),("setfx",v4.setfx),("setfee",v4.setfee),
      ("products",v4.products),("setprice",lambda u,c:v4.edit_product(u,c,"setprice")),("setstock",lambda u,c:v4.edit_product(u,c,"setstock")),
      ("orders",admin_orders),("orderdone",v4.orderdone),("orderreject",v4.orderreject),("myproducts",v4.myproducts),("myorders",v4.myorders),("history",v4.history),("levels",v4.levels),("centralcatalog",central.admin_catalog),("centralprice",central.set_price),("centralstock",central.set_stock),("centralactive",central.set_active),("centralorders",central.orders),("centralbuy",central.buy)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(approved_callback,pattern=r"^(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(level_callback,pattern=r"^lv:[1-5]$"))
    app.add_handler(CallbackQueryHandler(level_pay_callback,pattern=r"^lvpay:[1-5]$"))
    app.add_handler(CallbackQueryHandler(level_back_callback,pattern=r"^lvback$"))
    app.add_handler(CallbackQueryHandler(network_callback,pattern=r"^net:[0-9]+:(trc|bep)$"))
    app.add_handler(CallbackQueryHandler(txid_callback,pattern=r"^txid:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(menu_callback,pattern=r"^m:(market|level1|upgrade|sellermarket|deposit|depositstart|depositfund|deposit_fund|deposit-fund|depositdone|deposits|withdraw|withdrawsetup|withdrawsetupedit|wallet|products|orders|history|profile|dashboard|support)$"))
    app.add_handler(CallbackQueryHandler(admin_panel_callback,pattern=r"^adm:(home|owners|shops|payments|levelpayments|withdrawals|withdrawwallets|products|orders|levels|settings|central|resale|audit|back)$"))
    app.add_handler(CallbackQueryHandler(central.resale_callback,pattern=r"^rs:(list:[0-9]+|listmenu|market|review:[0-9]+|buy:[0-9]+:[0-9]+)$"))
    app.add_handler(CallbackQueryHandler(audit_callback,pattern=r"^audit:(all|payments|levels|wallet|orders|products):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(delivered_callback,pattern=r"^order:delivered:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(central.admin_level_callback,pattern=r"^ccadminlevel:[12]$"))
    app.add_handler(CallbackQueryHandler(central.admin_catalog_page_callback,pattern=r"^ccadminpage:(?:[12]:)?[0-9]+$"))
    app.add_handler(CallbackQueryHandler(central.owner_level_callback,pattern=r"^cclevel:[12]$"))
    app.add_handler(CallbackQueryHandler(central.owner_catalog_page_callback,pattern=r"^ccpage:(?:[12]:)?[0-9]+$"))
    app.add_handler(CallbackQueryHandler(central.admin_callback,pattern=r"^cc:(view|orders|price|stock|toggle|fulfill|refund)(:[0-9]+)?$"))
    app.add_handler(CallbackQueryHandler(central.owner_buy_callback,pattern=r"^ccbuy:[0-9]+:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v4.payment_review,pattern=r"^pay:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(withdrawal_review_callback,pattern=r"^wd:(paid|reject):[0-9]+$"))
    app.add_handler(MessageHandler(filters.PHOTO,v4.payment_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    print("SHOP IN INDIA V5 Telegram UI started.")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()

if __name__=="__main__": main()