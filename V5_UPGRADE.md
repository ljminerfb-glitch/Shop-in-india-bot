# Shop in India V5 — Telegram chat interface

## Install on Termux

Stop any currently running bot with Ctrl+C, then run:

```bash
cd ~/Shop-in-india-bot
git pull origin main
python -m py_compile bot_v3.py bot_v4.py bot_v5.py migrate_v4.py
python test_v4.py
```

For isolated testing, use the test database already created:

```bash
SHOP_DB=shop_in_india_test.db python bot_v5.py
```

Do not run V3, V4 and V5 polling at the same time. Keep BOT_TOKEN and ADMIN_ID in the environment; never paste the token into chat or commit database files.

## V5 chat flow

- New users see only Register Shop / Login.
- After registration, the application waits for Admin approval; legacy owner menu is hidden.
- Admin approval sends the owner an inline L1–L5 level selector.
- Until a level is activated, the owner does not see the owner panel.
- After Admin manually verifies the level payment, the owner can use /start or /dashboard to open the inline panel.
- Owner panel includes Marketplace, Deposit, Withdraw, Withdrawal Wallet, My Products, My Orders, Profit & History, and My Account.
- Deposit creates a USDT payment request; Admin manually verifies the proof. An approved deposit is recorded in the ledger as USDT deposit_credit.
- Withdrawal is restricted to available completed sale earnings and a verified wallet. Admin must send the payout manually before marking it paid.
- Conversion rate defaults to 1 USDT = INR 110 and Admin can change it with /setfx.

## Product catalogue limitation

V5 displays active products already present in the database, including their saved descriptions, INR prices and stock. The inspected database had no product catalogue, so this release does NOT pretend that Amazon products have already been imported. Do not scrape/copy Amazon listing images or descriptions in bulk without permission or an authorized data source. Product records and prices must be verified before orders can be enabled. Admin can manage imported records using /products, /setprice and /setstock.

## Safety

- V5 uses SHOP_DB; test with shop_in_india_test.db first.
- Deposits are not automatically verified on-chain.
- Withdrawals are not automatically sent.
- No earning or profit is guaranteed.
- Never use a live customer's database for first-run testing.
