
import os
import re
import io
import json
import hmac
import hashlib
import secrets
import urllib.request
import urllib.parse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta
from functools import wraps

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, send_file
from psycopg.types.json import Jsonb


app = Flask(__name__)

# --------------------------------------------------
# НАСТРОЙКИ
# --------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_SECRET = os.getenv("BOT_SECRET", "").strip()

ADMIN_ID = int(os.getenv("ADMIN_ID", "7580041056"))
MAX_QUANTITY = 99
MAX_INIT_DATA_AGE = 86400

ALLOWED_CATEGORIES = {
    "blox",
    "brainrot",
    "brainrot_ordinary",
    "brainrot_mutations",
    "brainrot_divine",
    "brainrot_lucky",
    "brainrot_rare",
    "accounts",
    "help",
    "reviews",
}


# --------------------------------------------------
# ОБЩИЕ ФУНКЦИИ
# --------------------------------------------------

def db():
    if not DATABASE_URL:
        raise RuntimeError("Не задан DATABASE_URL")
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=15,
    )


def json_error(message, status=400):
    return jsonify({"success": False, "error": str(message)}), status


def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "да")


def parse_quantity(value):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise ValueError("Некорректное количество")

    if quantity < 1 or quantity > MAX_QUANTITY:
        raise ValueError(f"Количество должно быть от 1 до {MAX_QUANTITY}")

    return quantity


def parse_price(value):
    """
    Извлекает числовую цену из строк вроде:
    25 Robux, 10,5 USD, 100.
    """
    if value is None:
        raise ValueError("У товара не указана цена")

    match = re.search(r"\d+(?:[.,]\d{1,2})?", str(value).replace(" ", ""))
    if not match:
        raise ValueError("Для промокода нужна числовая цена товара")

    try:
        price = Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        raise ValueError("Некорректная цена товара")

    if price <= 0:
        raise ValueError("Цена должна быть больше нуля")

    return price


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def clean_text(value, max_length=500):
    return str(value or "").strip()[:max_length]


# --------------------------------------------------
# БАЗА ДАННЫХ
# --------------------------------------------------

