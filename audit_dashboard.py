"""Read-only unified admin audit timeline for Shop in India."""
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
import html
import bot_v4 as v4

PAGE_SIZE = 12

def _rows(c, category):
    sources = {
      "all": [
        ("ADMIN", "SELECT created_at, 'ADMIN '||action AS action, COALESCE('Owner '||owner_id||' | ','')||COALESCE(details,'') AS details, admin_id AS actor FROM admin_audit"),
        ("SHOP", "SELECT created_at, 'SHOP '||action, 'Shop '||shop_id||' | Owner '||owner_id||' | '||COALESCE(old_value,'')||' -> '||COALESCE(new_value,''), actor_id FROM shop_audit"),
        ("PRODUCT", "SELECT created_at, 'PRODUCT '||action, 'Product '||product_id||' | Shop '||shop_id||' | '||COALESCE(old_value,'')||' -> '||COALESCE(new_value,''), actor_id FROM product_audit"),
        ("LEVEL", "SELECT created_at, 'LEVEL CHANGE', 'Shop '||shop_id||' | Owner '||owner_id||' | L'||old_level||' -> L'||new_level||' | '||COALESCE(reason,''), actor_id FROM level_history"),
        ("PAYMENT", "SELECT COALESCE(reviewed_at,created_at), 'PAYMENT '||status, payment_type||' #'||id||' | '||amount||' '||currency||' | Owner '||owner_id, actor_id FROM payment_history"),
        ("WITHDRAWAL", "SELECT COALESCE(reviewed_at,created_at), 'WITHDRAWAL '||status, 'Withdrawal #'||id||' | Owner '||owner_id||' | '||amount||' '||currency, NULL FROM withdrawals"),
        ("WALLET", "SELECT created_at, 'WALLET '||entry_type, amount||' '||currency||' | Owner '||owner_id||' | '||COALESCE(note,''), NULL FROM ledger"),
        ("ORDER", "SELECT updated_at, 'SELLER ORDER '||status, 'Order #'||id||' | INR '||total_amount||' | Buyer '||buyer_owner_id||' | Seller '||seller_owner_id, NULL FROM v4_orders"),
        ("CENTRAL", "SELECT created_at, 'CENTRAL ORDER '||status, 'Order #'||id||' | USDT '||total_usdt||' | Owner '||buyer_owner_id, NULL FROM central_orders")
      ],
      "payments": [],
      "levels": [],
      "wallet": [],
      "orders": [],
      "products": []
    }
    if category != "all":
        mapping = {"payments":["PAYMENT","WITHDRAWAL"],"levels":["LEVEL","SHOP"],"wallet":["WALLET"],"orders":["ORDER","CENTRAL"],"products":["PRODUCT"]}
        allowed = mapping.get(category, [])
        sources["all"] = [x for x in sources["all"] if x[0] in allowed]
    out=[]
    for kind, sql in sources["all"]:
        try:
            for r in c.execute(sql).fetchall():
                out.append((str(r[0] or ""), str(r[1] or kind), str(r[2] or ""), str(r[3] or "—")))
        except Exception:
            continue
    out.sort(key=lambda x:x[0], reverse=True)
    return out

async def show(update, category="all", page=0):
    uid=update.effective_user.id
    if not v4.admin(uid):
        await update.effective_message.reply_text("Admin only.")
        return
    c=v4.db()
    try:
        rows=_rows(c, category)
    finally:
        c.close()
    total=len(rows)
    start=page*PAGE_SIZE
    chunk=rows[start:start+PAGE_SIZE]
    labels={"all":"ALL ACTIVITY","payments":"PAYMENTS & WITHDRAWALS","levels":"LEVEL / SHOP CHANGES","wallet":"WALLET LEDGER","orders":"ORDERS","products":"PRODUCT CHANGES"}
    body=f"<b>🧾 AUDIT LOG — {html.escape(labels.get(category,'ALL ACTIVITY'))}</b>\n━━━━━━━━━━━━━━━━━━\nRecords: <b>{total}</b> • Page <b>{page+1}</b>\n\n"
    if chunk:
        body += "\n\n".join(
            f"🕒 <code>{html.escape(str(t))}</code>\n"
            f"<b>{html.escape(str(a))}</b>\n"
            f"{html.escape(str(d[:260]))}\n"
            f"Actor: <code>{html.escape(str(actor))}</code>"
            for t,a,d,actor in chunk)
    else:
        body += "No records found in this category."
    nav=[]
    filters=[("All","all"),("Payments","payments"),("Levels","levels"),("Wallet","wallet"),("Orders","orders"),("Products","products")]
    nav.extend([[InlineKeyboardButton(label,callback_data=f"audit:{key}:0") for label,key in filters[i:i+2]] for i in range(0,len(filters),2)])
    pages=[]
    if page>0: pages.append(InlineKeyboardButton("⬅️ Previous",callback_data=f"audit:{category}:{page-1}"))
    if start+PAGE_SIZE<total: pages.append(InlineKeyboardButton("Next ➡️",callback_data=f"audit:{category}:{page+1}"))
    if pages: nav.append(pages)
    nav.append([InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")])
    await update.effective_message.reply_text(body[:3900],reply_markup=InlineKeyboardMarkup(nav),parse_mode="HTML")
