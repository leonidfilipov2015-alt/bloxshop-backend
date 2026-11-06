
import os
import sqlite3
import asyncio
import json
import urllib.request
import urllib.error
import re
import uuid
from decimal import Decimal, InvalidOperation

from telegram import (
    Update,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)


# ---------- SETTINGS ----------

TOKEN = os.getenv("BOT_TOKEN")

WEB_APP_URL = "https://leonidfilipov2015-alt.github.io/blox-shop/"
API_URL = "https://bloxshop-backend.onrender.com"

SELLER_ID = 7580041056
REQUIRED_CHANNEL = "@jkhhkhg"

PROMO_FOOTER = (
    "\n\n━━━━━━━━━━━━━━\n"
    "🛒 Выгодные фрукты и брейнроты в Blox Shop!\n"
    "👉 @BloxShopLegend_bot"
)

DB_NAME = "blox_bot.db"
MAX_QUANTITY = 99

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


# ---------- BACKEND API ----------

def api_request(path, method="GET", data=None, bot_auth=False):
    url = API_URL + path

    headers = {
        "Content-Type": "application/json"
    }

    if bot_auth:
        headers["X-Bot-Token"] = TOKEN

    body = None

    if data is not None:
        body = json.dumps(data).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = response.read().decode("utf-8")
            return json.loads(result) if result else {}

    except urllib.error.HTTPError as error:
        try:
            result = error.read().decode("utf-8")
            details = json.loads(result)
        except Exception:
            details = {}

        message = details.get("error") or details.get("message")

        raise RuntimeError(
            message or f"Ошибка сервера: {error.code}"
        )

    except urllib.error.URLError as error:
        raise RuntimeError(
            "Не удалось подключиться к серверу Blox Shop."
        ) from error


def get_product_from_api(product_id):
    result = api_request("/api/products")

    if isinstance(result, dict):
        products = result.get("products", [])
    else:
        products = result

    for product in products:
        if int(product.get("id", -1)) == int(product_id):
            return product

    return None


def format_product(product):
    name = product.get("name", "Неизвестный товар")
    emoji = product.get("emoji", "🎁")
    price = product.get("price", "Уточнить")

    return {
        "name": f"{name} {emoji}",
        "price": str(price),
        "id": product.get("id"),
        "category": product.get("category", "blox")
    }


def format_number(value):
    value = Decimal(str(value))

    if value == value.to_integral_value():
        return str(int(value))

    return format(value.normalize(), "f")


def calculate_total(price, quantity):
    price = str(price or "").strip()

    match = re.match(
        r"^\s*(\d+(?:[.,]\d{1,2})?)\s*(.*?)\s*$",
        price
    )

    if not match:
        return None

    try:
        unit_price = Decimal(
            match.group(1).replace(",", ".")
        )
    except InvalidOperation:
        return None

    suffix = match.group(2)
    total = unit_price * int(quantity)

    return f"{format_number(total)} {suffix}".strip()


# ---------- PROMOCODES ----------

async def reserve_promo(order_id, product_id, code, quantity):
    return await asyncio.to_thread(
        api_request,
        "/api/promocodes/reserve",
        "POST",
        {
            "order_id": order_id,
            "product_id": int(product_id),
            "code": code,
            "quantity": int(quantity)
        },
        True
    )


async def confirm_promo(order_id):
    return await asyncio.to_thread(
        api_request,
        "/api/promocodes/confirm",
        "POST",
        {
            "order_id": order_id
        },
        True
    )


async def cancel_promo(order_id):
    return await asyncio.to_thread(
        api_request,
        "/api/promocodes/cancel",
        "POST",
        {
            "order_id": order_id
        },
        True
    )


# ---------- ORDER LINK PARSER ----------

