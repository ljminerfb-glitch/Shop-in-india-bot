import os
import logging
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")

MAIN_MENU = [
    ["🛍️ E-Commerce", "📈 Trading"],
    ["🌐 Language"]
]

ECOM_MENU = [
    ["🔐 Login Shop", "🏪 Register Shop"],
    ["🛒 Buy Products", "📦 Sell Products"],
    ["⬅️ Back"]
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        MAIN_MENU, resize_keyboard=True
    )
    await update.message.reply_text(
        "🌍 Welcome to SHOP IN INDIA!\n\n"
        "Your Global Marketplace.\n\n"
        "Please select a service:",
        reply_markup=keyboard
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n"
        "/start - Open main menu\n"
        "/help - Help center\n"
        "/shop - Shop menu\n"
        "/support - Customer support"
    )

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(ECOM_MENU, resize_keyboard=True)
    await update.message.reply_text(
        "🛍️ E-COMMERCE MENU\n\nSelect an option:",
        reply_markup=keyboard
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text

    if msg == "🛍️ E-Commerce":
        await shop(update, context)

    elif msg == "📈 Trading":
        await update.message.reply_text(
            "📈 TRADING SECTION\n\n"
            "Welcome to the Trading section.\n"
            "Trading services will be available soon."
        )

    elif msg == "🌐 Language":
        await update.message.reply_text(
            "🌐 Select your language:\n"
            "English\nUrdu"
        )

    elif msg == "⬅️ Back":
        await start(update, context)

    elif msg == "🏪 Register Shop":
        await update.message.reply_text(
            "🏪 Shop Registration\n\n"
            "Please contact the administrator for registration approval."
        )

    elif msg == "🔐 Login Shop":
        await update.message.reply_text(
            "🔐 Shop Login\n\nLogin service will be configured next."
        )

    elif msg == "🛒 Buy Products":
        await update.message.reply_text(
            "🛒 Product marketplace will be available soon."
        )

    elif msg == "📦 Sell Products":
        await update.message.reply_text(
            "📦 Seller section will be available soon."
        )

    else:
        await update.message.reply_text(
            "Please select an option from the menu."
        )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 Welcome to Shop in India Customer Support."
    )

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN is missing!")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Shop in India Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
