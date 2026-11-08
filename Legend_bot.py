
import os
import re
import html
import json
import asyncio
import urllib.request
import urllib.error
import urllib.parse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# --------------------------------------------------
# НАСТРОЙКИ
# --------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_SECRET = os.getenv("BOT_SECRET", "").strip()

ADMIN_ID = int(os.getenv("ADMIN_ID", "7580041056"))

API_URL = os.getenv(
    "API_URL",
    "https://bloxshop-backend.onrender.com",
).strip().rstrip("/")

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://leonidfilipov2015-alt.github.io/blox-shop/",
).strip()

BOT_USERNAME = "LegendMarketShopBot"
MAX_QUANTITY = 99

ORDER_PATTERN = re.compile(
    r"^order_(\d+)_q(\d+)(?:_p([A-Za-z0-9_-]{3,32}))?$"
)


# --------------------------------------------------
# ЗАПРОСЫ К СЕРВЕРУ
# --------------------------------------------------

def backend_request(path, method="GET", data=None):
    if not API_URL:
        raise RuntimeError("Не задан API_URL")

    url = API_URL + path

    headers = {
        "Accept": "application/json",
        "X-Bot-Token": BOT_SECRET,
    }

    body = None

    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")

        try:
            result = json.loads(raw)
        except Exception:
            result = {"error": raw or str(exc)}

        raise RuntimeError(
            result.get("error")
            or result.get("message")
            or f"Ошибка сервера: {exc.code}"
        )

    except urllib.error.URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к серверу: {exc.reason}")


async def api(path, method="GET", data=None):
    return await asyncio.to_thread(
        backend_request,
        path,
        method,
        data,
    )


# --------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# --------------------------------------------------

def safe_text(value):
    return html.escape(str(value or ""), quote=True)


def order_keyboard(order_id, buyer_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Принять",
                callback_data=f"accept:{order_id}:{buyer_id}",
            ),
            InlineKeyboardButton(
                "❌ Отменить",
                callback_data=f"cancel:{order_id}:{buyer_id}",
            ),
        ]
    ])


def main_keyboard():
    return ReplyKeyboardMarkup(
        [[
            KeyboardButton(
                "🛒 Открыть Legend Market",
                web_app=WebAppInfo(url=WEB_APP_URL),
            )
        ]],
        resize_keyboard=True,
    )


async def send_shop_message(update: Update, text):
    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )


