# Shop in India — Telegram Bot

V5 Telegram shop-owner and admin bot. The worker entry point is `bot_v5.py`.

## Setup

- Python 3.12+
- Install dependencies: `pip install -r requirements.txt`
- Set environment variables:
  - `BOT_TOKEN`: token from Telegram BotFather
  - `ADMIN_ID`: numeric Telegram ID of the administrator
  - `SHOP_DB` (optional): SQLite database file path; defaults to `shop_in_india.db`
- Start locally with `python bot_v5.py`.

On startup, V5 runs an additive, repeat-safe schema bootstrap. It creates missing V3/V4/V5 tables and columns without deleting existing rows. Keep a separate backup of the SQLite database before production upgrades.

## Main features

- Shop registration, password setup, and Admin approval
- Shop levels and manual USDT level-activation review
- USDT wallet deposits with network selection, optional TXID, screenshot proof, and Admin approval/rejection
- Level 1 central catalogue purchases, quantity buttons, inventory, and resale listings
- Merchant-to-merchant resale marketplace and Admin resale review
- Merchant TRC20 withdrawal-address setup and USDT withdrawal requests
- Admin queues for shops, deposits, level payments, withdrawals, orders, products, settings, and audit history
- Merchant wallet, products, orders, profit, and history screens

## Important payment and hosting notes

- Payments are **manually verified**. The bot does not monitor blockchains, verify confirmations, or send USDT automatically. Admin must independently verify the transaction on the correct network before approving a deposit or marking a withdrawal paid.
- Profit and margin figures are records/calculations, not guaranteed earnings.
- SQLite data must live on persistent storage in production. On Render, attach a persistent disk and set `SHOP_DB` to a file path inside its mount directory. The default worker filesystem may be ephemeral; do not rely on it for customer, order, or wallet records.
- Run only one active worker against a SQLite database unless the database is moved to a server database designed for concurrent workers.
- Never commit `BOT_TOKEN`, database files, wallet private keys, or other secrets to GitHub.

## CI tests

GitHub Actions compiles the Python modules and runs isolated Telegram-handler tests against a temporary SQLite database. These tests do not make real payments or modify the production database. A live Telegram/hosting smoke test is still needed after deployment.
