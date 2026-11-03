# Shop in India Bot

## V4 upgrade files

- `migrate_v4.py`: non-destructive SQLite migration. Creates a timestamped backup before schema changes, adds audit/history/order tables, preserves existing tables and rows, and runs SQLite integrity_check.
- `bot_v4.py`: V4 entry point. Retains V3 registration, admin shop approval, wallet, product suggestion and product approval handlers; adds owner/admin dashboard commands, editable level settings, product price/stock/level editing, product/shop audit views, buy-order reservation, admin completion/rejection, and owner histories.

## Android / Termux upgrade

Stop the old bot first. From the repository folder:

```bash
cd ~/Shop-in-india-bot
git pull origin main
cp shop_in_india.db shop_in_india_manual_before_v4.db
python -m py_compile bot_v3.py bot_v4.py migrate_v4.py
python migrate_v4.py
```

Check that the migration prints `Integrity: ok` and records the backup filename. Then set your existing private environment variables (do not paste the token into chat):

```bash
export BOT_TOKEN='YOUR_EXISTING_TOKEN'
export ADMIN_ID='YOUR_TELEGRAM_NUMERIC_ID'
python bot_v4.py
```

Do not run both V3 and V4 polling processes at the same time. Do not commit the database, backup files, or token.

## V4 commands

### Owner
- `/dashboard` — shop summary
- `/myproducts` — own products
- `/myorders` — buy and sell orders
- `/history` — buy, sell, ledger, level, payment and withdrawal records
- `/mylevels` — level change history
- `/buy PRODUCT_ID QUANTITY` — create a marketplace order

### Admin
- `/dashboard`, `/levels`, `/setlevel LEVEL ACTIVATION_USDT PRODUCT_TARGET PROFIT_MIN PROFIT_MAX`
- `/setfx INR_PER_USDT` — admin changes the USDT-to-INR display/conversion rate (default: 110)
- `/products`, `/setprice PRODUCT_ID INR_PRICE`, `/setstock PRODUCT_ID QTY`, `/setproductlevel PRODUCT_ID LEVEL`
- `/orders`, `/orderdone ORDER_ID`, `/orderreject ORDER_ID REASON`
- `/audit PRODUCT_ID`, `/shopaudit SHOP_ID`, `/owner OWNER_ID`

Level values are initial configuration defaults only. L3 target is 80 products; L4 target 200; L5 target 0 means no fixed cap. Existing level settings are preserved by `INSERT OR IGNORE`, so rerunning the migration will not overwrite admin edits.

## Important operational notes

- The current active database inspected before this update had 5 shops and 2 users, but no products or orders. It did not contain an old product catalogue or old product INR prices. Therefore no product prices are fabricated. Existing products keep their current price; admin can set prices with `/setprice`.
- The L1–L5 profit ranges are stored as editable reference ranges, not guaranteed earnings. Actual seller credits are recorded only after an order is marked completed. Payment and fulfilment should be verified against a real sale.
- `/buy` reserves stock and creates a pending order; it does not collect buyer payment. Admin should use `/orderdone` only after independently verifying actual payment and delivery. Rejection restores reserved stock.
- Deposit screens show the USDT amount and its INR reference equivalent. The default display rate is 1 USDT = ₹110; Admin can change it with `/setfx`.
- Withdrawal requests show the INR debit amount and the USDT equivalent at the saved rate. Admin must independently calculate/check and manually send the payout; the bot does not transfer crypto automatically. Mark PAID only after the actual payout is sent.
- This release does not automatically process blockchain deposits, issue refunds, or send withdrawals. Do not represent a pending order as paid or a ledger credit as a completed bank/crypto payout.
- Use a test copy of the database and complete end-to-end checks before production use. Keep the database and all backups private and untracked by Git.
