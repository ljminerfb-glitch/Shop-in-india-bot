"""V5 Telegram-chat UI: inline level selection, owner panel and deposits."""
import os, logging, asyncio, html, re, secrets, json, time
from datetime import datetime, timedelta, timezone, time as datetime_time
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, CopyTextButton, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.request import HTTPXRequest
import bot_v3 as v3
import bot_v4 as v4
import central_catalog as central
import audit_dashboard as audit
import runtime_schema

TOKEN=os.getenv("BOT_TOKEN")
ADMIN_ID=int(os.getenv("ADMIN_ID","0"))
def _configured_admin_ids():
    """Return the primary admin plus any comma-separated additional admin IDs."""
    ids={ADMIN_ID} if ADMIN_ID else set()
    for raw_id in os.getenv("ADMIN_IDS","").split(","):
        raw_id=raw_id.strip()
        if raw_id.isdigit() and int(raw_id)>0:
            ids.add(int(raw_id))
    return ids

ADMIN_IDS=_configured_admin_ids()

def is_admin(user_id):
    try:
        return int(user_id) in (ADMIN_IDS | ({int(ADMIN_ID)} if ADMIN_ID else set()))
    except (TypeError,ValueError):
        return False

async def notify_admins(bot,*args,**kwargs):
    """Send the same operational notification to every configured admin."""
    results=[]
    for admin_id in sorted(ADMIN_IDS):
        try:
            results.append(await bot.send_message(chat_id=admin_id,*args,**kwargs))
        except Exception:
            log.exception("Could not notify configured admin %s",admin_id)
    return results
ROOT_REFERRAL_CODE=os.getenv("ROOT_REFERRAL_CODE","SHOPINDIA").strip().upper() or "SHOPINDIA"
logging.basicConfig(level=logging.INFO)
log=logging.getLogger("shop_v5")
try:
    from timezonefinder import TimezoneFinder
    _timezone_finder=TimezoneFinder()
except Exception:
    _timezone_finder=None
    log.exception("TimezoneFinder could not be initialized; merchant timezone capture is unavailable")
_geocode_lock=asyncio.Lock()
_geocode_last_request=0.0
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
      [InlineKeyboardButton("👥 Invited Merchants",callback_data="m:referrals")],
      [InlineKeyboardButton("💬 Support",callback_data="m:support")]
    ])


async def send_shop_profile(bot, chat_id, owner_id, caption, reply_markup=None, parse_mode="HTML"):
    """Fetch and display the owner's latest Telegram profile photo on the shop card."""
    try:
        photos=await bot.get_user_profile_photos(user_id=int(owner_id),limit=1)
        photo_sets=getattr(photos,"photos",None) or []
        if photo_sets and photo_sets[0]:
            file_id=getattr(photo_sets[0][-1],"file_id",None)
            if file_id:
                return await bot.send_photo(chat_id=chat_id,photo=file_id,caption=caption[:1024],
                                            reply_markup=reply_markup,parse_mode=parse_mode)
    except Exception:
        log.info("Could not fetch Telegram profile photo for shop owner %s",owner_id,exc_info=True)
    return await bot.send_message(chat_id=chat_id,text=caption,reply_markup=reply_markup,parse_mode=parse_mode)


async def edit_screen(ctx, update, text, reply_markup=None):
    """Append a new screen; never overwrite earlier chat messages."""
    sent = await ctx.bot.send_message(
        chat_id=update.effective_chat.id, text=text,
        reply_markup=reply_markup, parse_mode="HTML"
    )
    ctx.user_data["screen_message_id"] = sent.message_id
    ctx.user_data["screen_chat_id"] = sent.chat_id
    return sent


def back_dashboard_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="m:dashboard")]])

def location_request_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Share my location",request_location=True)]],
        resize_keyboard=True,one_time_keyboard=True
    )

def _reverse_geocode_sync(latitude,longitude):
    params=urlencode({"format":"jsonv2","lat":latitude,"lon":longitude,"zoom":10,"addressdetails":1})
    request=Request("https://nominatim.openstreetmap.org/reverse?"+params,
        headers={"User-Agent":"ShopInIndiaTelegramBot/1.0 (merchant registration)","Accept":"application/json"})
    with urlopen(request,timeout=12) as response:
        payload=json.loads(response.read().decode("utf-8"))
    address=payload.get("address") or {}
    city=(address.get("city") or address.get("town") or address.get("village")
          or address.get("municipality") or address.get("hamlet") or address.get("locality"))
    state=address.get("state") or address.get("province") or address.get("region")
    country=address.get("country")
    if not country:
        raise ValueError("Country could not be identified from the shared location.")
    return {"country":country,"state":state or "Not available","city":city or "Not available"}

async def reverse_geocode(latitude,longitude):
    """Resolve coarse admin geography; exact GPS coordinates are never persisted."""
    global _geocode_last_request
    lat_key=round(float(latitude),2); lon_key=round(float(longitude),2)
    c=v4.db()
    try:
        cached=c.execute("SELECT country,state,city FROM location_geocode_cache WHERE lat_key=? AND lon_key=?",
                         (lat_key,lon_key)).fetchone()
        if cached:
            return {"country":cached["country"],"state":cached["state"],"city":cached["city"]}
    finally:
        c.close()
    async with _geocode_lock:
        delay=1.05-(time.monotonic()-_geocode_last_request)
        if delay>0:
            await asyncio.sleep(delay)
        result=await asyncio.to_thread(_reverse_geocode_sync,latitude,longitude)
        _geocode_last_request=time.monotonic()
    c=v4.db()
    try:
        c.execute("INSERT OR REPLACE INTO location_geocode_cache(lat_key,lon_key,country,state,city,updated_at) VALUES(?,?,?,?,?,?)",
                  (lat_key,lon_key,result["country"],result["state"],result["city"],v4.now()))
        c.commit()
    finally:
        c.close()
    return result

def timezone_from_coordinates(latitude,longitude):
    if _timezone_finder is None:
        raise RuntimeError("Timezone lookup is unavailable. Install the requirements.txt packages and retry.")
    name=_timezone_finder.timezone_at(lat=float(latitude),lng=float(longitude))
    if not name:
        raise ValueError("Timezone could not be identified from this location.")
    return str(name)


async def location_handler(update,ctx):
    state=ctx.user_data.get("state")
    if state not in ("shop_location","shop_location_update"):
        return
    location=getattr(update.message,"location",None)
    if not location:
        await update.message.reply_text("Please use the Share my location button.",
                                        reply_markup=location_request_keyboard())
        return
    try:
        place=await reverse_geocode(location.latitude,location.longitude)
        location_timezone=timezone_from_coordinates(location.latitude,location.longitude)
    except Exception:
        log.exception("Merchant location or timezone resolution failed")
        await update.message.reply_text(
            "I could not verify your city and local timezone. Please try sharing your location again. "
            "Your exact GPS coordinates are never stored.",
            reply_markup=location_request_keyboard())
        return
    if state=="shop_location_update":
        c=v4.db()
        try:
            c.execute("""UPDATE shops SET location_country=?,location_state=?,location_city=?,location_timezone=?
                         WHERE owner_id=?""",
                      (place["country"],place["state"],place["city"],location_timezone,update.effective_user.id))
            c.commit()
        finally:
            c.close()
        ctx.user_data.pop("state",None)
        await update.message.reply_text(
            "<b>✅ SHOP LOCATION UPDATED</b>\n"
            f"City: <b>{html.escape(place['city'])}</b>\n"
            f"State/Province: <b>{html.escape(place['state'])}</b>\n"
            f"Country: <b>{html.escape(place['country'])}</b>\n"
            f"Local timezone: <code>{html.escape(location_timezone)}</code>\n\n"
            "Your exact GPS coordinates are not saved.",
            parse_mode="HTML",reply_markup=ReplyKeyboardRemove())
        return
    ctx.user_data.update({"location_country":place["country"],"location_state":place["state"],
                          "location_city":place["city"],"location_timezone":location_timezone})
    ctx.user_data["state"]="shop_password"
    await update.message.reply_text(
        "<b>📍 LOCATION VERIFIED</b>\n"
        f"Country: <b>{html.escape(place['country'])}</b>\n"
        f"State/Province: <b>{html.escape(place['state'])}</b>\n"
        f"City: <b>{html.escape(place['city'])}</b>\n\n"
        "Your exact GPS coordinates are not saved. Now create your shop password (minimum 8 characters).",
        parse_mode="HTML",reply_markup=ReplyKeyboardRemove())

def ensure_referral_code(c, owner_id):
    """Return a stable invitation code for a registered merchant."""
    existing=c.execute(
        "SELECT referral_code FROM merchant_referrals WHERE owner_id=?",
        (int(owner_id),)
    ).fetchone()
    if existing:
        return str(existing["referral_code"])
    shop=c.execute(
        "SELECT id FROM shops WHERE owner_id=? ORDER BY id DESC LIMIT 1",
        (int(owner_id),)
    ).fetchone()
    if not shop:
        return None
    for _ in range(20):
        code="SI"+secrets.token_hex(4).upper()
        try:
            c.execute(
                "INSERT INTO merchant_referrals(owner_id,shop_id,referral_code,created_at) VALUES(?,?,?,?)",
                (int(owner_id),int(shop["id"]),code,v4.now())
            )
            return code
        except Exception:
            collision=c.execute(
                "SELECT 1 FROM merchant_referrals WHERE referral_code=?",(code,)
            ).fetchone()
            if collision:
                continue
            raise
    raise RuntimeError("Could not allocate a unique merchant invitation code")


def ensure_all_referral_codes():
    c=v4.db()
    try:
        owners=c.execute("SELECT DISTINCT owner_id FROM shops ORDER BY owner_id").fetchall()
        for row in owners:
            ensure_referral_code(c,int(row["owner_id"]))
        c.commit()
    finally:
        c.close()


