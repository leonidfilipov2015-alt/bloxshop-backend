
import os

from telegram import (
    Update,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

TOKEN = os.getenv("BOT_TOKEN")

WEB_APP_URL = "https://leonidfilipov2015-alt.github.io/blox-shop/"
SELLER_ID = 7580041056

PRODUCTS = {
    "lighting": "Lightning ⚡",
    "shadow": "Shadow 🌑",
    "mammoth": "Mammoth 🐘",
    "portal": "Portal 🌀",
    "dough": "Тесто 🍩",
    "gas": "Газ ☁️",
    "brainrot1": "Брейнрот 1 🧠",
    "brainrot2": "Брейнрот 2 🐸",
    "brainrot3": "Брейнрот 3 🦈"
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].startswith("buy_"):
        product_id = context.args[0][4:]
        product_name = PRODUCTS.get(product_id)

        if not product_name:
            await update.message.reply_text("❌ Такого товара нет.")
            return

        context.user_data["pending_product"] = product_name

        await update.message.reply_text(
            f"🛒 Ты выбрал: {product_name}\n\n"
            "🎮 Напиши свой Roblox Username, "
            "чтобы оформить заказ.\n\n"
            "❌ Для отмены напиши /cancel"
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🛒 Открыть Blox Shop",
                web_app=WebAppInfo(url=WEB_APP_URL)
            )
        ]
    ]

    await update.message.reply_text(
        "🔥 Добро пожаловать в Blox Shop!\n\n"
        "Здесь ты можешь покупать фрукты Blox Fruits "
        "и брейнротов Steal a Brainrot.\n\n"
        "Нажми кнопку ниже, чтобы открыть магазин!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def receive_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    product_name = context.user_data.get("pending_product")

    if not product_name:
        return

    roblox_username = update.message.text.strip()
    buyer = update.effective_user

    if len(roblox_username) > 50:
        await update.message.reply_text(
            "❌ Слишком длинный ник. Попробуй ещё раз."
        )
        return

    telegram_username = (
        f"@{buyer.username}" if buyer.username else "Нет username"
    )

    order = (
        "🛒 НОВЫЙ ЗАКАЗ!\n\n"
        f"📦 Товар: {product_name}\n"
        f"🎮 Roblox Username: {roblox_username}\n"
        f"👤 Покупатель: {buyer.first_name}\n"
        f"🔗 Telegram: {telegram_username}\n"
        f"🆔 Telegram ID: {buyer.id}"
    )

    await context.bot.send_message(
        chat_id=SELLER_ID,
        text=order
    )

    context.user_data.pop("pending_product", None)

    await update.message.reply_text(
        "✅ Твоя заявка отправлена продавцу!\n\n"
        f"📦 Товар: {product_name}\n"
        f"🎮 Roblox Username: {roblox_username}\n\n"
        "Ожидай, с тобой свяжутся! ❤️"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_product", None)

    await update.message.reply_text(
        "❌ Оформление заказа отменено."
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твой Telegram ID: {update.effective_user.id}"
    )


def main():
    if not TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("myid", myid))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_username
        )
    )

    print("Blox Shop 3.0 запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