def init_db():
    """
    Безопасная инициализация.
    Существующие записи не удаляются.
    """

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    price TEXT NOT NULL DEFAULT '',
                    emoji TEXT DEFAULT '🎁',
                    category TEXT NOT NULL DEFAULT 'blox',
                    image_file_id TEXT,
                    description TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Добавляем недостающие поля, не удаляя старые.
            cur.execute("""
                ALTER TABLE products
                ADD COLUMN IF NOT EXISTS image_file_id TEXT
            """)
            cur.execute("""
                ALTER TABLE products
                ADD COLUMN IF NOT EXISTS description TEXT
            """)
            cur.execute("""
                ALTER TABLE products
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()
            """)
            cur.execute("""
                ALTER TABLE products
                ADD COLUMN IF NOT EXISTS emoji TEXT DEFAULT '🎁'
            """)
            cur.execute("""
                ALTER TABLE products
                ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'blox'
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS promocodes (
                    id SERIAL PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    discount_percent INTEGER NOT NULL DEFAULT 10,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    max_uses INTEGER,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                ALTER TABLE promocodes
                ADD COLUMN IF NOT EXISTS discount_percent INTEGER NOT NULL DEFAULT 10
            """)
            cur.execute("""
                ALTER TABLE promocodes
                ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE
            """)
            cur.execute("""
                ALTER TABLE promocodes
                ADD COLUMN IF NOT EXISTS max_uses INTEGER
            """)
            cur.execute("""
                ALTER TABLE promocodes
                ADD COLUMN IF NOT EXISTS used_count INTEGER NOT NULL DEFAULT 0
            """)
            cur.execute("""
                ALTER TABLE promocodes
                ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ
            """)
            cur.execute("""
                ALTER TABLE promocodes
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS promo_redemptions (
                    id SERIAL PRIMARY KEY,
                    order_id TEXT NOT NULL UNIQUE,
                    promo_id INTEGER,
                    code TEXT,
                    product_id INTEGER,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'reserved',
                    result JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    confirmed_at TIMESTAMPTZ
                )
            """)

            # Миграция старой таблицы промокодов.
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS code TEXT
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS promo_id INTEGER
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS product_id INTEGER
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 1
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'reserved'
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS result JSONB
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()
            """)
            cur.execute("""
                ALTER TABLE promo_redemptions
                ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                promo_redemptions_order_id_idx
                ON promo_redemptions(order_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS products_category_idx
                ON products(category)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS promocodes_code_idx
                ON promocodes(code)
            """)

        conn.commit()


# --------------------------------------------------
# АВТОРИЗАЦИЯ TELEGRAM MINI APP
# --------------------------------------------------

def verify_telegram_init_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        parsed = urllib.parse.parse_qs(init_data, strict_parsing=True)
        received_hash = parsed.pop("hash")[0]

        data = {
            key: values[0]
            for key, values in parsed.items()
        }

        auth_date = int(data.get("auth_date", "0"))
        now = int(datetime.now(timezone.utc).timestamp())

        if auth_date <= 0 or auth_date > now + 60:
            return None

        if now - auth_date > MAX_INIT_DATA_AGE:
            return None

        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        user_data = json.loads(data.get("user", "{}"))
        return user_data

    except Exception:
        return None


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        user = verify_telegram_init_data(init_data)

        if not user:
            return json_error("Не удалось проверить Telegram", 401)

        try:
            user_id = int(user.get("id", 0))
        except (TypeError, ValueError):
            return json_error("Некорректный пользователь", 401)

        if user_id != ADMIN_ID:
            return json_error("Нет доступа администратора", 403)

        return func(*args, **kwargs)

    return wrapper


def bot_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not BOT_SECRET:
            return json_error("На сервере не настроен BOT_SECRET", 500)

        received = request.headers.get("X-Bot-Token", "")

        if not received or not hmac.compare_digest(received, BOT_SECRET):
            return json_error("Нет доступа бота", 401)

        return func(*args, **kwargs)

    return wrapper


# --------------------------------------------------
# CORS
# --------------------------------------------------

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data, X-Bot-Token"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, PUT, DELETE, OPTIONS"
    )
    return response


@app.route("/<path:path>", methods=["OPTIONS"])
@app.route("/", methods=["OPTIONS"])
def options_handler(path=None):
    return "", 204


# --------------------------------------------------
# СЛУЖЕБНЫЕ МАРШРУТЫ
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "success": True,
        "name": "Legend Market API",
        "status": "online",
    })


@app.route("/health", methods=["GET"])
def health():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

        return jsonify({
            "success": True,
            "database": "connected",
        })

    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ТОВАРЫ
# --------------------------------------------------

def product_to_json(product):
    item = dict(product)
    item["image_url"] = (
        f"/api/products/{item['id']}/image"
        if item.get("image_file_id")
        else ""
    )
    return item


@app.route("/api/products", methods=["GET"])
def get_products():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, price, emoji, category,
                           image_file_id, description, created_at
                    FROM products
                    ORDER BY id DESC
                """)
                products = cur.fetchall()

        return jsonify([product_to_json(p) for p in products])

    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/products", methods=["POST"])
@admin_required
def create_product():
    data = request.get_json(silent=True) or {}

    name = clean_text(data.get("name"), 200)
    price = clean_text(data.get("price"), 100)
    emoji = clean_text(data.get("emoji"), 30) or "🎁"
    category = clean_text(data.get("category"), 50)
    description = clean_text(data.get("description"), 2000)
    image_file_id = clean_text(data.get("image_file_id"), 500)

    if not name or not price:
        return json_error("Заполни название и цену")

    if category not in ALLOWED_CATEGORIES:
        return json_error("Неизвестная категория")

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO products
                        (name, price, emoji, category,
                         image_file_id, description)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, (
                    name,
                    price,
                    emoji,
                    category,
                    image_file_id or None,
                    description,
                ))
                product = cur.fetchone()

        return jsonify({
            "success": True,
            "product": product_to_json(product),
        })

    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/products/<int:product_id>", methods=["PUT"])