def referral_code_owner(c, code, registering_owner_id):
    """Resolve a valid inviter code. The platform code is the no-upline entry."""
    normalized=str(code or "").strip().upper()
    if not normalized or len(normalized)>64:
        return False,None
    if normalized==ROOT_REFERRAL_CODE:
        return True,None
    row=c.execute("""SELECT mr.owner_id
        FROM merchant_referrals mr JOIN shops s ON s.id=mr.shop_id
        WHERE UPPER(mr.referral_code)=? AND s.status='approved' AND mr.owner_id<>?
        ORDER BY s.id DESC LIMIT 1""",
        (normalized,int(registering_owner_id))
    ).fetchone()
    return (True,int(row["owner_id"])) if row else (False,None)


async def register_start(update,ctx):
    existing=v4.shop_for(update.effective_user.id)
    if existing:
        await update.message.reply_text(
            f"You already have a shop application.\n"
            f"Shop: {html.escape(str(existing['shop_name']))}\n"
            f"Status: {html.escape(str(existing['status']))}"
        )
        return
    invite_code=ctx.user_data.get("invite_code_prefill")
    ctx.user_data.clear()
    if invite_code:
        ctx.user_data["invite_code_prefill"]=invite_code
    ctx.user_data["state"]="shop_name"
    await update.message.reply_text("What would you like to name your shop?")


async def continue_registration_with_code(update,ctx,code):
    c=v4.db()
    try:
        valid,referrer_owner_id=referral_code_owner(
            c,code,update.effective_user.id
        )
    finally:
        c.close()
    if not valid:
        ctx.user_data["state"]="referral_code"
        await update.message.reply_text(
            "That invitation code is invalid or inactive. Enter a valid merchant invitation code, "
            f"or use the platform code {ROOT_REFERRAL_CODE}."
        )
        return False
    ctx.user_data["referrer_owner_id"]=referrer_owner_id
    ctx.user_data.pop("invite_code_prefill",None)
    ctx.user_data["state"]="shop_location"
    await update.message.reply_text(
        "<b>📍 SHARE YOUR LOCATION — REQUIRED</b>\n"
        "To complete merchant registration, share your current location using the button below. "
        "We use it to identify your country, state/province and city for the Admin application. "
        "Your exact GPS coordinates are not stored.",
        parse_mode="HTML",reply_markup=location_request_keyboard()
    )
    return True


async def show_referrals(update,ctx,shop):
    c=v4.db()
    try:
        code=ensure_referral_code(c,update.effective_user.id)
        c.commit()
        rows=c.execute("""SELECT s.owner_id,s.shop_name,s.status,s.level,
            (SELECT COALESCE(SUM(p.amount),0) FROM payment_requests p
             WHERE p.owner_id=s.owner_id AND p.status='approved' AND p.currency='USDT'
               AND p.payment_type IN ('wallet_deposit','level_activation')) AS verified_payments
            FROM shops s WHERE s.referred_by_owner_id=?
            ORDER BY s.id DESC LIMIT 50""",
            (update.effective_user.id,)
        ).fetchall()
        totals=c.execute("""SELECT COUNT(*),COALESCE(SUM(bonus_amount),0)
            FROM referral_rewards WHERE referrer_owner_id=?""",
            (update.effective_user.id,)
        ).fetchone()
    finally:
        c.close()
    username=""
    try:
        me=await ctx.bot.get_me()
        username=getattr(me,"username",None) or ""
    except Exception:
        username=""
    invite_link=f"https://t.me/{username}?start={code}" if username and code else ""
    link_text=(f'<a href="{html.escape(invite_link,quote=True)}">Join with my code</a>'
               if invite_link else "Share the invitation code above")
    body=(
        "<b>👥 MY INVITED MERCHANTS</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Your invite code: <code>{html.escape(str(code or 'Unavailable'))}</code>\n"
        f"Invite link: {link_text}\n\n"
        f"Invited merchants: <b>{len(rows)}</b>\n"
        f"Verified referral bonus earned: <b>{float(totals[1] or 0):.6f} USDT</b>\n"
        "Referral reward: <b>2% of each invited merchant's verified USDT payment</b>\n\n"
    )
    if rows:
        body+="\n\n".join(
            f"<b>{html.escape(str(r['shop_name']))}</b> • {html.escape(str(r['status']).upper())} • Level {int(r['level'] or 0)}\n"
            f"Merchant ID: <code>{r['owner_id']}</code>\n"
            f"Verified payments: <b>{float(r['verified_payments'] or 0):.6f} USDT</b>"
            for r in rows
        )
    else:
        body+="No merchants have joined with your invitation code yet."
    buttons=[]
    if code:
        buttons.append([InlineKeyboardButton(
            "📋 COPY INVITE CODE",copy_text=CopyTextButton(text=code)
        )])
    buttons.append([InlineKeyboardButton(
        "⬅️ Back to Dashboard",callback_data="m:dashboard"
    )])
    await update.callback_query.message.reply_text(
        body[:3900],reply_markup=InlineKeyboardMarkup(buttons),parse_mode="HTML"
    )


def format_usdt(value):
    """Display USDT without meaningless trailing decimal zeros, retaining up to 6 decimals."""
    rendered = f"{float(value or 0):.6f}".rstrip("0").rstrip(".")
    return rendered if rendered and rendered != "-0" else "0"


def shop_brand(shop):
    """Country-aware merchant brand; fall back only when registration country is unavailable."""
    country = shop["location_country"] if shop is not None and "location_country" in shop.keys() else None
    return f"SHOP IN {str(country).strip().upper()}" if country else "SHOP MARKETPLACE"


async def levels(bot,uid):
    shop=v4.shop_for(uid)
    brand=html.escape(shop_brand(shop))
    await bot.send_message(
        uid,
        f"<b>🔷 {brand}</b>\n━━━━━━━━━━━━━━━━━━\n"
        "<b>SELECT YOUR SHOP LEVEL</b>\n"
        "Choose a level to see its price and benefits. Payment is made in USDT.",
        reply_markup=level_kb(),parse_mode="HTML")

async def start(update,ctx):
    invite_payload=(ctx.args[0].strip() if getattr(ctx,"args",None) else None)
    ctx.user_data.clear()
    if invite_payload:
        ctx.user_data["invite_code_prefill"]=invite_payload
    s=v4.shop_for(update.effective_user.id)
    if s and str(s["control_status"] or "active").lower()!="active":
        await update.message.reply_text(
            f"⛔ Your shop {shop_number(s['id'])} is currently {str(s['control_status']).upper()} by Admin. New orders and purchases are unavailable. Please contact Support.")
        return
    if not s:
        await update.message.reply_text(
            "<b>🔷 SHOP MARKETPLACE</b>\n━━━━━━━━━━━━━━━━━━\n"
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
            f"<b>🔷 {html.escape(shop_brand(s))}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🏪 <b>{html.escape(str(s['shop_name']))}</b> • <b>LEVEL {s['level']}</b>\n"
            + (f"📍 <b>{html.escape(str(s['location_country']))}</b>\n" if s["location_country"] else "")
            + "\n<b>MERCHANT COMMAND CENTER</b>\nWelcome back! Choose a section below.",
            reply_markup=owner_kb(),parse_mode="HTML")

async def approved_callback(update,ctx):
    q=update.callback_query
    if not is_admin(q.from_user.id): await q.answer("Admin only",show_alert=True); return
    action,sid=q.data.split(":")
    if action=="reject":
        c=v4.db()
        row=c.execute("SELECT owner_id,shop_name,status FROM shops WHERE id=?",(int(sid),)).fetchone()
        c.close()
        if not row or row["status"]!="pending":
            await q.message.reply_text("Shop application missing or already reviewed."); return
        ctx.user_data["pending_admin_rejection"]={"kind":"shop_application","id":int(sid)}
        await q.message.reply_text(
            f"✍️ Enter the rejection reason for shop application #{sid}. "
            "The applicant will receive this reason. Type CANCEL to stop.")
        return
    c=v4.db(); row=c.execute("SELECT owner_id,level FROM shops WHERE id=?",(int(sid),)).fetchone(); c.close()
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


