SHOP IN INDIA — TELEGRAM BOT STARTER

This is a starter implementation, not a production-ready global commerce/payment system.

WHAT IT CURRENTLY DOES
- Telegram reply-keyboard main menu
- User registration in SQLite
- Shop registration with owner approval
- Admin pending queue (/pending)
- Shop approval (/approve_shop_ID)
- Add and list products for approved shops
- Basic product order requests
- Manual deposit/withdraw request placeholders

IMPORTANT LIMITATIONS
- No real payment processing or wallet balance is implemented.
- No shipping carrier integration or live tracking is implemented.
- No identity/KYC, tax, fraud, refund, dispute or supplier verification system.
- Do not accept real customer funds or promise delivery using this starter.
- For reliable production hosting, use a persistent disk/database. A free host may sleep or have limited storage.

SETUP
1. Create/revoke your Telegram bot token through @BotFather. Keep it secret.
2. Find your numeric Telegram user ID using a trusted Telegram ID bot (for example, @userinfobot).
3. Deploy this project to a Python host such as Render as a Background Worker.
4. Add environment variables:
   BOT_TOKEN = your NEW token
   ADMIN_ID = your numeric Telegram user ID
5. Deploy and open your Telegram bot. Send /start.

ADMIN COMMANDS
/pending
/approve_shop_123

The admin is configured as one owner only. Do not share the token or your hosting secrets.
