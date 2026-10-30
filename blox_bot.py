
import os
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")

WEB_APP_URL = "https://leonidfilipov2015-alt.github.io/blox-shop/"
SELLER_ID = 7580041056

PRODUCTS = {
    "lighting": "Лайтинг",
    "shadow": "Шадоу",
    "mammoth": "Мамонт",
    "portal": "Портал",
    "dough": "Тесто",
    "gas": "Газ",
    "brainrot1": "Брейнрот 1",
    "brainrot2": "Брейнрот 2",
    "brainrot3": "Брейнрот 3"
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("КОМАНДА START:", context.args)

    if context.args and context.args[0].startswith("buy_"):
        product_id = context.args[0][4:]
        product_name = PRODUCTS.get(product_id)

        if not product_name:
            await update.message.reply_text("❌ Такого товара нет.")
            return

        buyer = update.effective_user
        username = f"@{buyer.username}" if buyer.username else "Нет username"

        order = (
            "🛒 НОВЫЙ ЗАКАЗ!\n\n"
            f"📦 Товар: {product_name}\n"
            f"👤 Покупатель: {buyer.first_name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 ID покупателя: {buyer.id}"
        )

        await context.bot.send_message(
            chat_id=SELLER_ID,
            text=order
        )

        await update.message.reply_text(
            f"✅ Заказ на «{product_name}» отправлен продавцу!\n"
            "Ожидай, с тобой свяжутся."
        )
        return

    keyboard = [
        [InlineKeyboardButton(
            "🛒 Открыть Blox Shop",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )]
    ]

    await update.message.reply_text(
        "🔥 Добро пожаловать в Blox Shop!\n\n"
        "Здесь ты можешь покупать фрукты Blox Fruits "
        "и брейнротов Steal a Brainrot.\n\n"
        "Нажми кнопку ниже, чтобы открыть магазин!",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
    app.add_handler(CommandHandler("myid", myid))

    print("Blox Shop 2.0 запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