async def merchant_password_callback(update,ctx):
    q=update.callback_query
    if is_admin(q.from_user.id):
        await q.answer("Merchant account only",show_alert=True); return
    shop=v4.shop_for(q.from_user.id)
    if not shop:
        await q.answer("Shop account not found",show_alert=True); return
    await q.answer()
    ctx.user_data["merchant_password_stage"]="current"
    await q.message.reply_text("🔐 Enter your current shop password. Your message will be deleted where Telegram permits. Type CANCEL to stop.")

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

    if action=="location":
        if not s:
            await q.message.reply_text("Shop account was not found.")
            return
        ctx.user_data["state"]="shop_location_update"
        await q.message.reply_text(
            "<b>📍 UPDATE SHOP LOCATION</b>\n"
            "Share your current Telegram location using the button below. "
            "Your country, state/province and city will be saved; exact GPS coordinates will not be stored.",
            reply_markup=location_request_keyboard(),parse_mode="HTML")
        return

    if action=="dashboard":
        if not s or s["status"]!="approved" or int(s["level"])<1:
            await q.message.reply_text("Approved shop aur active level zaroori hai.")
            return
        ctx.user_data.clear()
        ctx.user_data["screen_message_id"]=q.message.message_id
        ctx.user_data["screen_chat_id"]=q.message.chat_id
        await q.message.reply_text(
            f"<b>🔷 {html.escape(shop_brand(s))}</b>\n━━━━━━━━━━━━━━━━━━\n🏪 <b>{html.escape(str(s['shop_name']))}</b> • <b>LEVEL {s['level']}</b>\n"
            + (f"📍 <b>{html.escape(str(s['location_country']))}</b>\n" if s["location_country"] else "")
            + "\n<b>MERCHANT COMMAND CENTER</b>\nChoose a section below.",
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
            deposit_bonus=c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='deposit_bonus_credit'",(q.from_user.id,)).fetchone()[0]
            referral_bonus=c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE owner_id=? AND currency='USDT' AND entry_type='referral_bonus_credit'",(q.from_user.id,)).fetchone()[0]
        finally: c.close()
        balance_line=(f"Available balance: <b>{format_usdt(available_wallet)} USDT</b>" if bal>=-1e-9 else
                      f"⚠️ <b>Wallet shortfall: {format_usdt(abs(bal))} USDT</b>")
        await q.message.reply_text(
            "<b>👛 USDT WALLET</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"{balance_line}\n"
            f"Total deposits: {format_usdt(dep)} USDT\n"
            f"Deposit bonuses (20%): {format_usdt(deposit_bonus)} USDT\n"
            f"Referral bonuses (2%): {format_usdt(referral_bonus)} USDT\n"
            f"Catalogue investment: {format_usdt(invested)} USDT\n"
            f"Total resale proceeds: {format_usdt(summary['earned'])} USDT\n"
            f"Paid withdrawals: {format_usdt(summary['paid'])} USDT\n"
            f"Pending withdrawals: {format_usdt(summary['pending'])} USDT\n"
            f"Sale proceeds remaining: {format_usdt(summary['sale_remaining'])} USDT\n"
            f"Available to withdraw: <b>{format_usdt(summary['available'])} USDT</b>\n"
            f"💚 <b>Recorded profit: +{format_usdt(profit)} USDT</b>\n\n"
            "<b>DEPOSIT NETWORKS</b>\n"
            f"TRC20: <code>{html.escape(str(v4.PAY_TRON))}</code>\n"
            f"BEP20: <code>{html.escape(str(v4.PAY_BSC))}</code>\n\n"
            "Send USDT only on the selected network and upload payment proof for verification.",
            reply_markup=back_dashboard_kb(),parse_mode="HTML")
    elif action=="referrals":
        await show_referrals(update,ctx,s)
    elif action=="market":
        await central.owner_market(update,ctx,s)
    elif action=="level1":
        await central.owner_market(update,ctx,s)
    elif action=="sellermarket":
        central.resale_setup()
        c=v4.db()
        try:
            rows=c.execute("""SELECT p.id,p.title,p.description,p.price,p.currency,p.stock,
                s.shop_name,s.owner_id,s.id shop_id
              FROM products p JOIN shops s ON s.id=p.shop_id
              WHERE p.active=1 AND p.stock>0 AND s.status='approved' AND p.shop_id<>?
              ORDER BY p.id DESC LIMIT 30""",(s["id"],)).fetchall()
            own_resale=c.execute("""SELECT l.id,l.remaining_quantity,l.ask_price_usdt,p.title
              FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id
              JOIN shops sh ON sh.id=l.shop_id
              WHERE l.status='active' AND l.remaining_quantity>0 AND l.owner_id=?
                AND sh.status='approved' AND sh.level>=p.level_required AND p.level_required<=?
              ORDER BY l.id DESC LIMIT 30""",(q.from_user.id,int(s["level"]))).fetchall()
            resale=c.execute("""SELECT l.id,l.remaining_quantity,l.ask_price_usdt,p.title,sh.shop_name,sh.owner_id,sh.id shop_id
              FROM resale_listings l JOIN central_catalog p ON p.id=l.catalog_id
              JOIN shops sh ON sh.id=l.shop_id
              WHERE l.status='active' AND l.remaining_quantity>0 AND l.owner_id<>?
                AND sh.status='approved' AND sh.level>=p.level_required AND p.level_required<=?
              ORDER BY l.id DESC LIMIT 30""",(q.from_user.id,int(s["level"]))).fetchall()
        finally: c.close()
        seller_cards={}
        for seller in list(resale)+list(rows):
            seller_cards.setdefault(int(seller["owner_id"]),(seller["shop_name"],int(seller["owner_id"])))
        for seller_name,seller_owner in seller_cards.values():
            await send_shop_profile(ctx.bot,q.message.chat_id,seller_owner,
                f"🏬 <b>{html.escape(str(seller_name))}</b>\n<i>Merchant Telegram profile photo</i>")
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
        c=v4.db()
        try:
            wallet_summary=usdt_wallet_summary(c,q.from_user.id)
            reserve_row=c.execute("""SELECT
                COALESCE(SUM(i.remaining_quantity*i.unit_cost_usdt),0) reserve_usdt,
                COALESCE(SUM(i.remaining_quantity),0) reserve_items
                FROM central_inventory i WHERE i.owner_id=?""",(q.from_user.id,)).fetchone()
            bought_row=c.execute("""SELECT COALESCE(SUM(o.quantity),0) bought_items
                FROM central_orders o
                WHERE o.buyer_owner_id=? AND o.status IN ('paid','fulfilled')
                  AND EXISTS (
                    SELECT 1 FROM ledger l
                    WHERE l.transaction_id=o.id AND l.owner_id=o.buyer_owner_id
                      AND l.entry_type='central_purchase_debit'
                      AND l.currency='USDT'
                  )""",(q.from_user.id,)).fetchone()
            central.ensure_pair_progress(c,int(s["id"]),int(q.from_user.id),s["location_timezone"] if "location_timezone" in s.keys() else None)
            pair_row=c.execute("""SELECT completed_count FROM merchant_pair_progress WHERE shop_id=?""",
                               (int(s["id"]),)).fetchone()
            profit_row=c.execute("""SELECT COALESCE(SUM(amount),0) profit
                FROM ledger WHERE owner_id=? AND currency='USDT'
                  AND entry_type IN ('resale_profit_credit','central_sale_profit')""",(q.from_user.id,)).fetchone()
        finally:
            c.close()
        reserve=float(reserve_row["reserve_usdt"] or 0)
        reserve_items=int(reserve_row["reserve_items"] or 0)
        pair_target=50
        pair_progress=min(pair_target,max(0,int(pair_row["completed_count"] or 0))) if pair_row else 0
        total_funds=max(0.0,float(wallet_summary["ledger_balance"])-float(wallet_summary["pending"]))
        profit=float(profit_row["profit"] or 0)
        body=(
            "<b>👤 MY ACCOUNT</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Shop: <b>{html.escape(str(s['shop_name']))}</b>\n"
            f"Shop ID: <code>{html.escape(str(s['shop_code'] or 'Pending'))}</code> • Level: <b>L{s['level']}</b>\n"
            f"📍 Location: <b>{html.escape(str(s['location_city'] or 'Not available'))}, {html.escape(str(s['location_state'] or 'Not available'))}, {html.escape(str(s['location_country'] or 'Not available'))}</b>\n\n"
            "<b>💼 MERCHANT FUNDS SUMMARY</b>\n"
            f"Total funds (available wallet only): <b>{format_usdt(total_funds)} USDT</b>\n"
            f"🔒 Reserve in unsold items (not included above): <b>{format_usdt(reserve)} USDT</b>\n"
            f"Items currently in reserve: <b>{reserve_items}</b>\n"
            f"Total items purchased: <b>{int(bought_row['bought_items'] or 0)}</b>\n"
            f"🔄 Completed buy/sell pairs: <b>{pair_progress}/{pair_target}</b>\n"
            f"💚 Recorded realized profit: <b>+{format_usdt(profit)} USDT</b>\n\n"
            "Withdrawal network: <b>USDT TRC20 only</b>\n"
            "Enter your TRC20 address with each withdrawal request.\n\n"
            "<b>DEPOSIT NETWORKS</b>\n"
            f"TRC20: <code>{html.escape(str(v4.PAY_TRON))}</code>\n"
            f"BEP20: <code>{html.escape(str(v4.PAY_BSC))}</code>\n\n"
        )
        profile_kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Change My Password",callback_data="m:changepassword")],
            [InlineKeyboardButton("📍 Update Location",callback_data="m:location")],
            [InlineKeyboardButton("⬅️ Back to Dashboard",callback_data="m:dashboard")]
        ])
        await send_shop_profile(ctx.bot,q.message.chat_id,q.from_user.id,body,reply_markup=profile_kb)
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



MOTIVATION_MESSAGES = [
    "🌟 Your first withdrawal is a milestone! Keep improving your shop, explore Level 2, and work towards more genuine sales, commissions and profit.",
    "🚀 Great progress! Review your Level 2 catalogue and product opportunities. Consistent service and real sales can help grow your commissions.",
    "💪 Keep building momentum: add quality listings, serve buyers well, and consider Level 2 when it suits your business. Every genuine sale is progress.",
    "📈 Your shop journey is moving forward. Learn the Level 2 benefits, improve your product range, and focus on sustainable sales and profit.",
    "🏆 One withdrawal completed—well done! Keep your shop active, support your customers, and work steadily towards stronger commissions.",
    "✨ Think growth: compare Level 2 features, keep product information clear, and build trust with buyers for more opportunities to earn.",
    "🔥 Keep learning and improving! Better listings and reliable customer service can support more sales, commissions and shop profit.",
    "🎯 Set your next shop goal: explore Level 2, improve your catalogue, and focus on genuine customer demand. Small improvements add up.",
    "🌱 Your first payout is a good start. Grow at a pace that works for you—quality products, honest service and consistent effort matter.",
    "💼 Keep developing your business. Check the Level 2 options and look for practical ways to increase sales and commission opportunities."
]

def _motivation_now():
    return datetime.now(timezone.utc)

def _motivation_stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")

def _next_motivation_message(last_index):
    choices=[i for i in range(len(MOTIVATION_MESSAGES)) if i != last_index]
    index=secrets.choice(choices)
    return index,MOTIVATION_MESSAGES[index]

async def send_due_motivation_reminders(bot, now_dt=None):
    """Deliver due hourly reminders; persisted due times survive bot restarts."""
    current=now_dt or _motivation_now()
    current_stamp=_motivation_stamp(current)
    c=v4.db()
    try:
        due=c.execute("""SELECT id,owner_id,shop_name,motivation_reminder_last_index
                         FROM shops WHERE motivation_reminder_status='on'
                           AND motivation_reminder_next_at IS NOT NULL
                           AND motivation_reminder_next_at<=?
                         ORDER BY id LIMIT 100""",(current_stamp,)).fetchall()
    finally:
        c.close()
    sent=0
    for shop in due:
        last=int(shop["motivation_reminder_last_index"] if shop["motivation_reminder_last_index"] is not None else -1)
        index,message=_next_motivation_message(last)
        body=(f"<b>⏰ SHOP GROWTH REMINDER</b>\n━━━━━━━━━━━━━━━━━━\n"
              f"🏪 <b>{html.escape(str(shop['shop_name']))}</b>\n\n{message}\n\n"
              "<i>Contact Customer Services if you need help.</i>")
        try:
            await bot.send_message(chat_id=ADMIN_ID,text=body,parse_mode="HTML")
        except Exception:
            log.info("Motivation reminder delivery failed for shop %s",shop["id"],exc_info=True)
        next_stamp=_motivation_stamp(current+timedelta(hours=1))
        c=v4.db()
        try:
            cur=c.execute("""UPDATE shops SET motivation_reminder_next_at=?,
                             motivation_reminder_last_index=?
                             WHERE id=? AND motivation_reminder_status='on'
                               AND motivation_reminder_next_at<=?""",
                          (next_stamp,index,int(shop["id"]),current_stamp))
            c.commit()
            if cur.rowcount==1:
                sent+=1
        finally:
            c.close()
    return sent