def parse_order_argument(argument):
    """
    Новый формат:
    order_ID_qQUANTITY
    order_ID_qQUANTITY_pPROMOCODE

    Старые форматы:
    order_ID
    order_ID_PROMOCODE
    """

    if not argument.startswith("order_"):
        return None

    value = argument[len("order_"):]

    match = re.fullmatch(
        r"(\d+)_q(\d+)(?:_p([A-Z0-9_-]+))?",
        value,
        flags=re.IGNORECASE
    )

    if match:
        return {
            "product_id": int(match.group(1)),
            "quantity": int(match.group(2)),
            "promo_code": (
                match.group(3).upper()
                if match.group(3)
                else None
            )
        }

    parts = value.split("_", 1)

    if not parts[0].isdigit():
        return None

    return {
        "product_id": int(parts[0]),
        "quantity": 1,
        "promo_code": (
            parts[1].upper()
            if len(parts) > 1 and parts[1]
            else None
        )
    }


# ---------- DATABASE ----------

def init_db():
    with sqlite3.connect(DB_NAME) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                ads_enabled INTEGER DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        """)


def save_user(user_id):
    with sqlite3.connect(DB_NAME) as db:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,)
        )


def get_users():
    with sqlite3.connect(DB_NAME) as db:
        return [
            row[0] for row in db.execute(
                "SELECT user_id FROM users WHERE ads_enabled = 1"
            )
        ]


def get_chats():
    with sqlite3.connect(DB_NAME) as db:
        return [
            row[0] for row in db.execute(
                "SELECT chat_id FROM chats"
            )
        ]


# ---------- SUBSCRIPTION ----------

async def is_subscribed(user_id, context):
    try:
        member = await context.bot.get_chat_member(
            REQUIRED_CHANNEL,
            user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator"
        )

    except TelegramError:
        return False


async def require_subscription(update, context):
    user = update.effective_user

    if await is_subscribed(user.id, context):
        return True

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Подписаться на канал",
                url="https://t.me/jkhhkhg"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Проверить подписку",
                callback_data="check_subscription"
            )
        ]
    ]

    if update.callback_query:
        await update.callback_query.message.reply_text(
            "❌ Для использования рассылки подпишись на наш канал!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.effective_message.reply_text(
            "❌ Для использования рассылки подпишись на наш канал!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    return False


async def check_subscription_button(update, context):
    query = update.callback_query
    await query.answer()

    if await is_subscribed(query.from_user.id, context):
        await query.message.reply_text(
            "✅ Подписка подтверждена!\n\n"
            "Теперь можешь пользоваться рассылкой.\n"
            "Напиши /broadcast"
        )
    else:
        await query.message.reply_text(
            "❌ Подписка пока не найдена. "
            "Подпишись и попробуй ещё раз."
        )


# ---------- START AND ORDERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id)

    if context.args:
        argument = context.args[0]

        if argument.startswith("order_"):
            order_data = parse_order_argument(argument)

            if not order_data:
                await update.message.reply_text(
                    "❌ Некорректная ссылка на заказ. "
                    "Открой магазин и попробуй снова."
                )
                return

            product_id = order_data["product_id"]
            quantity = order_data["quantity"]
            promo_code = order_data["promo_code"]

            if quantity < 1 or quantity > MAX_QUANTITY:
                await update.message.reply_text(
                    "❌ Некорректное количество товара."
                )
                return

            await update.message.reply_text(
                "⏳ Загружаю товар из Blox Shop..."
            )

            try:
                product = await asyncio.to_thread(
                    get_product_from_api,
                    product_id
                )

            except Exception as error:
                await update.message.reply_text(
                    f"❌ Не удалось загрузить товар.\n\n"
                    f"{error}\n\n"
                    "Попробуй открыть магазин ещё раз."
                )
                return

            if not product:
                await update.message.reply_text(
                    "❌ Этот товар больше не найден в магазине."
                )
                return

            formatted = format_product(product)

            if formatted["category"] == "accounts":
                quantity = 1

            formatted["quantity"] = quantity

            context.user_data["pending_product"] = formatted
            context.user_data["pending_promo"] = promo_code

            total = calculate_total(
                formatted["price"],
                quantity
            )

            message = (
                f"🛒 Ты выбрал: {formatted['name']}\n"
                f"💰 Цена за 1 шт.: {formatted['price']}\n"
            )

            if formatted["category"] != "accounts":
                message += f"📦 Количество: {quantity}\n"

            if total is not None:
                message += f"💵 Итого: {total}\n"
            else:
                message += "💵 Итоговую цену уточнит продавец.\n"

            if promo_code:
                message += (
                    f"\n🎟 Промокод: {promo_code}\n"
                    "⏳ Скидка будет проверена при оформлении заказа.\n"
                )

            message += (
                "\n🎮 Напиши свой Roblox Username, "
                "чтобы оформить заказ.\n\n"
                "❌ Для отмены напиши /cancel"
            )

            await update.message.reply_text(message)
            return

        if argument.startswith("buy_"):
            product_id = argument[4:]
            product_name = PRODUCTS.get(product_id)

            if not product_name:
                await update.message.reply_text(
                    "❌ Такого товара нет."
                )
                return

            context.user_data["pending_product"] = {
                "name": product_name,
                "price": "Уточнить",
                "id": None,
                "category": "blox",
                "quantity": 1
            }

            context.user_data["pending_promo"] = None

            await update.message.reply_text(
                f"🛒 Ты выбрал: {product_name}\n\n"
                "🎮 Напиши свой Roblox Username, "
                "чтобы оформить заказ.\n\n"
                "❌ Для отмены напиши /cancel"
            )
            return

    keyboard = [[
        InlineKeyboardButton(
            "🛒 Открыть Blox Shop",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )
    ]]

    await update.message.reply_text(
        "🔥 Добро пожаловать в Blox Shop!\n\n"
        "Здесь ты можешь покупать фрукты Blox Fruits, "
        "брейнротов Steal a Brainrot и игровые аккаунты.\n\n"
        "Нажми кнопку ниже, чтобы открыть магазин!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ---------- AD PERMISSIONS ----------

async def ads_on(update, context):
    save_user(update.effective_user.id)

    with sqlite3.connect(DB_NAME) as db:
        db.execute(
            "UPDATE users SET ads_enabled = 1 WHERE user_id = ?",
            (update.effective_user.id,)
        )

    await update.message.reply_text(
        "✅ Ты разрешил получать рекламные рассылки.\n"
        "Отключить их можно командой /ads_off"
    )


async def ads_off(update, context):
    with sqlite3.connect(DB_NAME) as db:
        db.execute(
            "UPDATE users SET ads_enabled = 0 WHERE user_id = ?",
            (update.effective_user.id,)
        )

    await update.message.reply_text(
        "🔕 Рекламные рассылки отключены."
    )


# ---------- GROUP REGISTRATION ----------

async def addchat(update, context):
    if not await require_subscription(update, context):
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text(
            "Добавь бота в группу или канал, "
            "выдай необходимые права и напиши /addchat там."
        )
        return

    try:
        member = await context.bot.get_chat_member(
            chat.id,
            user.id
        )

        if member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "❌ Добавить чат может только его администратор."
            )
            return

        bot_member = await context.bot.get_chat_member(
            chat.id,
            context.bot.id
        )

        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "❌ Сначала сделай бота администратором."
            )
            return

        with sqlite3.connect(DB_NAME) as db:
            db.execute(
                "INSERT OR REPLACE INTO chats (chat_id, title) "
                "VALUES (?, ?)",
                (chat.id, chat.title or "Без названия")
            )

        await update.message.reply_text(
            "✅ Этот чат добавлен в список рассылки!"
        )

    except TelegramError:
        await update.message.reply_text(
            "❌ Не удалось зарегистрировать чат. "
            "Проверь права бота."
        )


async def removechat(update, context):
    if not await require_subscription(update, context):
        return

    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text(
            "Команду нужно использовать в зарегистрированной группе."
        )
        return

    try:
        member = await context.bot.get_chat_member(
            chat.id,
            update.effective_user.id
        )

        if member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "❌ Только администратор может удалить чат."
            )
            return

        with sqlite3.connect(DB_NAME) as db:
            db.execute(
                "DELETE FROM chats WHERE chat_id = ?",
                (chat.id,)
            )

        await update.message.reply_text(
            "✅ Чат удалён из рассылки."
        )

    except TelegramError:
        await update.message.reply_text("❌ Ошибка удаления.")


# ---------- BROADCAST CREATOR ----------

async def broadcast(update, context):
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "❌ Создавать рассылку нужно в личных сообщениях бота."
        )
        return

    if not await require_subscription(update, context):
        return

    context.user_data["broadcast_waiting"] = True

    await update.message.reply_text(
        "📢 СОЗДАНИЕ РАССЫЛКИ\n\n"
        "Отправь мне рекламный текст или фотографию с подписью.\n\n"
        "Я автоматически добавлю рекламу Blox Shop.\n\n"
        "❌ Для отмены напиши /cancel"
    )


async def receive_broadcast(update, context):
    if not context.user_data.get("broadcast_waiting"):
        return False

    if not await require_subscription(update, context):
        return True

    message = update.message

    if message.photo:
        context.user_data["broadcast_content"] = {
            "type": "photo",
            "file_id": message.photo[-1].file_id,
            "text": message.caption or ""
        }

        preview = (message.caption or "") + PROMO_FOOTER

    elif message.text:
        preview = message.text + PROMO_FOOTER

        if len(preview) > 4096:
            await message.reply_text(
                "❌ Текст слишком длинный. Сократи его и попробуй ещё раз."
            )
            return True

        context.user_data["broadcast_content"] = {
            "type": "text",
            "text": message.text
        }

    else:
        await message.reply_text(
            "❌ Отправь текст или фотографию."
        )
        return True

    context.user_data["broadcast_waiting"] = False

    keyboard = [
        [
            InlineKeyboardButton(
                "👥 Пользователям",
                callback_data="send_users"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Группам и каналам",
                callback_data="send_chats"
            )
        ],
        [
            InlineKeyboardButton(
                "🔥 Всем",
                callback_data="send_both"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="cancel_broadcast"
            )
        ]
    ]

    if context.user_data["broadcast_content"]["type"] == "photo":
        await message.reply_photo(
            photo=context.user_data["broadcast_content"]["file_id"],
            caption=preview[:1024],
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await message.reply_text(
            "👀 ПРЕДПРОСМОТР:\n\n" + preview,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    return True


# ---------- SENDING ----------

async def send_content(chat_id, content, context):
    text = content["text"] + PROMO_FOOTER

    if content["type"] == "photo":
        if len(text) <= 1024:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=content["file_id"],
                caption=text
            )
        else:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=content["file_id"]
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=text
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text
        )


async def broadcast_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_broadcast":
        context.user_data.pop("broadcast_content", None)
        await query.edit_message_text("❌ Рассылка отменена.")
        return

    if not await is_subscribed(query.from_user.id, context):
        await query.message.reply_text(
            "❌ Для рассылки нужна подписка на @jkhhkhg."
        )
        return

    content = context.user_data.get("broadcast_content")

    if not content:
        await query.message.reply_text(
            "❌ Реклама не найдена. Начни заново: /broadcast"
        )
        return

    targets = []

    if query.data in ("send_users", "send_both"):
        targets.extend(get_users())

    if query.data in ("send_chats", "send_both"):
        targets.extend(get_chats())

    targets = list(dict.fromkeys(targets))

    if not targets:
        await query.message.reply_text(
            "❌ Пока нет доступных получателей.\n\n"
            "Пользователи должны включить /ads_on, "
            "а группы нужно зарегистрировать через /addchat."
        )
        return

    await query.edit_message_text(
        f"📤 Начинаю рассылку...\nПолучателей: {len(targets)}"
    )

    success = 0
    failed = 0

    for target in targets:
        try:
            await send_content(target, content, context)
            success += 1
            await asyncio.sleep(0.05)

        except TelegramError:
            failed += 1

    context.user_data.pop("broadcast_content", None)

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            "✅ РАССЫЛКА ЗАВЕРШЕНА!\n\n"
            f"📨 Успешно отправлено: {success}\n"
            f"❌ Ошибок отправки: {failed}"
        )
    )


# ---------- PHOTO HANDLER ----------

async def receive_photo(update, context):
    if await receive_broadcast(update, context):
        return

    if update.effective_user.id != SELLER_ID:
        await update.message.reply_text(
            "❌ Эта команда доступна только продавцу."
        )
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    await update.message.reply_text(
        "✅ Фото получено!\n\n"
        "🆔 ID фотографии:\n"
        f"<code>{file_id}</code>\n\n"
        "Скопируй этот ID и вставь в админку Blox Shop.",
        parse_mode="HTML"
    )


# ---------- ORDER HANDLER ----------

async def receive_username(update, context):
    if await receive_broadcast(update, context):
        return

    pending = context.user_data.get("pending_product")

    if not pending:
        return

    roblox_username = update.message.text.strip()
    buyer = update.effective_user

    if not roblox_username or len(roblox_username) > 50:
        await update.message.reply_text(
            "❌ Некорректный ник. Попробуй ещё раз."
        )
        return

    product_name = pending["name"]
    product_price = pending.get("price", "Уточнить")
    product_id = pending.get("id")
    category = pending.get("category", "blox")

    quantity = int(pending.get("quantity", 1))

    if category == "accounts":
        quantity = 1

    promo_code = context.user_data.get("pending_promo")
    promo_info = None
    order_id = None

    # Уникальный ID для каждого заказа.
    if promo_code:
        if not product_id:
            await update.message.reply_text(
                "❌ Не удалось определить товар для промокода. "
                "Открой магазин и попробуй снова."
            )
            return

        order_id = uuid.uuid4().hex

        await update.message.reply_text(
            "🎟 Резервирую промокод и оформляю заказ..."
        )

        try:
            promo_info = await reserve_promo(
                order_id,
                product_id,
                promo_code,
                quantity
            )

            if not isinstance(promo_info, dict):
                raise RuntimeError("Сервер вернул некорректный ответ.")

            if promo_info.get("success") is False:
                raise RuntimeError(
                    promo_info.get("error")
                    or "Промокод недействителен."
                )

            # Поддержка ответа, если сервер вернул данные внутри result.
            if isinstance(promo_info.get("result"), dict):
                promo_info = promo_info["result"]

        except Exception as error:
            await update.message.reply_text(
                f"❌ Не удалось применить промокод:\n{error}\n\n"
                "Открой магазин и попробуй оформить заказ заново."
            )

            context.user_data.pop("pending_product", None)
            context.user_data.pop("pending_promo", None)
            return

    original_total = calculate_total(
        product_price,
        quantity
    )

    final_total = original_total

    if promo_info:
        original_total = promo_info.get(
            "original_total",
            original_total
        )

        final_total = promo_info.get(
            "discounted_total",
            promo_info.get("discounted_price", original_total)
        )

    telegram_username = (
        f"@{buyer.username}" if buyer.username else "Нет username"
    )

    order = (
        "🛒 НОВЫЙ ЗАКАЗ!\n\n"
        f"📦 Товар: {product_name}\n"
        f"💰 Цена за 1 шт.: {product_price}\n"
    )

    if category != "accounts":
        order += f"📦 Количество: {quantity}\n"

    if original_total is not None:
        order += f"💵 Сумма до скидки: {original_total}\n"

    if promo_info:
        percent = promo_info.get("discount_percent", 0)

        order += (
            f"🎟 Промокод: {promo_code}\n"
            f"📉 Скидка: {percent}%\n"
            f"💰 Итого со скидкой: {final_total}\n"
        )
    elif final_total is not None:
        order += f"💰 Итого: {final_total}\n"
    else:
        order += "💰 Итоговую стоимость нужно уточнить.\n"

    order += (
        f"\n🎮 Roblox Username: {roblox_username}\n"
        f"👤 Покупатель: {buyer.first_name}\n"
        f"🔗 Telegram: {telegram_username}\n"
        f"🆔 Telegram ID: {buyer.id}"
    )

    # Сначала отправляем заказ продавцу.
    try:
        await context.bot.send_message(
            chat_id=SELLER_ID,
            text=order
        )

    except TelegramError:
        # Если заказ не отправлен, освобождаем промокод.
        if order_id:
            try:
                await cancel_promo(order_id)
            except Exception as cancel_error:
                print(
                    "Не удалось отменить резерв промокода:",
                    cancel_error
                )

        await update.message.reply_text(
            "❌ Не удалось отправить заказ продавцу. "
            "Попробуй ещё раз чуть позже."
        )
        return

    # Заказ отправлен продавцу — подтверждаем промокод.
    promo_confirmed = True

    if order_id:
        try:
            result = await confirm_promo(order_id)

            if isinstance(result, dict) and result.get("success") is False:
                raise RuntimeError(
                    result.get("error", "Подтверждение не выполнено.")
                )

        except Exception as error:
            promo_confirmed = False
            print("Ошибка подтверждения промокода:", error)

            try:
                await context.bot.send_message(
                    chat_id=SELLER_ID,
                    text=(
                        "⚠️ ВНИМАНИЕ: заказ отправлен, но сервер "
                        "не подтвердил использование промокода.\n"
                        f"ID заказа: {order_id}\n"
                        f"Промокод: {promo_code}"
                    )
                )
            except TelegramError:
                pass

    context.user_data.pop("pending_product", None)
    context.user_data.pop("pending_promo", None)

    confirmation = (
        "✅ Твоя заявка отправлена продавцу!\n\n"
        f"📦 Товар: {product_name}\n"
    )

    if category != "accounts":
        confirmation += f"📦 Количество: {quantity}\n"

    if final_total is not None:
        confirmation += f"💰 Итого: {final_total}\n"

    confirmation += f"🎮 Roblox Username: {roblox_username}\n"

    if promo_code:
        if promo_confirmed:
            confirmation += f"🎟 Промокод применён: {promo_code}\n"
        else:
            confirmation += (
                f"🎟 Промокод: {promo_code}\n"
                "⚠️ Продавец проверит скидку при обработке заказа.\n"
            )

    confirmation += "\nОжидай, с тобой свяжутся! ❤️"

    await update.message.reply_text(confirmation)


# ---------- CANCEL ----------

async def cancel(update, context):
    context.user_data.pop("pending_product", None)
    context.user_data.pop("pending_promo", None)
    context.user_data.pop("broadcast_waiting", None)
    context.user_data.pop("broadcast_content", None)

    await update.message.reply_text(
        "❌ Действие отменено."
    )


async def myid(update, context):
    await update.message.reply_text(
        f"Твой Telegram ID: {update.effective_user.id}"
    )


# ---------- HELP ----------

async def help_command(update, context):
    await update.message.reply_text(
        "🤖 Blox Shop — команды\n\n"
        "/start — открыть магазин\n"
        "/broadcast — создать рекламу\n"
        "/ads_on — разрешить получать рекламу\n"
        "/ads_off — отключить рекламу\n"
        "/addchat — добавить группу или канал\n"
        "/removechat — убрать группу или канал\n"
        "/cancel — отменить действие\n"
        "/myid — узнать свой Telegram ID"
    )


# ---------- MAIN ----------

def main():
    if not TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN!")

    init_db()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("ads_on", ads_on))
    application.add_handler(CommandHandler("ads_off", ads_off))

    application.add_handler(CommandHandler("addchat", addchat))
    application.add_handler(CommandHandler("removechat", removechat))

    application.add_handler(
        CallbackQueryHandler(
            check_subscription_button,
            pattern="^check_subscription$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            broadcast_callback,
            pattern="^(send_users|send_chats|send_both|cancel_broadcast)$"
        )
    )

    application.add_handler(
        MessageHandler(filters.PHOTO, receive_photo)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_username
        )
    )

    print("Blox Shop 8.0 запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()