# --------------------------------------------------
# КОМАНДА /START
# --------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if context.args:
        payload = context.args[0]

        match = ORDER_PATTERN.fullmatch(payload)

        if match:
            product_id = int(match.group(1))
            quantity = int(match.group(2))
            promo_code = match.group(3)

            if quantity < 1 or quantity > MAX_QUANTITY:
                await update.message.reply_text(
                    f"Количество должно быть от 1 до {MAX_QUANTITY}."
                )
                return

            await process_order(
                update,
                product_id,
                quantity,
                promo_code,
            )
            return

    await update.message.reply_text(
        "🔥 <b>LEGEND MARKET</b>\n\n"
        "Добро пожаловать в магазин игровых товаров и услуг!\n\n"
        "Нажми кнопку ниже, чтобы открыть магазин.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# --------------------------------------------------
# КОМАНДА /MYID
# --------------------------------------------------

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or not update.message:
        return

    await update.message.reply_text(
        f"Твой Telegram ID: <code>{user.id}</code>",
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------
# ПОЛУЧЕНИЕ ТОВАРА
# --------------------------------------------------

async def get_product(product_id):
    result = await api("/api/products")

    if isinstance(result, dict):
        products = result.get("products", [])
    else:
        products = result

    if not isinstance(products, list):
        raise RuntimeError("Сервер вернул неправильный список товаров")

    for product in products:
        try:
            if int(product.get("id")) == product_id:
                return product
        except (TypeError, ValueError):
            continue

    return None


# --------------------------------------------------
# СОЗДАНИЕ ЗАКАЗА
# --------------------------------------------------

async def process_order(
    update: Update,
    product_id: int,
    quantity: int,
    promo_code: str | None,
):
    user = update.effective_user
    message = update.message

    if not user or not message:
        return

    if not BOT_SECRET:
        await message.reply_text(
            "⚠️ Бот пока не настроен: отсутствует BOT_SECRET."
        )
        return

    await message.reply_text("⏳ Проверяю заказ...")

    try:
        product = await get_product(product_id)

        if not product:
            await message.reply_text(
                "❌ Этот товар больше не найден в магазине."
            )
            return

        name = str(product.get("name") or "Товар")
        price = str(product.get("price") or "Цена не указана")
        category = str(product.get("category") or "")

        # Для аккаунтов количество всегда равно одному.
        if category == "accounts":
            quantity = 1

        order_id = f"LM-{user.id}-{message.message_id}"

        promo_result = None

        if promo_code:
            promo_result = await api(
                "/api/promocodes/reserve",
                method="POST",
                data={
                    "order_id": order_id,
                    "code": promo_code.upper(),
                    "product_id": product_id,
                    "quantity": quantity,
                },
            )

        username = (
            f"@{user.username}"
            if user.username
            else "нет username"
        )

        full_name = user.full_name or "Не указано"

        promo_text = ""

        if promo_result:
            original_total = promo_result.get("original_total")
            discounted_total = promo_result.get("discounted_total")
            discount_percent = promo_result.get("discount_percent")

            promo_text = (
                "\n\n🎟 <b>Промокод:</b> "
                f"{safe_text(promo_code.upper())}\n"
                f"📉 <b>Скидка:</b> {safe_text(discount_percent)}%\n"
                f"💰 <b>До скидки:</b> {safe_text(original_total)}\n"
                f"💵 <b>После скидки:</b> {safe_text(discounted_total)}"
            )

        admin_text = (
            "🛒 <b>НОВЫЙ ЗАКАЗ — LEGEND MARKET</b>\n\n"
            f"📦 <b>Товар:</b> {safe_text(name)}\n"
            f"💰 <b>Цена:</b> {safe_text(price)}\n"
            f"🔢 <b>Количество:</b> {quantity}"
            f"{promo_text}\n\n"
            f"👤 <b>Покупатель:</b> {safe_text(full_name)}\n"
            f"📨 <b>Username:</b> {safe_text(username)}\n"
            f"🆔 <b>ID:</b> <code>{user.id}</code>\n\n"
            f"🔖 <b>Заказ:</b> <code>{safe_text(order_id)}</code>"
        )

        try:
            await context_bot_send_admin(
                update,
                admin_text,
                order_id,
                user.id,
            )
        except Exception:
            if promo_result:
                try:
                    await api(
                        "/api/promocodes/cancel",
                        method="POST",
                        data={"order_id": order_id},
                    )
                except Exception:
                    pass

            raise

        await message.reply_text(
            "✅ <b>Заказ отправлен!</b>\n\n"
            f"📦 <b>Товар:</b> {safe_text(name)}\n"
            f"🔢 <b>Количество:</b> {quantity}\n\n"
            "Продавец получил твой заказ. "
            "Ожидай ответа в Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

    except Exception as exc:
        await message.reply_text(
            "❌ <b>Не удалось оформить заказ.</b>\n\n"
            f"{safe_text(exc)}",
            parse_mode=ParseMode.HTML,
        )


async def context_bot_send_admin(
    update,
    text,
    order_id,
    buyer_id,
):
    bot = update.get_bot()

    await bot.send_message(
        chat_id=ADMIN_ID,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=order_keyboard(order_id, buyer_id),
    )


# --------------------------------------------------
# КНОПКИ ПРИНЯТИЯ И ОТМЕНЫ
# --------------------------------------------------

async def order_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query or not query.data:
        return

    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await query.answer(
            "Эта кнопка доступна только администратору.",
            show_alert=True,
        )
        return

    match = re.fullmatch(
        r"(accept|cancel):(LM-\d+-\d+):(\d+)",
        query.data,
    )

    if not match:
        await query.answer("Некорректный заказ.", show_alert=True)
        return

    action, order_id, buyer_id_text = match.groups()
    buyer_id = int(buyer_id_text)

    await query.answer("Обрабатываю заказ...")

    try:
        if action == "accept":
            await api(
                "/api/promocodes/confirm",
                method="POST",
                data={"order_id": order_id},
            )

            await query.edit_message_text(
                (query.message.text or "")
                + "\n\n<blockquote>✅ <b>ЗАКАЗ ПРИНЯТ</b></blockquote>",
                parse_mode=ParseMode.HTML,
            )

            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    "✅ <b>Твой заказ принят!</b>\n\n"
                    "Продавец скоро свяжется с тобой для завершения сделки."
                ),
                parse_mode=ParseMode.HTML,
            )

        else:
            await api(
                "/api/promocodes/cancel",
                method="POST",
                data={"order_id": order_id},
            )

            await query.edit_message_text(
                (query.message.text or "")
                + "\n\n<blockquote>❌ <b>ЗАКАЗ ОТМЕНЁН</b></blockquote>",
                parse_mode=ParseMode.HTML,
            )

            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    "❌ <b>К сожалению, заказ отменён.</b>\n\n"
                    "Если возникла ошибка, можешь написать продавцу "
                    "@Legendjhnb."
                ),
                parse_mode=ParseMode.HTML,
            )

    except Exception as exc:
        await query.message.reply_text(
            "⚠️ Не удалось обработать заказ:\n"
            f"{safe_text(exc)}",
            parse_mode=ParseMode.HTML,
        )


# --------------------------------------------------
# ОБРАБОТКА ОШИБОК
# --------------------------------------------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    print("Ошибка бота:", context.error)


# --------------------------------------------------
# ЗАПУСК
# --------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не найден. Добавь его в переменные окружения."
        )

    if not BOT_SECRET:
        raise RuntimeError(
            "BOT_SECRET не найден. Добавь его в переменные окружения."
        )

    if not API_URL:
        raise RuntimeError("Не задан API_URL")

    application: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(
        CallbackQueryHandler(order_callback)
    )

    application.add_error_handler(error_handler)

    print("🔥 Legend Market Bot запущен!")
    print(f"Бот: @{BOT_USERNAME}")
    print(f"API: {API_URL}")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