@admin_required
def update_product(product_id):
    data = request.get_json(silent=True) or {}

    allowed = {
        "name": 200,
        "price": 100,
        "emoji": 30,
        "category": 50,
        "description": 2000,
        "image_file_id": 500,
    }

    updates = {}

    for field, max_length in allowed.items():
        if field in data:
            updates[field] = clean_text(data[field], max_length)

    if "category" in updates and updates["category"] not in ALLOWED_CATEGORIES:
        return json_error("Неизвестная категория")

    if not updates:
        return json_error("Нет данных для изменения")

    columns = list(updates.keys())
    set_clause = ", ".join(f"{column} = %s" for column in columns)
    values = list(updates.values())

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE products
                    SET {set_clause}
                    WHERE id = %s
                    RETURNING *
                    """,
                    values + [product_id],
                )
                product = cur.fetchone()

        if not product:
            return json_error("Товар не найден", 404)

        return jsonify({
            "success": True,
            "product": product_to_json(product),
        })

    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
@admin_required
def delete_product(product_id):
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM products WHERE id = %s RETURNING id",
                    (product_id,),
                )
                deleted = cur.fetchone()

        if not deleted:
            return json_error("Товар не найден", 404)

        return jsonify({"success": True})

    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ИЗОБРАЖЕНИЯ TELEGRAM
# --------------------------------------------------

@app.route("/api/products/<int:product_id>/image", methods=["GET"])
def get_product_image(product_id):
    if not BOT_TOKEN:
        return json_error("На сервере не настроен BOT_TOKEN", 500)

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT image_file_id FROM products WHERE id = %s",
                    (product_id,),
                )
                product = cur.fetchone()

        if not product or not product.get("image_file_id"):
            return json_error("Изображение не найдено", 404)

        file_id = product["image_file_id"]

        api_url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
            f"?file_id={urllib.parse.quote(file_id)}"
        )

        with urllib.request.urlopen(api_url, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            return json_error("Telegram не вернул файл", 502)

        file_path = result["result"]["file_path"]
        file_url = (
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        )

        with urllib.request.urlopen(file_url, timeout=20) as response:
            image_bytes = response.read()
            content_type = response.headers.get(
                "Content-Type",
                "application/octet-stream",
            )

        return send_file(
            io.BytesIO(image_bytes),
            mimetype=content_type,
            max_age=3600,
        )

    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ПРОМОКОДЫ: РАСЧЁТ СКИДКИ
# --------------------------------------------------

def calculate_discount(price, quantity, discount_percent):
    original_total = money(price * quantity)
    discounted_unit_price = money(
        price * (Decimal(100 - discount_percent) / Decimal(100))
    )
    discounted_total = money(discounted_unit_price * quantity)

    return {
        "original_unit_price": float(money(price)),
        "discounted_unit_price": float(discounted_unit_price),
        "original_total": float(original_total),
        "discounted_total": float(discounted_total),
        "discount_amount": float(money(original_total - discounted_total)),
        "discount_percent": discount_percent,
    }


def get_active_promo(cur, code, lock=False):
    sql = """
        SELECT *
        FROM promocodes
        WHERE UPPER(code) = UPPER(%s)
    """
    if lock:
        sql += " FOR UPDATE"

    cur.execute(sql, (code,))
    return cur.fetchone()


def promo_usage_count(cur, promo_id):
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM promo_redemptions
        WHERE promo_id = %s
          AND status IN ('reserved', 'redeemed')
    """, (promo_id,))
    row = cur.fetchone()
    return int(row["count"] or 0)