_motivation_reminder_task=None

async def merchant_pair_reset_worker(app):
    """Reset a full 50-pair cycle at 07:00 the following local calendar day."""
    while True:
        c=None
        try:
            c=v4.db()
            now=datetime.now(timezone.utc)
            now_text=now.isoformat(timespec="seconds")
            rows=c.execute("""SELECT p.shop_id,p.completed_count,p.reached_at,p.reset_at,s.location_timezone
                FROM merchant_pair_progress p JOIN shops s ON s.id=p.shop_id
                WHERE p.completed_count>=50""").fetchall()
            for row in rows:
                timezone_name=row["location_timezone"]
                if not timezone_name:
                    continue
                try:
                    zone=ZoneInfo(str(timezone_name))
                    reset_at=row["reset_at"]
                    if not reset_at:
                        if not row["reached_at"]:
                            continue
                        reached=datetime.fromisoformat(str(row["reached_at"]))
                        if reached.tzinfo is None:
                            reached=reached.replace(tzinfo=timezone.utc)
                        local_day=reached.astimezone(zone).date()
                        local_reset=datetime.combine(local_day+timedelta(days=1),datetime_time(7,0),tzinfo=zone)
                        reset_at=local_reset.astimezone(timezone.utc).isoformat(timespec="seconds")
                        c.execute("UPDATE merchant_pair_progress SET reset_at=?,updated_at=? WHERE shop_id=? AND completed_count>=50 AND reset_at IS NULL",
                                  (reset_at,now_text,int(row["shop_id"])))
                    due=datetime.fromisoformat(str(reset_at))
                    if due.tzinfo is None:
                        due=due.replace(tzinfo=timezone.utc)
                    if due<=now:
                        c.execute("""UPDATE merchant_pair_progress
                            SET completed_count=0,reached_at=NULL,reset_at=NULL,
                                cycle_started_at=?,updated_at=?
                            WHERE shop_id=? AND completed_count>=50 AND reset_at<=?""",
                            (now_text,now_text,int(row["shop_id"]),now_text))
                except Exception:
                    log.exception("Merchant pair reset processing failed for shop %s",row["shop_id"])
            c.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Merchant pair reset worker failed")
            if c:
                try: c.rollback()
                except Exception: pass
        finally:
            if c:
                c.close()
        await asyncio.sleep(30)


_pair_reset_task=None


async def send_due_level2_workflow_reminders(bot, now_dt=None):
    """Send daily workflow encouragement to Level 2+ merchants with unfinished inventory."""
    current=now_dt or _motivation_now()
    current_stamp=_motivation_stamp(current)
    next_stamp=_motivation_stamp(current+timedelta(hours=24))
    c=v4.db()
    try:
        # Schedule only merchants who still have unsold inventory and have not
        # completed the 50-pair Level 2 cycle. This reminder goes to merchants,
        # unlike the separate Admin-only shop-growth notification.
        c.execute("""INSERT OR IGNORE INTO merchant_l2_workflow_reminders(shop_id,next_at)
            SELECT s.id,? FROM shops s
            LEFT JOIN merchant_pair_progress p ON p.shop_id=s.id
            WHERE s.level=2 AND COALESCE(p.completed_count,0)<50
              AND EXISTS (SELECT 1 FROM central_inventory i
                          WHERE i.shop_id=s.id AND i.remaining_quantity>0)""",
            (_motivation_stamp(current+timedelta(hours=1)),))
        c.commit()
        due=c.execute("""SELECT s.id,s.owner_id,s.shop_name,p.completed_count
            FROM merchant_l2_workflow_reminders r
            JOIN shops s ON s.id=r.shop_id
            LEFT JOIN merchant_pair_progress p ON p.shop_id=s.id
            WHERE r.next_at<=? AND s.level=2 AND COALESCE(p.completed_count,0)<50
              AND EXISTS (SELECT 1 FROM central_inventory i
                          WHERE i.shop_id=s.id AND i.remaining_quantity>0)
            ORDER BY r.next_at LIMIT 100""",(current_stamp,)).fetchall()
    finally:
        c.close()
    sent=0
    for shop in due:
        body=(
            "<b>🌟 YOUR NEXT GROWTH OPPORTUNITY</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🏪 <b>{html.escape(str(shop['shop_name']))}</b>\n\n"
            "Congratulations on your progress! The company is preparing more order opportunities for active merchants. "
            "Please focus on completing your current orders and reselling your pending items so you can continue the workflow "
            "and be ready for the next opportunities.\n\n"
            f"📊 Completed pairs: <b>{int(shop['completed_count'] or 0)}/50</b>\n\n"
            "Stay consistent—your hard work can open the door to more opportunities. Keep going! 💪"
        )
        try:
            await bot.send_message(chat_id=int(shop["owner_id"]),text=body,parse_mode="HTML")
        except Exception:
            log.info("Level 2 workflow reminder delivery failed for shop %s",shop["id"],exc_info=True)
            continue
        c=v4.db()
        try:
            cur=c.execute("""UPDATE merchant_l2_workflow_reminders
                SET last_sent_at=?,next_at=? WHERE shop_id=? AND next_at<=?""",
                (current_stamp,next_stamp,int(shop["id"]),current_stamp))
            c.commit()
            if cur.rowcount==1:
                sent+=1
        finally:
            c.close()
    return sent


async def level2_workflow_reminder_worker(app):
    while True:
        try:
            await send_due_level2_workflow_reminders(app.bot)
        except Exception:
            log.exception("Level 2 workflow reminder worker failed")
        await asyncio.sleep(300)


_l2_workflow_reminder_task=None


async def motivation_reminder_worker(app):
    """Poll once per minute; each shop receives no more than one reminder per due hour."""
    while True:
        try:
            await send_due_motivation_reminders(app.bot)
        except Exception:
            log.exception("Hourly motivation reminder worker failed")
        await asyncio.sleep(60)

async def start_motivation_reminder_worker(app):
    global _motivation_reminder_task, _pair_reset_task, _l2_workflow_reminder_task
    # Backfill merchants whose first withdrawal was already paid before this
    # feature was deployed. Preserve Admin-stopped shops permanently.
    c=v4.db()
    try:
        due_at=_motivation_stamp(_motivation_now()+timedelta(hours=1))
        c.execute("""UPDATE shops
                     SET motivation_reminder_status='on',
                         motivation_reminder_next_at=?,
                         motivation_reminder_last_index=-1
                     WHERE motivation_reminder_status='off'
                       AND EXISTS (
                           SELECT 1 FROM withdrawals w
                           WHERE w.owner_id=shops.owner_id AND w.status='paid'
                       )""",(due_at,))
        c.commit()
    finally:
        c.close()
    _motivation_reminder_task=app.create_task(
        motivation_reminder_worker(app),name="shop-motivation-reminders")
    _pair_reset_task=app.create_task(
        merchant_pair_reset_worker(app),name="merchant-pair-resets")
    _l2_workflow_reminder_task=app.create_task(
        level2_workflow_reminder_worker(app),name="level2-workflow-reminders")