def validate_promo_in_transaction(cur, code, product_id, quantity):
    code = clean_text(code, 32).upper()

    if not code:
        raise ValueError("Введи промокод")

    quantity = parse_quantity(quantity)

    cur.execute(
        "SELECT * FROM products WHERE id = %s",
        (product_id,),
    )
    product = cur.fetchone()

    if not product:
        raise ValueError("Товар не найден")

    promo = get_active_promo(cur, code)

    if not promo:
        raise ValueError("Промокод не найден")

    if not parse_bool(promo.get("active")):
        raise ValueError("Промокод отключён")

    expires_at = promo.get("expires_at")
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("Срок действия промокода истёк")

    discount = int(promo.get("discount_percent") or 0)

    if discount < 1 or discount > 100:
        raise ValueError("Некорректная скидка промокода")

    max_uses = promo.get("max_uses")

    if max_uses is not None:
        used = promo_usage_count(cur, promo["id"])
        if used >= int(max_uses):
            raise ValueError("Лимит использований промокода исчерпан")

    price = parse_price(product["price"])
    totals = calculate_discount(price, quantity, discount)

    return {
        "promo": promo,
        "product": product,
        "quantity": quantity,
        "totals": totals,
    }


@app.route("/api/promocodes/validate", methods=["POST"])
def validate_promo():
    data = request.get_json(silent=True) or {}

    try:
        code = data.get("code", "")
        product_id = int(data.get("product_id"))
        quantity = parse_quantity(data.get("quantity", 1))

        with db() as conn:
            with conn.cursor() as cur:
                result = validate_promo_in_transaction(
                    cur,
                    code,
                    product_id,
                    quantity,
                )

        return jsonify({
            "success": True,
            "code": result["promo"]["code"],
            "product_id": result["product"]["id"],
            "quantity": quantity,
            **result["totals"],
        })

    except ValueError as exc:
        return json_error(str(exc))
    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ПРОМОКОДЫ: СОЗДАНИЕ И УПРАВЛЕНИЕ
# --------------------------------------------------

def promo_to_json(promo):
    item = dict(promo)
    return item


@app.route("/api/promocodes", methods=["GET"])
@admin_required
def get_promocodes():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT *
                    FROM promocodes
                    ORDER BY id DESC
                """)
                promos = cur.fetchall()

        return jsonify([promo_to_json(p) for p in promos])

    except Exception as exc:
        return json_error(str(exc), 500)


def parse_expiry(value):
    if not value:
        return None

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Неправильная дата окончания")

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed

    raise ValueError("Неправильная дата окончания")


@app.route("/api/promocodes", methods=["POST"])
@admin_required
def create_promocode():
    data = request.get_json(silent=True) or {}

    code = clean_text(data.get("code"), 32).upper()

    try:
        discount = int(data.get("discount_percent", 10))
        max_uses_value = data.get("max_uses")
        max_uses = (
            int(max_uses_value)
            if max_uses_value not in (None, "")
            else None
        )
        expires_at = parse_expiry(data.get("expires_at"))

        if not re.fullmatch(r"[A-Z0-9_-]{3,32}", code):
            return json_error(
                "Код должен содержать 3–32 символа: латинские буквы, цифры, _ или -"
            )

        if discount < 1 or discount > 100:
            return json_error("Скидка должна быть от 1 до 100 процентов")

        if max_uses is not None and max_uses < 1:
            return json_error("Лимит использований должен быть больше нуля")

        active = parse_bool(data.get("active"), True)

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO promocodes
                        (code, discount_percent, active, max_uses, expires_at)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                """, (code, discount, active, max_uses, expires_at))
                promo = cur.fetchone()

        return jsonify({
            "success": True,
            "promocode": promo_to_json(promo),
        })

    except ValueError as exc:
        return json_error(str(exc))
    except psycopg.errors.UniqueViolation:
        return json_error("Такой промокод уже существует")
    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/promocodes/<int:promo_id>", methods=["PUT"])
@admin_required
def update_promocode(promo_id):
    data = request.get_json(silent=True) or {}

    updates = {}

    try:
        if "code" in data:
            code = clean_text(data["code"], 32).upper()
            if not re.fullmatch(r"[A-Z0-9_-]{3,32}", code):
                return json_error("Некорректный формат промокода")
            updates["code"] = code

        if "discount_percent" in data:
            discount = int(data["discount_percent"])
            if discount < 1 or discount > 100:
                return json_error("Скидка должна быть от 1 до 100 процентов")
            updates["discount_percent"] = discount

        if "active" in data:
            updates["active"] = parse_bool(data["active"])

        if "max_uses" in data:
            value = data["max_uses"]
            max_uses = int(value) if value not in (None, "") else None
            if max_uses is not None and max_uses < 1:
                return json_error("Лимит должен быть больше нуля")
            updates["max_uses"] = max_uses

        if "expires_at" in data:
            updates["expires_at"] = parse_expiry(data["expires_at"])

        if not updates:
            return json_error("Нет данных для изменения")

        columns = list(updates.keys())
        set_clause = ", ".join(f"{column} = %s" for column in columns)

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE promocodes
                    SET {set_clause}
                    WHERE id = %s
                    RETURNING *
                    """,
                    list(updates.values()) + [promo_id],
                )
                promo = cur.fetchone()

        if not promo:
            return json_error("Промокод не найден", 404)

        return jsonify({
            "success": True,
            "promocode": promo_to_json(promo),
        })

    except ValueError as exc:
        return json_error(str(exc))
    except psycopg.errors.UniqueViolation:
        return json_error("Такой промокод уже существует")
    except Exception as exc:
        return json_error(str(exc), 500)


@app.route("/api/promocodes/<int:promo_id>", methods=["DELETE"])
@admin_required
def delete_promocode(promo_id):
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM promocodes WHERE id = %s RETURNING id",
                    (promo_id,),
                )
                deleted = cur.fetchone()

        if not deleted:
            return json_error("Промокод не найден", 404)

        return jsonify({"success": True})

    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ПРОМОКОДЫ: РЕЗЕРВИРОВАНИЕ ПРИ ЗАКАЗЕ
# --------------------------------------------------

@app.route("/api/promocodes/reserve", methods=["POST"])
@bot_required
def reserve_promo():
    data = request.get_json(silent=True) or {}

    order_id = clean_text(data.get("order_id"), 150)
    code = clean_text(data.get("code"), 32).upper()

    try:
        product_id = int(data.get("product_id"))
        quantity = parse_quantity(data.get("quantity", 1))

        if not order_id:
            return json_error("Не указан номер заказа")

        with db() as conn:
            with conn.cursor() as cur:

                # Повторный запрос с тем же заказом не создаёт вторую бронь.
                cur.execute("""
                    SELECT *
                    FROM promo_redemptions
                    WHERE order_id = %s
                    FOR UPDATE
                """, (order_id,))
                existing = cur.fetchone()

                if existing:
                    if (
                        str(existing.get("code") or "").upper() != code
                        or int(existing.get("product_id") or 0) != product_id
                        or int(existing.get("quantity") or 1) != quantity
                    ):
                        return json_error(
                            "Этот номер заказа уже использован с другими данными",
                            409,
                        )

                    if existing["status"] == "cancelled":
                        return json_error("Бронь этого заказа отменена", 409)

                    return jsonify({
                        "success": True,
                        "already_reserved": True,
                        "order_id": order_id,
                        "status": existing["status"],
                        "result": existing.get("result"),
                    })

                # Блокируем промокод, чтобы не превысить лимит.
                promo = get_active_promo(cur, code, lock=True)

                if not promo:
                    return json_error("Промокод не найден")

                result = validate_promo_in_transaction(
                    cur,
                    code,
                    product_id,
                    quantity,
                )

                totals = result["totals"]

                cur.execute("""
                    INSERT INTO promo_redemptions
                        (order_id, promo_id, code, product_id,
                         quantity, status, result, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'reserved', %s, NOW(), NOW())
                """, (
                    order_id,
                    promo["id"],
                    code,
                    product_id,
                    quantity,
                    Jsonb(totals),
                ))

        return jsonify({
            "success": True,
            "order_id": order_id,
            "status": "reserved",
            **totals,
        })

    except ValueError as exc:
        return json_error(str(exc))
    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ПРОМОКОДЫ: ПОДТВЕРЖДЕНИЕ ЗАКАЗА
# --------------------------------------------------

@app.route("/api/promocodes/confirm", methods=["POST"])
@bot_required
def confirm_promo():
    data = request.get_json(silent=True) or {}
    order_id = clean_text(data.get("order_id"), 150)

    if not order_id:
        return json_error("Не указан номер заказа")

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT *
                    FROM promo_redemptions
                    WHERE order_id = %s
                    FOR UPDATE
                """, (order_id,))
                redemption = cur.fetchone()

                # Обычный заказ без промокода тоже можно подтвердить.
                if not redemption:
                    return jsonify({
                        "success": True,
                        "no_promo": True,
                        "order_id": order_id,
                    })

                if redemption["status"] == "redeemed":
                    return jsonify({
                        "success": True,
                        "already_confirmed": True,
                        "order_id": order_id,
                    })

                if redemption["status"] == "cancelled":
                    return json_error("Заказ уже отменён", 409)

                cur.execute("""
                    UPDATE promo_redemptions
                    SET status = 'redeemed',
                        confirmed_at = NOW(),
                        updated_at = NOW()
                    WHERE order_id = %s
                    RETURNING *
                """, (order_id,))

                updated = cur.fetchone()

                if redemption.get("promo_id"):
                    cur.execute("""
                        UPDATE promocodes
                        SET used_count = used_count + 1
                        WHERE id = %s
                    """, (redemption["promo_id"],))

        return jsonify({
            "success": True,
            "order_id": order_id,
            "status": "redeemed",
        })

    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ПРОМОКОДЫ: ОТМЕНА ЗАКАЗА