async def stop_motivation_reminder_worker(app):
    global _motivation_reminder_task, _pair_reset_task, _l2_workflow_reminder_task
    tasks=[_motivation_reminder_task,_pair_reset_task,_l2_workflow_reminder_task]
    _motivation_reminder_task=None
    _pair_reset_task=None
    _l2_workflow_reminder_task=None
    for task in tasks:
        if task and not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*(task for task in tasks if task),return_exceptions=True)

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

    # Activate the reminder only after the first confirmed paid withdrawal.
    c=v4.db()
    try:
        paid_count=int(c.execute(
            "SELECT COUNT(*) FROM withdrawals WHERE owner_id=? AND status='paid'",(owner_id,)
        ).fetchone()[0])
        if paid_count==1:
            shop=c.execute("SELECT id,shop_name,motivation_reminder_status FROM shops WHERE owner_id=? ORDER BY id DESC LIMIT 1",
                           (owner_id,)).fetchone()
            if shop and str(shop["motivation_reminder_status"] or "off")=="off":
                next_at=_motivation_stamp(_motivation_now()+timedelta(hours=1))
                c.execute("""UPDATE shops SET motivation_reminder_status='on',
                             motivation_reminder_next_at=?,motivation_reminder_last_index=-1
                             WHERE id=? AND motivation_reminder_status='off'""",
                          (next_at,int(shop["id"])))
                activated=c.execute("SELECT changes()").fetchone()[0]==1
                c.commit()
                if activated:
                    try:
                        await ctx.bot.send_message(ADMIN_ID,
                            "<b>🎉 FIRST WITHDRAWAL COMPLETED — SHOP REMINDER ACTIVATED</b>\n━━━━━━━━━━━━━━━━━━\n"
                            f"Shop: <b>{html.escape(str(shop['shop_name']))}</b>\n"
                            "Hourly Shop Growth reminders are now <b>ON</b>. These reminders are delivered to Admin only.",
                            parse_mode="HTML")
                    except Exception:
                        log.exception("Could not notify merchant that motivation reminders started")
    finally:
        c.close()
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
    password_stage=ctx.user_data.get("merchant_password_stage")
    if password_stage:
        if is_admin(update.effective_user.id):
            ctx.user_data.pop("merchant_password_stage",None); return
        supplied=(update.message.text or "").strip()
        try: await update.message.delete()
        except Exception: pass
        if supplied.lower()=="cancel":
            ctx.user_data.pop("merchant_password_stage",None)
            await ctx.bot.send_message(update.effective_chat.id,"Password change cancelled."); return
        shop=v4.shop_for(update.effective_user.id)
        if not shop:
            ctx.user_data.pop("merchant_password_stage",None)
            await ctx.bot.send_message(update.effective_chat.id,"Shop account not found."); return
        if password_stage=="current":
            if not v3.verify_password(supplied,str(shop["password_hash"] or "")):
                await ctx.bot.send_message(update.effective_chat.id,"Current password is incorrect. Enter it again or type CANCEL."); return
            ctx.user_data["merchant_password_stage"]="new"
            await ctx.bot.send_message(update.effective_chat.id,"Enter your new password (8–128 characters).")
            return
        if len(supplied)<8 or len(supplied)>128:
            await ctx.bot.send_message(update.effective_chat.id,"New password must be 8–128 characters. Try again or CANCEL."); return
        c=v4.db()
        try:
            c.execute("UPDATE shops SET password_hash=? WHERE owner_id=?",(v3.hash_password(supplied),update.effective_user.id))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Merchant password change failed")
            await ctx.bot.send_message(update.effective_chat.id,"Password could not be changed. Please retry."); return
        finally: c.close()
        ctx.user_data.pop("merchant_password_stage",None)
        await ctx.bot.send_message(update.effective_chat.id,"✅ Your shop password has been changed successfully.")
        return

    pending_photo=ctx.user_data.get("central_admin_photo")
    if pending_photo:
        if not is_admin(update.effective_user.id):
            ctx.user_data.pop("central_admin_photo",None); return
        if (update.message.text or "").strip().lower()=="cancel":
            ctx.user_data.pop("central_admin_photo",None)
            await update.message.reply_text("Picture change cancelled. The current product image is unchanged.")
        else:
            await update.message.reply_text("Please send a photo, or type CANCEL to keep the existing image.")
        return
    if ctx.user_data.get("admin_shop_search"):
        if not is_admin(update.effective_user.id):
            ctx.user_data.pop("admin_shop_search",None); return
        raw=(update.message.text or "").strip()
        if raw.lower()=="cancel":
            ctx.user_data.pop("admin_shop_search",None)
            await update.message.reply_text("Shop search cancelled."); return
        match=re.fullmatch(r"(?:SHOP-)?(\d{1,6})",raw,re.IGNORECASE)
        if not match:
            await update.message.reply_text("Invalid Shop ID. Enter the six-digit Shop ID, or CANCEL."); return
        public_id=match.group(1)
        c=v4.db()
        try:
            found=c.execute("SELECT id FROM shops WHERE shop_code=?",(public_id,)).fetchone()
            if not found and len(public_id)<6:
                found=c.execute("SELECT id FROM shops WHERE id=?",(int(public_id),)).fetchone()
        finally:
            c.close()
        ctx.user_data.pop("admin_shop_search",None)
        if not found:
            await update.message.reply_text("Shop ID not found.")
            return
        await show_admin_shop(update,ctx,int(found["id"]))
        return
    rename_sid=ctx.user_data.get("admin_shop_rename_id")
    if rename_sid is not None:
        if not is_admin(update.effective_user.id):
            ctx.user_data.pop("admin_shop_rename_id",None); return
        new_name=(update.message.text or "").strip()
        if new_name.lower()=="cancel":
            ctx.user_data.pop("admin_shop_rename_id",None)
            await update.message.reply_text("Shop-name change cancelled."); return
        if len(new_name)<2 or len(new_name)>40:
            await update.message.reply_text("Shop name must be 2–40 characters. Try again or CANCEL."); return
        c=v4.db()
        try:
            clash=c.execute("SELECT id FROM shops WHERE LOWER(shop_name)=LOWER(?) AND id<>?",(new_name,int(rename_sid))).fetchone()
            if clash:
                await update.message.reply_text("That shop name is already in use. Enter a different unique name or CANCEL."); return
            row=c.execute("SELECT owner_id,shop_name FROM shops WHERE id=?",(int(rename_sid),)).fetchone()
            if not row:
                await update.message.reply_text("Shop not found."); return
            old_name=str(row["shop_name"])
            c.execute("UPDATE shops SET shop_name=? WHERE id=?",(new_name,int(rename_sid)))
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                      (ADMIN_ID,row["owner_id"],"admin_shop_name_changed",f"{shop_number(rename_sid)}: {old_name} -> {new_name}",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Admin shop rename failed")
            await update.message.reply_text("Shop name could not be changed. Please retry."); return
        finally: c.close()
        ctx.user_data.pop("admin_shop_rename_id",None)
        await update.message.reply_text(f"Shop name updated to {new_name}.")
        await show_admin_shop(update,ctx,int(rename_sid)); return

    reset_sid=ctx.user_data.get("admin_shop_password_id")
    if reset_sid is not None:
        if not is_admin(update.effective_user.id):
            ctx.user_data.pop("admin_shop_password_id",None); return
        new_password=(update.message.text or "").strip()
        try: await update.message.delete()
        except Exception: pass
        if new_password.lower()=="cancel":
            ctx.user_data.pop("admin_shop_password_id",None)
            await update.message.reply_text("Password reset cancelled."); return
        if len(new_password)<8 or len(new_password)>128:
            await update.message.reply_text("Password must be 8–128 characters. Try again or CANCEL."); return
        c=v4.db()
        try:
            row=c.execute("SELECT owner_id FROM shops WHERE id=?",(int(reset_sid),)).fetchone()
            if not row:
                await update.message.reply_text("Shop not found."); return
            c.execute("UPDATE shops SET password_hash=? WHERE id=?",(v3.hash_password(new_password),int(reset_sid)))
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                      (ADMIN_ID,row["owner_id"],"admin_shop_password_reset",f"{shop_number(reset_sid)}: password reset by admin",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Admin password reset failed")
            await update.message.reply_text("Password could not be reset. Please retry."); return
        finally: c.close()
        ctx.user_data.pop("admin_shop_password_id",None)
        await update.message.reply_text(f"Password reset successfully for {shop_number(reset_sid)}. For security, the new password is not repeated here.")
        await show_admin_shop(update,ctx,int(reset_sid)); return

    limit_request=ctx.user_data.get("admin_shop_item_limit")
    if limit_request:
        if not is_admin(update.effective_user.id):
            ctx.user_data.pop("admin_shop_item_limit",None); return
        raw=(update.message.text or "").strip()
        if raw.lower()=="cancel":
            ctx.user_data.pop("admin_shop_item_limit",None)
            await update.message.reply_text("Item assignment cancelled."); return
        if not raw.isdigit() or not 0<=int(raw)<=int(limit_request["max"]):
            await update.message.reply_text(f"Enter a whole number from 0 to {limit_request['max']}, or CANCEL."); return
        c=v4.db()
        try:
            c.execute("""INSERT INTO merchant_level_item_limits(shop_id,level,item_limit,updated_at,updated_by)
              VALUES(?,?,?,?,?) ON CONFLICT(shop_id,level) DO UPDATE SET
              item_limit=excluded.item_limit,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
              (int(limit_request["shop_id"]),int(limit_request["level"]),int(raw),v4.now(),ADMIN_ID))
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                      (ADMIN_ID,None,"merchant_level_item_limit_set",
                       f"{shop_number(limit_request['shop_id'])}: Level {limit_request['level']} assigned {int(raw)} items",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Merchant item limit save failed")
            await update.message.reply_text("Item limit could not be saved. Please retry."); return
        finally: c.close()
        sid=int(limit_request["shop_id"]); level=int(limit_request["level"])
        ctx.user_data.pop("admin_shop_item_limit",None)
        await update.message.reply_text(f"Level {level} limit saved: {int(raw)} item(s) assigned to {shop_number(sid)}.")
        await show_admin_shop(update,ctx,sid); return

    warning_sid=ctx.user_data.get("admin_shop_warning_id")
    if warning_sid is not None:
        if not is_admin(update.effective_user.id):
            ctx.user_data.pop("admin_shop_warning_id",None); return
        reason=(update.message.text or "").strip()
        if reason.lower()=="cancel":
            ctx.user_data.pop("admin_shop_warning_id",None)
            await update.message.reply_text("Next-order warning cancelled."); return
        if not reason or len(reason)>1000:
            await update.message.reply_text("Warning reason is mandatory (1–1000 characters). Enter it or CANCEL."); return
        c=v4.db()
        try:
            row=c.execute("SELECT owner_id,shop_name FROM shops WHERE id=?",(int(warning_sid),)).fetchone()
            if not row:
                await update.message.reply_text("Shop ID not found."); return
            c.execute("UPDATE shops SET next_order_warning=1,warning_reason=?,control_note=? WHERE id=?",
                      (reason,f"Next-order warning: {reason}",int(warning_sid)))
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                      (ADMIN_ID,row["owner_id"],"shop_next_order_warning",f"{shop_number(warning_sid)}: {reason}",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Saving next-order warning failed")
            await update.message.reply_text("Warning could not be saved. Try again."); return
        finally:
            c.close()
        ctx.user_data.pop("admin_shop_warning_id",None)
        await update.message.reply_text(f"Warning saved for {shop_number(warning_sid)}. Reason will be shown on its next successful order.")
        await show_admin_shop(update,ctx,int(warning_sid))
        return
    pending_rejection=ctx.user_data.get("pending_admin_rejection")
    if pending_rejection:
        if not is_admin(update.effective_user.id):
            ctx.user_data.pop("pending_admin_rejection",None)
            return
        reason=(update.message.text or "").strip()
        if reason.lower()=="cancel":
            ctx.user_data.pop("pending_admin_rejection",None)
            await update.message.reply_text("Rejection cancelled. Request remains pending.")
            return
        if not reason or len(reason)>1000:
            await update.message.reply_text("Please enter a reason (1–1000 characters), or type CANCEL.")
            return
        kind=pending_rejection.get("kind")
        if kind=="payment":
            await v4.reject_payment_with_reason(update,ctx,reason)
        elif kind=="withdrawal":
            await v4.reject_withdrawal_with_reason(update,ctx,reason)
        elif kind=="product_suggestion":
            sid=int(pending_rejection["id"])
            c=v4.db()
            try:
                item=c.execute("SELECT * FROM product_suggestions WHERE id=?",(sid,)).fetchone()
                if not item or item["status"]!="pending":
                    ctx.user_data.pop("pending_admin_rejection",None)
                    await update.message.reply_text("Product suggestion missing or already reviewed.")
                    return
                c.execute("UPDATE product_suggestions SET status='rejected',reviewed_at=? WHERE id=? AND status='pending'",
                          (v4.now(),sid))
                c.commit()
            except Exception:
                c.rollback()
                await update.message.reply_text("Could not reject product suggestion. Try again.")
                return
            finally:
                c.close()
            ctx.user_data.pop("pending_admin_rejection",None)
            try:
                await ctx.bot.send_message(item["owner_id"],
                    f"❌ <b>PRODUCT LISTING REJECTED</b>\\nSuggestion: <code>#{sid}</code>\\n"
                    f"Reason: {html.escape(reason)}",parse_mode="HTML")
            except Exception:
                log.exception("Product rejection notification failed")
            await update.message.reply_text(f"Product suggestion #{sid} rejected. Reason sent to merchant.")
        elif kind=="central_listing":
            lid=int(pending_rejection["id"])
            c=v4.db()
            try:
                listing=c.execute("""SELECT l.owner_id,l.status,p.title FROM resale_listings l
                    JOIN central_catalog p ON p.id=l.catalog_id WHERE l.id=?""",(lid,)).fetchone()
                if not listing or listing["status"]!="active":
                    ctx.user_data.pop("pending_admin_rejection",None)
                    await update.message.reply_text("Resale listing missing or already reviewed.")
                    return
                c.execute("UPDATE resale_listings SET status='rejected' WHERE id=? AND status='active'",(lid,))
                c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                          (ADMIN_ID,listing["owner_id"],"resale_listing_rejected",f"Listing #{lid}: {reason}",v4.now()))
                c.commit()
            except Exception:
                c.rollback()
                await update.message.reply_text("Could not reject resale listing. Try again.")
                return
            finally:
                c.close()
            ctx.user_data.pop("pending_admin_rejection",None)
            try:
                await ctx.bot.send_message(listing["owner_id"],
                    f"❌ <b>YOUR RESALE LISTING WAS REJECTED</b>\\n"
                    f"Listing: <b>#{lid} — {html.escape(str(listing['title']))}</b>\\n"
                    f"Reason: {html.escape(reason)}",parse_mode="HTML")
            except Exception:
                log.exception("Resale listing rejection notification failed")
            await update.message.reply_text(f"Resale listing #{lid} rejected. Reason sent to seller.")
        elif kind=="shop_application":
            sid=int(pending_rejection["id"])
            c=v4.db()
            try:
                row=c.execute("SELECT owner_id,shop_name,status FROM shops WHERE id=?",(sid,)).fetchone()
                if not row or row["status"]!="pending":
                    ctx.user_data.pop("pending_admin_rejection",None)
                    await update.message.reply_text("Shop application missing or already reviewed.")
                    return
                c.execute("UPDATE shops SET status='rejected' WHERE id=? AND status='pending'",(sid,))
                c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                          (ADMIN_ID,row["owner_id"],"rejected",f"{row['shop_name']}: {reason}",v4.now()))
                c.commit()
            except Exception:
                c.rollback()
                await update.message.reply_text("Could not reject shop application. Try again.")
                return
            finally:
                c.close()
            ctx.user_data.pop("pending_admin_rejection",None)
            try:
                await ctx.bot.send_message(row["owner_id"],
                    f"❌ <b>YOUR SHOP APPLICATION WAS REJECTED</b>\n"
                    f"Shop: <b>{html.escape(str(row['shop_name']))}</b>\n"
                    f"Reason: {html.escape(reason)}",parse_mode="HTML")
            except Exception:
                log.exception("Shop rejection notification failed")
            await update.message.reply_text(f"Shop application #{sid} rejected. Reason sent to applicant.")
        return
    if await central.admin_resale_text(update,ctx): return
    edit=ctx.user_data.get("central_admin_edit")
    if edit:
        if not is_admin(update.effective_user.id):
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
    if state=="shop_location":
        await update.message.reply_text(
            "Location sharing is required to register. Please tap 📍 Share my location below.",
            reply_markup=location_request_keyboard())
        return
    if state=="shop_name":
        name=(update.message.text or "").strip()
        if len(name)<2 or len(name)>80:
            await update.message.reply_text("Shop name must be 2–80 characters. Please try again.")
            return
        c=v4.db()
        try:
            exists=c.execute(
                "SELECT id FROM shops WHERE LOWER(shop_name)=LOWER(?)",(name,)
            ).fetchone()
        finally:
            c.close()
        if exists:
            await update.message.reply_text(
                "That shop name is already registered. Please choose another name."
            )
            return
        ctx.user_data["new_shop_name"]=name
        prefilled=ctx.user_data.get("invite_code_prefill")
        if prefilled:
            await continue_registration_with_code(update,ctx,prefilled)
        else:
            ctx.user_data["state"]="referral_code"
            await update.message.reply_text(
                "Enter the invitation code from the merchant who invited you. "
                f"A valid invitation code is required to register. If you do not have a merchant invite code, use {ROOT_REFERRAL_CODE}."
            )
        return
    if state=="referral_code":
        await continue_registration_with_code(
            update,ctx,(update.message.text or "").strip()
        )
        return
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
        if (update.message.text or "").strip()=="🏪 Register Shop":
            await register_start(update,ctx)
            return
        was_registering=state=="shop_password"
        await v3.handle_message(update,ctx)
        if was_registering and v4.shop_for(update.effective_user.id):
            c=v4.db()
            try:
                ensure_referral_code(c,update.effective_user.id)
                c.commit()
            finally:
                c.close()
            await levels(ctx.bot,update.effective_user.id)


def shop_number(shop_id):
    """Resolve the public random Shop ID while retaining the internal DB key."""
    c=v4.db()
    try:
        row=c.execute("SELECT shop_code FROM shops WHERE id=?",(int(shop_id),)).fetchone()
        return str(row["shop_code"]) if row and row["shop_code"] else "Pending"
    finally:
        c.close()

async def show_admin_shop(update,ctx,shop_id,edit=False):
    central.setup()
    c=v4.db()
    try:
        row=c.execute("SELECT * FROM shops WHERE id=?",(int(shop_id),)).fetchone()
        item_limits={int(x["level"]):int(x["item_limit"]) for x in c.execute(
            "SELECT level,item_limit FROM merchant_level_item_limits WHERE shop_id=?",(int(shop_id),)).fetchall()}
        pair_row=c.execute(
            "SELECT completed_count FROM merchant_pair_progress WHERE shop_id=?",(int(shop_id),)).fetchone()
        allowance_row=c.execute(
            "SELECT remaining_units,total_approved_units FROM merchant_extra_item_allowances WHERE shop_id=?",
            (int(shop_id),)).fetchone()
        active_l2_count=int(c.execute(
            "SELECT COUNT(*) FROM central_catalog WHERE active=1 AND level_required=2 AND product_key LIKE 'level2_%'").fetchone()[0])
    finally:
        c.close()
    if not row:
        await update.effective_message.reply_text("Shop ID not found.")
        return
    control=str(row["control_status"] or "active").lower()
    reminder_status=str(row["motivation_reminder_status"] or "off")
    body=(f"<b>🏪 SHOP MANAGEMENT — {shop_number(row['id'])}</b>\n━━━━━━━━━━━━━━━━━━\n"
          f"Name: <b>{html.escape(str(row['shop_name']))}</b>\n"
          f"Owner: <code>{row['owner_id']}</code>\n"
          f"Application: <b>{html.escape(str(row['status']).upper())}</b> • Level {row['level']}\n"
          f"Control: <b>{html.escape(control.upper())}</b>\n"
          f"Location: {html.escape(str(row['location_city'] or 'N/A'))}, {html.escape(str(row['location_state'] or 'N/A'))}, {html.escape(str(row['location_country'] or 'N/A'))}\n"
          f"Warning queued: <b>{'YES' if row['next_order_warning'] else 'NO'}</b>\n"
          f"Motivation reminder: <b>{html.escape(reminder_status.upper())}</b>\n"
          f"Control note: {html.escape(str(row['control_note'] or 'None'))}\n"
          f"Warning reason: {html.escape(str(row['warning_reason'] or 'None'))}\n"
          f"Completed pairs: <b>{int(pair_row['completed_count'] or 0) if pair_row else 0}/50</b>\n"
          f"Extra approved purchase units: <b>{int(allowance_row['remaining_units'] or 0) if allowance_row else 0}</b> remaining "
          f"(total approved: {int(allowance_row['total_approved_units'] or 0) if allowance_row else 0})\n"
          f"Level 2 product slots: <b>{item_limits.get(2, active_l2_count)}</b>\n"
          f"Assigned item limits: " + " • ".join(
              f"L{level}: {item_limits.get(level, 'Default')}" for level in range(1,6)))
    kb_rows=[
        [InlineKeyboardButton("✏️ Change Shop Name",callback_data=f"admshop:rename:{row['id']}"),
         InlineKeyboardButton("🔐 Reset Password",callback_data=f"admshop:password:{row['id']}")],
        [InlineKeyboardButton("❄️ Freeze",callback_data=f"admshop:freeze:{row['id']}"),
         InlineKeyboardButton("⛔ Disable",callback_data=f"admshop:disable:{row['id']}")],
        [InlineKeyboardButton("✅ Enable",callback_data=f"admshop:enable:{row['id']}"),
         InlineKeyboardButton("🗑 Delete",callback_data=f"admshop:delete:{row['id']}")],
        [InlineKeyboardButton("⚠️ Warn Next Order",callback_data=f"admshop:warn:{row['id']}")],
        [InlineKeyboardButton("📦 L1 Item Limit",callback_data=f"admshop:items:{row['id']}:1"),
         InlineKeyboardButton("📦 L2 Item Limit",callback_data=f"admshop:items:{row['id']}:2")],
        [InlineKeyboardButton("📦 L3 Item Limit",callback_data=f"admshop:items:{row['id']}:3"),
         InlineKeyboardButton("📦 L4 Item Limit",callback_data=f"admshop:items:{row['id']}:4")],
        [InlineKeyboardButton("📦 L5 Item Limit",callback_data=f"admshop:items:{row['id']}:5")],
        [InlineKeyboardButton("➕ APPROVE 10 MORE ITEMS",callback_data=f"admshop:approve10:{row['id']}")],
    ]
    if reminder_status=="on":
        kb_rows.append([InlineKeyboardButton("🛑 STOP Motivation Reminder",callback_data=f"admshop:motivationstop:{row['id']}")])
    kb_rows.extend([
        [InlineKeyboardButton("🔎 Search Shop ID",callback_data="admshop:search"),
         InlineKeyboardButton("⬅️ All Shops",callback_data="adm:shops")],
        [InlineKeyboardButton("🏠 Admin Panel",callback_data="adm:home")]
    ])
    kb=InlineKeyboardMarkup(kb_rows)
    await update.effective_message.reply_text(body,reply_markup=kb,parse_mode="HTML")

async def admin_shop_callback(update,ctx):
    q=update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("Admin only",show_alert=True); return
    await q.answer()
    parts=q.data.split(":")
    action=parts[1]
    if action=="search":
        ctx.user_data["admin_shop_search"]=True
        await q.message.reply_text("🔎 Enter the six-digit public Shop ID (example: 798546). Type CANCEL to stop.")
        return
    sid=int(parts[2])
    if action=="view":
        await show_admin_shop(update,ctx,sid); return
    if action=="approve10":
        central.setup()
        c=v4.db()
        try:
            c.execute("BEGIN IMMEDIATE")
            shop_row=c.execute("SELECT owner_id,shop_name FROM shops WHERE id=?",(sid,)).fetchone()
            if not shop_row:
                c.rollback()
                await q.message.reply_text("Shop ID not found. No approval was applied.")
                return
            pair_progress=c.execute(
                "SELECT completed_count FROM merchant_pair_progress WHERE shop_id=?",(sid,)).fetchone()
            completed_pairs=int(pair_progress["completed_count"] or 0) if pair_progress else 0
            if completed_pairs<50:
                c.rollback()
                await q.message.reply_text(
                    f"Approval is available only after the merchant completes 50/50 buy-and-resell pairs. Current progress: {completed_pairs}/50.")
                return
            base_products=int(c.execute(
                "SELECT COUNT(*) FROM central_catalog WHERE active=1 AND level_required=2 AND product_key LIKE 'level2_%'").fetchone()[0])
            purchased_base=int(c.execute("""SELECT COUNT(DISTINCT o.catalog_id)
                FROM central_orders o JOIN central_catalog p ON p.id=o.catalog_id
                WHERE o.buyer_shop_id=? AND p.level_required=2 AND p.product_key LIKE 'level2_%'""",
                (sid,)).fetchone()[0])
            if purchased_base<base_products:
                c.rollback()
                await q.message.reply_text(
                    f"Finish all original Level 2 products first ({purchased_base}/{base_products} purchased). New mobile products unlock only after the original 50 are used and 50/50 pairs are completed.")
                return
            current_limit=c.execute(
                "SELECT item_limit FROM merchant_level_item_limits WHERE shop_id=? AND level=2",
                (sid,)).fetchone()
            # A shop starts with exactly the original 50 products. Extra approvals
            # reveal the next ten mobile-phone SKUs without changing legacy products.
            base_l2=int(c.execute(
                "SELECT COUNT(*) FROM central_catalog WHERE active=1 AND level_required=2 AND product_key LIKE 'level2_%'").fetchone()[0])
            active_l2=int(c.execute(
                "SELECT COUNT(*) FROM central_catalog WHERE active=1 AND level_required=2").fetchone()[0])
            old_limit=int(current_limit["item_limit"]) if current_limit else base_l2
            old_limit=max(base_l2,old_limit)
            if old_limit>=active_l2:
                c.rollback()
                await q.message.reply_text("All currently prepared Level 2 products are already assigned. Add more mobile SKUs before approving another batch.")
                return
            new_limit=min(old_limit+10,active_l2)
            c.execute("""INSERT INTO merchant_level_item_limits
                (shop_id,level,item_limit,updated_at,updated_by) VALUES(?,2,?,?,?)
                ON CONFLICT(shop_id,level) DO UPDATE SET
                  item_limit=excluded.item_limit,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
                (sid,new_limit,v4.now(),ADMIN_ID))
            c.execute("""INSERT INTO merchant_extra_item_allowances
                (shop_id,remaining_units,total_approved_units,updated_at)
                VALUES(?,10,10,?)
                ON CONFLICT(shop_id) DO UPDATE SET
                  remaining_units=merchant_extra_item_allowances.remaining_units+10,
                  total_approved_units=merchant_extra_item_allowances.total_approved_units+10,
                  updated_at=excluded.updated_at""",(sid,v4.now()))
            c.execute("""INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at)
                VALUES(?,?,?,?,?)""",
                (ADMIN_ID,shop_row["owner_id"],"merchant_extra_items_approved",
                 f"{shop_number(sid)} ({shop_row['shop_name']}): +10 purchase units; Level 2 product limit {old_limit} -> {new_limit}; newly assigned mobile SKUs",
                 v4.now()))
            c.commit()
        except Exception:
            c.rollback()
            log.exception("Admin extra-item approval failed")
            await q.message.reply_text("Approval failed safely. No extra units or product slots were added.")
            return
        finally:
            c.close()
        try:
            await ctx.bot.send_message(
                shop_row["owner_id"],
                "<b>🎉 CONGRATULATIONS!</b>\n"
                "Your hard work and dedication have earned you another opportunity to grow.\n\n"
                "You can now purchase 10 additional item units and access 10 new mobile-phone variants in your Level 2 catalogue. "
                f"Your assigned Level 2 catalogue now contains up to {new_limit} products.\n\n"
                "Keep up your great effort, complete your current orders, and make the most of this opportunity. "
                "Wishing you continued success! 🌟",
                parse_mode="HTML")
        except Exception:
            log.exception("Could not notify merchant about extra-item approval")
        await q.message.reply_text(
            f"✅ Approved 10 additional purchase units for {shop_number(sid)}. "
            f"Level 2 product slots: {old_limit} → {new_limit} (new mobile SKUs assigned).")
        await show_admin_shop(update,ctx,sid)
        return
    if action=="rename":
        ctx.user_data["admin_shop_rename_id"]=sid
        await q.message.reply_text(f"Enter the new unique shop name for {shop_number(sid)} (2–40 characters), or CANCEL.")
        return
    if action=="password":
        ctx.user_data["admin_shop_password_id"]=sid
        await q.message.reply_text(f"Enter a new password for {shop_number(sid)} (8–128 characters), or CANCEL. The password will be stored as a secure hash.")
        return
    if action=="items":
        level=int(parts[3])
        central.setup()
        c=v4.db()
        try:
            available=int(c.execute("SELECT COUNT(*) FROM central_catalog WHERE active=1 AND level_required=?",(level,)).fetchone()[0])
        finally: c.close()
        ctx.user_data["admin_shop_item_limit"]={"shop_id":sid,"level":level,"max":available}
        await q.message.reply_text(
            f"Enter how many active Level {level} products to assign to {shop_number(sid)} (0–{available}). "
            "The merchant will see only one Level 2 product at a time; the next appears after a successful purchase. Type CANCEL to stop.")
        return
    if action=="motivationstop":
        c=v4.db()
        try:
            row=c.execute("SELECT owner_id,shop_name,motivation_reminder_status FROM shops WHERE id=?",(sid,)).fetchone()
            if not row:
                await q.message.reply_text("Shop ID not found."); return
            if str(row["motivation_reminder_status"] or "off")!="on":
                await q.message.reply_text("This shop's motivation reminder is not active.")
                await show_admin_shop(update,ctx,sid); return
            c.execute("""UPDATE shops SET motivation_reminder_status='stopped',
                         motivation_reminder_next_at=NULL WHERE id=? AND motivation_reminder_status='on'""",(sid,))
            c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                      (ADMIN_ID,row["owner_id"],"motivation_reminder_stopped",
                       f"{shop_number(sid)} ({row['shop_name']}): Admin stopped hourly motivation reminders",v4.now()))
            c.commit()
        except Exception:
            c.rollback(); log.exception("Could not stop motivation reminders")
            await q.message.reply_text("Reminder could not be stopped. Please retry."); return
        finally:
            c.close()
        try:
            await ctx.bot.send_message(row["owner_id"],
                "<b>⏸ SHOP GROWTH REMINDERS STOPPED</b>\nAdmin has stopped your hourly motivational messages.",
                parse_mode="HTML")
        except Exception:
            log.exception("Could not notify shop owner about stopped reminders")
        await q.message.reply_text(f"Hourly motivation reminders stopped for {shop_number(sid)}.")
        await show_admin_shop(update,ctx,sid)
        return
    if action=="warn":
        ctx.user_data["admin_shop_warning_id"]=sid
        await q.message.reply_text(f"⚠️ Enter the mandatory warning reason for {shop_number(sid)}. This will be shown to the buyer on that shop's next successful order. Type CANCEL to stop.")
        return
    control={"freeze":"frozen","disable":"disabled","enable":"active","delete":"deleted"}.get(action)
    if not control:
        await q.message.reply_text("Unknown shop action."); return
    c=v4.db()
    try:
        row=c.execute("SELECT owner_id,shop_name,control_status FROM shops WHERE id=?",(sid,)).fetchone()
        if not row:
            await q.message.reply_text("Shop ID not found."); return
        previous=str(row["control_status"] or "active")
        note=f"Admin set shop control status to {control}"
        c.execute("UPDATE shops SET control_status=?,control_note=? WHERE id=?",(control,note,sid))
        c.execute("INSERT INTO admin_audit(admin_id,owner_id,action,details,created_at) VALUES(?,?,?,?,?)",
                  (ADMIN_ID,row["owner_id"],f"shop_{control}",f"{shop_number(sid)} ({row['shop_name']}): {previous} -> {control}",v4.now()))
        c.commit()
    except Exception:
        c.rollback(); log.exception("Shop control update failed")
        await q.message.reply_text("Shop control could not be updated. Try again."); return
    finally:
        c.close()
    try:
        await ctx.bot.send_message(row["owner_id"],
            f"🏪 <b>SHOP STATUS UPDATED</b>\nShop: <b>{html.escape(str(row['shop_name']))}</b> ({shop_number(sid)})\n"
            f"New control status: <b>{control.upper()}</b>\nNew orders and purchases are {'blocked' if control!='active' else 'enabled'}.",
            parse_mode="HTML")
    except Exception:
        log.exception("Shop control notification failed")
    await q.message.reply_text(f"{shop_number(sid)} control status updated to {control.upper()}.")
    await show_admin_shop(update,ctx,sid)

async def admin_panel(update,ctx):
    if not is_admin(update.effective_user.id):
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
      [InlineKeyboardButton("👥 System Management Center",callback_data="adm:owners")],
      [InlineKeyboardButton("🛒 Central Catalogue (USDT)",callback_data="adm:central")],
      [InlineKeyboardButton("🏷️ System Resale Market",callback_data="adm:resale")],
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
      "<b>🛡️ SHOP IN INDIA — SYSTEM CONTROL CENTER</b>\n━━━━━━━━━━━━━━━━━━\n"
      f"🏪 Shops: <b>{counts['shops']}</b>\n🛍 Products: <b>{counts['products']}</b>\n"
      f"📦 Orders: <b>{counts['v4_orders']}</b>\n💳 Pending deposits: <b>{pending_deposits}</b>\n"
      f"🎚 Pending level payments: <b>{pending_levels}</b>\n"
      f"💸 Withdrawals: <b>{counts['withdrawals']}</b> (Pending: <b>{pending_wd}</b>)\n"
      "\n"
      "<b>CONTROL CENTER</b>\nChoose a section below.",
      reply_markup=kb,parse_mode="HTML")