# --------------------------------------------------

@app.route("/api/promocodes/cancel", methods=["POST"])
@bot_required
def cancel_promo():
    data = request.get_json(silent=True) or {}
    order_id = clean_text(data.get("order_id"), 150)

    if not order_id:
        return json_error("Не указан номер заказа")

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT *
                    FROM promo_redemptions
                    WHERE order_id = %s
                    FOR UPDATE
                """, (order_id,))
                redemption = cur.fetchone()

                # Обычный заказ без промокода можно отменить без ошибки.
                if not redemption:
                    return jsonify({
                        "success": True,
                        "no_promo": True,
                        "order_id": order_id,
                    })

                if redemption["status"] == "redeemed":
                    return json_error(
                        "Уже подтверждённый промокод нельзя отменить",
                        409,
                    )

                if redemption["status"] == "cancelled":
                    return jsonify({
                        "success": True,
                        "already_cancelled": True,
                        "order_id": order_id,
                    })

                cur.execute("""
                    UPDATE promo_redemptions
                    SET status = 'cancelled',
                        updated_at = NOW()
                    WHERE order_id = %s
                """, (order_id,))

        return jsonify({
            "success": True,
            "order_id": order_id,
            "status": "cancelled",
        })

    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ИСТОРИЯ ИСПОЛЬЗОВАНИЯ ПРОМОКОДА
# --------------------------------------------------

@app.route("/api/promocodes/redeem", methods=["GET"])
@bot_required
def get_redemption():
    order_id = clean_text(request.args.get("order_id"), 150)

    if not order_id:
        return json_error("Не указан номер заказа")

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT *
                    FROM promo_redemptions
                    WHERE order_id = %s
                """, (order_id,))
                redemption = cur.fetchone()

        if not redemption:
            return jsonify({
                "success": True,
                "found": False,
            })

        return jsonify({
            "success": True,
            "found": True,
            "redemption": redemption,
        })

    except Exception as exc:
        return json_error(str(exc), 500)


# --------------------------------------------------
# ЗАПУСК
# --------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