async def admin_panel_callback(update,ctx):
    q=update.callback_query
    if not is_admin(q.from_user.id):
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
                s.location_country,s.location_state,s.location_city,
                (SELECT COUNT(*) FROM products p WHERE p.shop_id=s.id) products,
                (SELECT COUNT(*) FROM v4_orders o WHERE o.buyer_shop_id=s.id) buys,
                (SELECT COUNT(*) FROM v4_orders o WHERE o.seller_shop_id=s.id) sells
                FROM shops s ORDER BY s.id DESC LIMIT 40""").fetchall()
            owner_body="<b>👥 OWNER CONTROL PANEL</b>\n━━━━━━━━━━━━━━━━━━\n"
            owner_body+="\n\n".join(
              f"<b>🏪 {html.escape(str(r['shop_name']))}</b> • Shop #{r['id']}\n"
              f"Owner: <code>{r['owner_id']}</code>\n"
              f"Location: {html.escape(str(r['location_city'] or 'Not available'))}, {html.escape(str(r['location_state'] or 'Not available'))}, {html.escape(str(r['location_country'] or 'Not available'))}\n"
              f"Status: {'✅' if r['status']=='approved' else '❌' if r['status']=='rejected' else '🟡'} <b>{html.escape(str(r['status']).upper())}</b> • Level {r['level']}\n"
              f"Products: {r['products']} • Buys: {r['buys']} • Sells: {r['sells']}\n"
              f"<code>/owner {r['owner_id']}</code>"
              for r in rows) if rows else "No owner shops found."
            await q.message.reply_text(owner_body[:4000],
              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]),parse_mode="HTML")
        elif action=="shops":
            rows=c.execute("SELECT id,shop_name,owner_id,status,level,control_status FROM shops ORDER BY id DESC LIMIT 10").fetchall()
            shops_body="<b>🏪 ALL SHOPS — MANAGEMENT</b>\n━━━━━━━━━━━━━━━━━━\nSearch by unique Shop ID or select a recent shop below.\n\n"
            shops_body+="\n".join(
              f"<b>{shop_number(r['id'])}</b> • {html.escape(str(r['shop_name']))}\nOwner: <code>{r['owner_id']}</code> • {html.escape(str(r['status']).upper())} • {html.escape(str(r['control_status'] or 'active').upper())}"
              for r in rows) if rows else "No shops registered."
            buttons=[[InlineKeyboardButton(f"{shop_number(r['id'])} • {str(r['shop_name'])[:20]}",callback_data=f"admshop:view:{r['id']}")] for r in rows]
            buttons.insert(0,[InlineKeyboardButton("🔎 SEARCH BY SHOP ID",callback_data="admshop:search")])
            buttons.append([InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")])
            await q.message.reply_text(shops_body[:3500],reply_markup=InlineKeyboardMarkup(buttons),parse_mode="HTML")
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
    if not is_admin(q.from_user.id):
        await q.answer("Admin only",show_alert=True); return
    await q.answer()
    try:
        _,category,page=q.data.split(":")
        await audit.show(update,category,int(page))
    except Exception:
        log.exception("Audit screen failed")
        await q.message.reply_text("Audit log could not be loaded.")

async def admin_orders(update,ctx):
    if not is_admin(update.effective_user.id):
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
    if not is_admin(q.from_user.id):
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
    if not is_admin(update.effective_user.id):
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
    if is_admin(update.effective_user.id):
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
    if is_admin(update.effective_user.id):
        await send_admin_panel(ctx.bot,update.effective_chat.id); return
    s=v4.shop_for(update.effective_user.id)
    if not s: await start(update,ctx)
    elif s["status"]!="approved": await update.message.reply_text("Awaiting Admin approval.")
    elif int(s["level"])<1: await levels(ctx.bot,update.effective_user.id)
    else: await send_shop_profile(ctx.bot,update.effective_chat.id,update.effective_user.id,
        f"🏪 <b>{html.escape(str(s['shop_name']))}</b> | L{s['level']}",reply_markup=owner_kb())

def main():
    if not TOKEN or not ADMIN_ID: raise RuntimeError("BOT_TOKEN and ADMIN_ID are required.")
    v3.setup()
    runtime_schema.ensure_schema(v4.DB)
    ensure_all_referral_codes()
    v3.ensure_catalog_schema()
    central.setup()
    central.resale_setup()
    # Separate request pools: long-polling needs a longer read timeout than normal Bot API calls.
    bot_request=HTTPXRequest(connect_timeout=20,read_timeout=30,write_timeout=30,pool_timeout=10)
    updates_request=HTTPXRequest(connect_timeout=20,read_timeout=45,write_timeout=30,pool_timeout=10)
    app=(ApplicationBuilder().token(TOKEN).request(bot_request)
         .get_updates_request(updates_request)
         .post_init(start_motivation_reminder_worker)
         .post_shutdown(stop_motivation_reminder_worker).build())
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
    app.add_handler(CallbackQueryHandler(merchant_password_callback,pattern=r"^m:changepassword$"))
    app.add_handler(CallbackQueryHandler(menu_callback,pattern=r"^m:(market|level1|upgrade|sellermarket|deposit|depositstart|depositfund|deposit_fund|deposit-fund|depositdone|deposits|withdraw|withdrawsetup|withdrawsetupedit|wallet|products|orders|history|profile|location|referrals|motivation|dashboard|support)$"))
    app.add_handler(CallbackQueryHandler(admin_panel_callback,pattern=r"^adm:(home|owners|shops|payments|levelpayments|withdrawals|withdrawwallets|products|orders|levels|settings|central|resale|audit|back)$"))
    app.add_handler(CallbackQueryHandler(admin_shop_callback,pattern=r"^admshop:(search|view:[0-9]+|freeze:[0-9]+|disable:[0-9]+|enable:[0-9]+|delete:[0-9]+|warn:[0-9]+|motivationstop:[0-9]+|rename:[0-9]+|password:[0-9]+|approve10:[0-9]+|items:[0-9]+:[1-5])$"))
    app.add_handler(CallbackQueryHandler(central.resale_callback,pattern=r"^rs:(list:[0-9]+|listmenu|market|review:[0-9]+|reject:[0-9]+|buy:[0-9]+:[0-9]+)$"))
    app.add_handler(CallbackQueryHandler(audit_callback,pattern=r"^audit:(all|payments|levels|wallet|orders|products):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(delivered_callback,pattern=r"^order:delivered:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(central.admin_level_callback,pattern=r"^ccadminlevel:[12]$"))
    app.add_handler(CallbackQueryHandler(central.admin_catalog_page_callback,pattern=r"^ccadminpage:(?:[12]:)?[0-9]+$"))
    app.add_handler(CallbackQueryHandler(central.owner_level_callback,pattern=r"^cclevel:[12]$"))
    app.add_handler(CallbackQueryHandler(central.owner_catalog_page_callback,pattern=r"^ccpage:(?:[12]:)?[0-9]+$"))
    app.add_handler(CallbackQueryHandler(central.admin_callback,pattern=r"^cc:(view|orders|price|stock|photo|toggle|fulfill|refund)(:[0-9]+)?$"))
    app.add_handler(CallbackQueryHandler(central.owner_buy_callback,pattern=r"^ccbuy:[0-9]+:[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v3.product_review,pattern=r"^product:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(v4.payment_review,pattern=r"^pay:(approve|reject):[0-9]+$"))
    app.add_handler(CallbackQueryHandler(withdrawal_review_callback,pattern=r"^wd:(paid|reject):[0-9]+$"))
    app.add_handler(MessageHandler(filters.LOCATION,location_handler))
    async def photo_handler(update,ctx):
        if ctx.user_data.get("central_admin_photo"):
            await central.admin_photo(update,ctx)
        else:
            await v4.payment_photo(update,ctx)
    app.add_handler(MessageHandler(filters.PHOTO,photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    print("SHOP IN INDIA V5 Telegram UI started.")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(timeout=30,bootstrap_retries=-1)

if __name__=="__main__": main()