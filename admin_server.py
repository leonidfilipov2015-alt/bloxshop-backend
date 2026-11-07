
import os
import io
import re
import hmac
import json
import time
import hashlib
import mimetypes
import urllib.request
import urllib.parse

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import wraps

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, send_file


app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7580041056"))

UKRAINE_TZ = ZoneInfo("Europe/Kyiv")

ALLOWED_CATEGORIES = (
    "blox",
    "brainrot",
    "brainrot_ordinary",
    "brainrot_mutations",
    "brainrot_divine",
    "brainrot_lucky",
    "brainrot_rare",
    "help",
    "reviews",
    "accounts"
)

MAX_QUANTITY = 99


# -------------------------
# БАЗА ДАННЫХ
# -------------------------

def connect_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "Не задана переменная DATABASE_URL в Render"
        )

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10
    )


def init_db():
    db = connect_db()

    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                price TEXT NOT NULL,
                emoji TEXT,
                category TEXT,
                image_file_id TEXT,
                description TEXT
            )
        """)

        db.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS image_file_id TEXT
        """)

        db.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS description TEXT
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                id BIGSERIAL PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                discount_percent INTEGER NOT NULL,
                max_uses INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0,
                expires_at TIMESTAMPTZ,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Учёт резервирования и подтверждения промокодов.
        # Один order_id может быть использован только один раз.
        db.execute("""
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                id BIGSERIAL PRIMARY KEY,
                order_id TEXT NOT NULL UNIQUE,
                promo_id BIGINT NOT NULL
                    REFERENCES promocodes(id)
                    ON DELETE CASCADE,
                product_id BIGINT NOT NULL,
                quantity INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                result JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                confirmed_at TIMESTAMPTZ,
                CONSTRAINT promo_redemptions_status_check
                    CHECK (status IN ('reserved', 'redeemed', 'cancelled'))
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_promo_redemptions_promo_status
            ON promo_redemptions(promo_id, status)
        """)

        starter_products = [
            ("Lightning", "25 Robux", "⚡", "blox"),
            ("Shadow", "10 Robux", "🌑", "blox"),
            ("Mammoth", "15 Robux", "🐘", "blox"),
            ("Portal", "15 Robux", "🌀", "blox"),
        ]

        for name, price, emoji, category in starter_products:
            exists = db.execute("""
                SELECT id
                FROM products
                WHERE lower(name) = lower(%s)
                  AND category = %s
                LIMIT 1
            """, (name, category)).fetchone()

            if not exists:
                db.execute("""
                    INSERT INTO products
                    (name, price, emoji, category)
                    VALUES (%s, %s, %s, %s)
                """, (name, price, emoji, category))

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# -------------------------
# АВТОРИЗАЦИЯ
# -------------------------

def check_telegram_admin():
    bot_token = os.environ.get("BOT_TOKEN")
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    if not bot_token or not init_data:
        return False

    try:
        parsed = dict(
            urllib.parse.parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return False

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return False

        auth_date = int(parsed.get("auth_date", "0"))
        now = time.time()

        if now - auth_date > 86400:
            return False

        if auth_date > now + 60:
            return False

        user = json.loads(
            parsed.get("user", "{}")
        )

        return int(user.get("id", 0)) == ADMIN_ID

    except Exception:
        return False


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not check_telegram_admin():
            return jsonify({
                "success": False,
                "error": "Нет доступа администратора"
            }), 403

        return function(*args, **kwargs)

    return wrapper


def bot_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        token = os.environ.get("BOT_TOKEN", "")
        received = request.headers.get("X-Bot-Token", "")

        if not token or not hmac.compare_digest(
            token,
            received
        ):
            return jsonify({
                "success": False,
                "error": "Нет доступа бота"
            }), 403

        return function(*args, **kwargs)

    return wrapper


# -------------------------
# CORS
# -------------------------

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


@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        return "", 204


@app.route("/")
def home():
    return "Blox Shop backend работает с Neon!"


# -------------------------
# ТОВАРЫ
# -------------------------

@app.route("/api/products", methods=["GET"])
def get_products():
    db = connect_db()

    try:
        rows = db.execute("""
            SELECT *
            FROM products
            ORDER BY id DESC
        """).fetchall()

        products = []

        for row in rows:
            product = dict(row)
            image_file_id = product.pop(
                "image_file_id",
                None
            )

            if image_file_id:
                product["image_url"] = (
                    request.host_url.rstrip("/")
                    + f"/api/products/{product['id']}/image"
                )

            products.append(product)

        return jsonify(products)

    finally:
        db.close()


@app.route(
    "/api/products/<int:product_id>/image",
    methods=["GET"]
)
def product_image(product_id):
    bot_token = os.environ.get("BOT_TOKEN")

    if not bot_token:
        return jsonify({
            "error": "BOT_TOKEN не настроен"
        }), 500

    db = connect_db()

    try:
        row = db.execute("""
            SELECT image_file_id
            FROM products
            WHERE id = %s
        """, (product_id,)).fetchone()

    finally:
        db.close()

    if not row or not row["image_file_id"]:
        return jsonify({
            "error": "Фотография не найдена"
        }), 404

    try:
        file_id = row["image_file_id"]

        get_file_url = (
            f"https://api.telegram.org/bot{bot_token}/getFile?"
            + urllib.parse.urlencode({
                "file_id": file_id
            })
        )

        with urllib.request.urlopen(
            get_file_url,
            timeout=20
        ) as response:
            file_data = json.loads(
                response.read().decode("utf-8")
            )

        if not file_data.get("ok"):
            return jsonify({
                "error": "Telegram не нашёл фотографию"
            }), 404

        file_path = file_data["result"]["file_path"]

        image_url = (
            f"https://api.telegram.org/file/"
            f"bot{bot_token}/{file_path}"
        )

        with urllib.request.urlopen(
            image_url,
            timeout=30
        ) as response:
            image_bytes = response.read()
            content_type = response.headers.get(
                "Content-Type"
            )

        if (
            not content_type
            or not content_type.startswith("image/")
        ):
            content_type = (
                mimetypes.guess_type(file_path)[0]
                or "image/jpeg"
            )

        return send_file(
            io.BytesIO(image_bytes),
            mimetype=content_type,
            max_age=3600
        )

    except Exception as error:
        print(
            "Ошибка получения фотографии:",
            error
        )

        return jsonify({
            "error": "Не удалось загрузить фотографию"
        }), 502


def validate_product_data(data):
    name = str(
        data.get("name", "")
    ).strip()

    price = str(
        data.get("price", "")
    ).strip()

    emoji = str(
        data.get("emoji", "🎁")
    ).strip() or "🎁"

    category = str(
        data.get("category", "blox")
    ).strip()

    description = str(
        data.get("description", "")
    ).strip()

    image_file_id = data.get("image_file_id")

    if image_file_id is not None:
        image_file_id = (
            str(image_file_id).strip() or None
        )

    if not name:
        return None, "Укажи название"

    if not price and category != "reviews":
        return None, "Укажи цену"

    if not price:
        price = "Отзыв"

    if category not in ALLOWED_CATEGORIES:
        return None, "Неверная категория"

    if len(name) > 120:
        return None, "Название слишком длинное"

    if len(price) > 80:
        return None, "Цена слишком длинная"

    if len(description) > 2000:
        return None, "Описание — максимум 2000 символов"

    return {
        "name": name,
        "price": price,
        "emoji": emoji,
        "category": category,
        "description": description,
        "image_file_id": image_file_id
    }, None


@app.route("/api/products", methods=["POST"])
@admin_required
def add_product():
    data = request.get_json(silent=True) or {}

    product, error = validate_product_data(data)

    if error:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    db = connect_db()

    try:
        row = db.execute("""
            INSERT INTO products
            (
                name,
                price,
                emoji,
                category,
                image_file_id,
                description
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            product["name"],
            product["price"],
            product["emoji"],
            product["category"],
            product["image_file_id"],
            product["description"]
        )).fetchone()

        db.commit()

        return jsonify({
            "success": True,
            "id": row["id"]
        })

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@app.route(
    "/api/products/<int:product_id>",
    methods=["PUT"]
)
@admin_required
def update_product(product_id):
    data = request.get_json(silent=True) or {}

    product, error = validate_product_data(data)

    if error:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    db = connect_db()

    try:
        if "image_file_id" in data:
            row = db.execute("""
                UPDATE products
                SET name = %s,
                    price = %s,
                    emoji = %s,
                    category = %s,
                    description = %s,
                    image_file_id = %s
                WHERE id = %s
                RETURNING id
            """, (
                product["name"],
                product["price"],
                product["emoji"],
                product["category"],
                product["description"],
                product["image_file_id"],
                product_id
            )).fetchone()

        else:
            row = db.execute("""
                UPDATE products
                SET name = %s,
                    price = %s,
                    emoji = %s,
                    category = %s,
                    description = %s
                WHERE id = %s
                RETURNING id
            """, (
                product["name"],
                product["price"],
                product["emoji"],
                product["category"],
                product["description"],
                product_id
            )).fetchone()

        db.commit()

        if not row:
            return jsonify({
                "success": False,
                "error": "Товар не найден"
            }), 404

        return jsonify({
            "success": True
        })

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@app.route(
    "/api/products/<int:product_id>",
    methods=["DELETE"]
)
@admin_required
def delete_product(product_id):
    db = connect_db()

    try:
        row = db.execute("""
            DELETE FROM products
            WHERE id = %s
            RETURNING id
        """, (product_id,)).fetchone()

        db.commit()

        if not row:
            return jsonify({
                "success": False,
                "error": "Товар не найден"
            }), 404

        return jsonify({
            "success": True
        })

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# -------------------------
# ПРОМОКОДЫ: ОБЩИЕ ФУНКЦИИ
# -------------------------

def normalize_code(value):
    return re.sub(
        r"\s+",
        "",
        str(value or "")
    ).upper()


def promo_to_dict(row):
    promo = dict(row)

    if promo.get("expires_at"):
        promo["expires_at"] = (
            promo["expires_at"].isoformat()
        )

    if promo.get("created_at"):
        promo["created_at"] = (
            promo["created_at"].isoformat()
        )

    return promo


def validate_promo_data(data):
    code = normalize_code(
        data.get("code")
    )

    try:
        discount = int(
            data.get("discount_percent")
        )

    except (TypeError, ValueError):
        return None, "Скидка должна быть числом"

    if not re.fullmatch(
        r"[A-Z0-9_-]{3,32}",
        code
    ):
        return None, (
            "Код: от 3 до 32 символов, "
            "только латиница и цифры"
        )

    if not 1 <= discount <= 100:
        return None, "Скидка должна быть от 1 до 100%"

    max_uses = data.get("max_uses")

    if max_uses in ("", None):
        max_uses = None

    else:
        try:
            max_uses = int(max_uses)

            if max_uses < 1:
                return None, (
                    "Лимит использований должен быть больше 0"
                )

        except (TypeError, ValueError):
            return None, "Неверный лимит использований"

    expires_at = data.get("expires_at") or None

    if expires_at:
        try:
            expires_at = datetime.fromisoformat(
                str(expires_at).replace("Z", "+00:00")
            )

            # datetime-local из браузера приходит без часового пояса.
            # Считаем такое время временем Украины.
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(
                    tzinfo=UKRAINE_TZ
                )

            # В PostgreSQL сохраняем абсолютное время UTC.
            expires_at = expires_at.astimezone(
                timezone.utc
            )

        except ValueError:
            return None, "Неверная дата окончания"

    active = data.get("active", True)

    if not isinstance(active, bool):
        active = str(active).lower() in (
            "true",
            "1",
            "yes"
        )

    return {
        "code": code,
        "discount_percent": discount,
        "max_uses": max_uses,
        "expires_at": expires_at,
        "active": active
    }, None


@app.route("/api/promocodes", methods=["GET"])
@admin_required
def get_promocodes():
    db = connect_db()

    try:
        rows = db.execute("""
            SELECT *
            FROM promocodes
            ORDER BY id DESC
        """).fetchall()

        return jsonify([
            promo_to_dict(row)
            for row in rows
        ])

    finally:
        db.close()


@app.route("/api/promocodes", methods=["POST"])
@admin_required
def add_promocode():
    data = request.get_json(silent=True) or {}

    promo, error = validate_promo_data(data)

    if error:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    db = connect_db()

    try:
        row = db.execute("""
            INSERT INTO promocodes
            (
                code,
                discount_percent,
                max_uses,
                expires_at,
                active
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            promo["code"],
            promo["discount_percent"],
            promo["max_uses"],
            promo["expires_at"],
            promo["active"]
        )).fetchone()

        db.commit()

        return jsonify({
            "success": True,
            "id": row["id"]
        })

    except psycopg.errors.UniqueViolation:
        db.rollback()

        return jsonify({
            "success": False,
            "error": "Такой промокод уже существует"
        }), 409

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@app.route(
    "/api/promocodes/<int:promo_id>",
    methods=["PUT"]
)
@admin_required
def update_promocode(promo_id):
    data = request.get_json(silent=True) or {}

    promo, error = validate_promo_data(data)

    if error:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    db = connect_db()

    try:
        row = db.execute("""
            UPDATE promocodes
            SET code = %s,
                discount_percent = %s,
                max_uses = %s,
                expires_at = %s,
                active = %s
            WHERE id = %s
            RETURNING id
        """, (
            promo["code"],
            promo["discount_percent"],
            promo["max_uses"],
            promo["expires_at"],
            promo["active"],
            promo_id
        )).fetchone()

        db.commit()

        if not row:
            return jsonify({
                "success": False,
                "error": "Промокод не найден"
            }), 404

        return jsonify({
            "success": True
        })

    except psycopg.errors.UniqueViolation:
        db.rollback()

        return jsonify({
            "success": False,
            "error": "Такой промокод уже существует"
        }), 409

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@app.route(
    "/api/promocodes/<int:promo_id>",
    methods=["DELETE"]
)
@admin_required
def delete_promocode(promo_id):
    db = connect_db()

    try:
        row = db.execute("""
            DELETE FROM promocodes
            WHERE id = %s
            RETURNING id
        """, (promo_id,)).fetchone()

        db.commit()

        if not row:
            return jsonify({
                "success": False,
                "error": "Промокод не найден"
            }), 404

        return jsonify({
            "success": True
        })

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# -------------------------
# РАСЧЁТ ЦЕНЫ И КОЛИЧЕСТВА
# -------------------------

def parse_quantity(data, category):
    if category == "accounts":
        return 1, None

    raw_quantity = data.get(
        "quantity",
        1
    )

    try:
        quantity = int(raw_quantity)

    except (TypeError, ValueError):
        return None, (
            "Количество должно быть целым числом"
        )

    if quantity < 1 or quantity > MAX_QUANTITY:
        return None, (
            f"Количество должно быть от 1 до {MAX_QUANTITY}"
        )

    return quantity, None


def format_decimal(value):
    if value == value.to_integral_value():
        return str(int(value))

    return format(
        value.normalize(),
        "f"
    )


def calculate_discount(
    price,
    discount_percent,
    quantity=1
):
    price = str(price or "").strip()

    match = re.match(
        r"^\s*(\d+(?:[.,]\d{1,2})?)\s*(.*?)\s*$",
        price
    )

    if not match:
        return None

    try:
        original = Decimal(
            match.group(1).replace(",", ".")
        )

    except InvalidOperation:
        return None

    suffix = match.group(2)
    quantity = int(quantity)

    discounted = (
        original
        * (
            Decimal(100 - discount_percent)
            / Decimal(100)
        )
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    original_total = original * quantity
    discounted_total = discounted * quantity

    discount_total = (
        original_total - discounted_total
    )

    original_text = format_decimal(original)
    discounted_text = format_decimal(discounted)

    return {
        "original_price": (
            f"{original_text} {suffix}".strip()
        ),
        "discounted_price": (
            f"{discounted_text} {suffix}".strip()
        ),
        "original_total": (
            f"{format_decimal(original_total)} {suffix}".strip()
        ),
        "discounted_total": (
            f"{format_decimal(discounted_total)} {suffix}".strip()
        ),
        "discount_amount": format_decimal(
            original - discounted
        ),
        "discount_total": format_decimal(
            discount_total
        ),
        "discount_percent": discount_percent,
        "quantity": quantity
    }


def get_valid_promo(db, code):
    row = db.execute("""
        SELECT *
        FROM promocodes
        WHERE code = %s
        FOR UPDATE
    """, (
        normalize_code(code),
    )).fetchone()

    if not row:
        return None, "Промокод не найден"

    if not row["active"]:
        return None, "Промокод отключён"

    if (
        row["expires_at"]
        and row["expires_at"] <= datetime.now(timezone.utc)
    ):
        return None, "Срок действия промокода истёк"

    if row["max_uses"] is not None:
        if row["used_count"] >= row["max_uses"]:
            return None, (
                "Промокод больше нельзя использовать"
            )

    return row, None


def get_product_and_quantity(
    db,
    product_id,
    data
):
    product = db.execute("""
        SELECT id, name, price, category
        FROM products
        WHERE id = %s
    """, (
        product_id,
    )).fetchone()

    if not product:
        return None, None, "Товар не найден"

    quantity, error = parse_quantity(
        data,
        product["category"]
    )

    if error:
        return None, None, error

    return product, quantity, None


def validate_promo_for_product(
    db,
    code,
    product_id,
    data
):
    product, quantity, error = get_product_and_quantity(
        db,
        product_id,
        data
    )

    if error:
        return None, error

    promo, error = get_valid_promo(
        db,
        code
    )

    if error:
        return None, error

    result = calculate_discount(
        product["price"],
        promo["discount_percent"],
        quantity
    )

    if result is None:
        return None, (
            "На этот товар скидка не распространяется"
        )

    response = {
        "success": True,
        "code": promo["code"],
        "product_id": product["id"],
        "product_name": product["name"],
        "category": product["category"],
        **result
    }

    return {
        "promo": promo,
        "product": product,
        "quantity": quantity,
        "response": response
    }, None


# -------------------------
# ПРОВЕРКА ПРОМОКОДА В МАГАЗИНЕ
# -------------------------

@app.route(
    "/api/promocodes/validate",
    methods=["POST"]
)
def validate_promocode():
    data = request.get_json(silent=True) or {}

    code = normalize_code(
        data.get("code")
    )

    if not code:
        return jsonify({
            "success": False,
            "error": "Не указан промокод"
        }), 400

    try:
        product_id = int(
            data.get("product_id")
        )

    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "Не указан товар"
        }), 400

    db = connect_db()

    try:
        promo = db.execute("""
            SELECT *
            FROM promocodes
            WHERE code = %s
        """, (
            code,
        )).fetchone()

        if not promo:
            return jsonify({
                "success": False,
                "error": "Промокод не найден"
            }), 404

        if not promo["active"]:
            return jsonify({
                "success": False,
                "error": "Промокод отключён"
            }), 400

        if (
            promo["expires_at"]
            and promo["expires_at"] <= datetime.now(timezone.utc)
        ):
            return jsonify({
                "success": False,
                "error": "Срок действия промокода истёк"
            }), 400

        if promo["max_uses"] is not None:
            reserved = db.execute("""
                SELECT COUNT(*) AS total
                FROM promo_redemptions
                WHERE promo_id = %s
                  AND status = 'reserved'
            """, (
                promo["id"],
            )).fetchone()["total"]

            if (
                promo["used_count"] + reserved
                >= promo["max_uses"]
            ):
                return jsonify({
                    "success": False,
                    "error": "Лимит использований исчерпан"
                }), 400

        product, quantity, error = get_product_and_quantity(
            db,
            product_id,
            data
        )

        if error:
            return jsonify({
                "success": False,
                "error": error
            }), 400 if error != "Товар не найден" else 404

        result = calculate_discount(
            product["price"],
            promo["discount_percent"],
            quantity
        )

        if result is None:
            return jsonify({
                "success": False,
                "error": (
                    "На этот товар скидка не распространяется"
                )
            }), 400

        return jsonify({
            "success": True,
            "code": promo["code"],
            "product_id": product["id"],
            "product_name": product["name"],
            "category": product["category"],
            **result
        })

    finally:
        db.close()


# -------------------------
# РЕЗЕРВИРОВАНИЕ ПРОМОКОДА
# -------------------------

@app.route(
    "/api/promocodes/reserve",
    methods=["POST"]
)
@bot_required
def reserve_promocode():
    data = request.get_json(silent=True) or {}

    order_id = str(
        data.get("order_id", "")
    ).strip()

    code = normalize_code(
        data.get("code")
    )

    if not order_id or len(order_id) > 128:
        return jsonify({
            "success": False,
            "error": "Неверный номер заказа"
        }), 400

    if not code:
        return jsonify({
            "success": False,
            "error": "Не указан промокод"
        }), 400

    try:
        product_id = int(
            data.get("product_id")
        )

    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "Не указан товар"
        }), 400

    db = connect_db()

    try:
        # Повторный запрос с тем же order_id не создаст
        # второе использование промокода.
        existing = db.execute("""
            SELECT *
            FROM promo_redemptions
            WHERE order_id = %s
            FOR UPDATE
        """, (
            order_id,
        )).fetchone()

        if existing:
            existing_result = dict(
                existing["result"]
            )

            if (
                existing["product_id"] != product_id
                or existing_result.get("code") != code
                or existing["quantity"] != int(
                    data.get("quantity", 1)
                )
            ):
                db.rollback()

                return jsonify({
                    "success": False,
                    "error": (
                        "Этот номер заказа уже использован "
                        "с другими данными"
                    )
                }), 409

            if existing["status"] == "cancelled":
                db.rollback()

                return jsonify({
                    "success": False,
                    "error": "Резервирование этого заказа отменено"
                }), 409

            db.commit()

            return jsonify({
                **existing_result,
                "status": existing["status"],
                "already_reserved": True
            })

        # Блокируем строку промокода, чтобы два заказа
        # одновременно не заняли последнее использование.
        promo = db.execute("""
            SELECT *
            FROM promocodes
            WHERE code = %s
            FOR UPDATE
        """, (
            code,
        )).fetchone()

        if not promo:
            db.rollback()

            return jsonify({
                "success": False,
                "error": "Промокод не найден"
            }), 404

        if not promo["active"]:
            db.rollback()

            return jsonify({
                "success": False,
                "error": "Промокод отключён"
            }), 400

        if (
            promo["expires_at"]
            and promo["expires_at"] <= datetime.now(timezone.utc)
        ):
            db.rollback()

            return jsonify({
                "success": False,
                "error": "Срок действия промокода истёк"
            }), 400

        product, quantity, error = get_product_and_quantity(
            db,
            product_id,
            data
        )

        if error:
            db.rollback()

            return jsonify({
                "success": False,
                "error": error
            }), 400 if error != "Товар не найден" else 404

        if promo["max_uses"] is not None:
            reserved = db.execute("""
                SELECT COUNT(*) AS total
                FROM promo_redemptions
                WHERE promo_id = %s
                  AND status = 'reserved'
            """, (
                promo["id"],
            )).fetchone()["total"]

            if (
                promo["used_count"] + reserved
                >= promo["max_uses"]
            ):
                db.rollback()

                return jsonify({
                    "success": False,
                    "error": "Лимит использований исчерпан"
                }), 400

        result = calculate_discount(
            product["price"],
            promo["discount_percent"],
            quantity
        )

        if result is None:
            db.rollback()

            return jsonify({
                "success": False,
                "error": (
                    "На этот товар скидка не распространяется"
                )
            }), 400

        response = {
            "success": True,
            "code": promo["code"],
            "product_id": product["id"],
            "product_name": product["name"],
            "category": product["category"],
            **result
        }

        db.execute("""
            INSERT INTO promo_redemptions
            (
                order_id,
                promo_id,
                product_id,
                quantity,
                status,
                result
            )
            VALUES (%s, %s, %s, %s, 'reserved', %s)
        """, (
            order_id,
            promo["id"],
            product["id"],
            quantity,
            json.dumps(response)
        ))

        db.commit()

        return jsonify({
            **response,
            "status": "reserved"
        })

    except psycopg.errors.UniqueViolation:
        db.rollback()

        return jsonify({
            "success": False,
            "error": "Этот заказ уже обрабатывается"
        }), 409

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# -------------------------
# ПОДТВЕРЖДЕНИЕ ПРОМОКОДА
# -------------------------

@app.route(
    "/api/promocodes/confirm",
    methods=["POST"]
)
@bot_required
def confirm_promocode():
    data = request.get_json(silent=True) or {}

    order_id = str(
        data.get("order_id", "")
    ).strip()

    if not order_id:
        return jsonify({
            "success": False,
            "error": "Не указан номер заказа"
        }), 400

    db = connect_db()

    try:
        redemption = db.execute("""
            SELECT *
            FROM promo_redemptions
            WHERE order_id = %s
            FOR UPDATE
        """, (
            order_id,
        )).fetchone()

        if not redemption:
            return jsonify({
                "success": False,
                "error": "Резервирование не найдено"
            }), 404

        if redemption["status"] == "redeemed":
            db.commit()

            return jsonify({
                "success": True,
                "already_confirmed": True,
                "order_id": order_id
            })

        if redemption["status"] != "reserved":
            db.rollback()

            return jsonify({
                "success": False,
                "error": "Резервирование уже отменено"
            }), 409

        db.execute("""
            UPDATE promo_redemptions
            SET status = 'redeemed',
                confirmed_at = NOW()
            WHERE order_id = %s
        """, (
            order_id,
        ))

        db.execute("""
            UPDATE promocodes
            SET used_count = used_count + 1
            WHERE id = %s
        """, (
            redemption["promo_id"],
        ))

        db.commit()

        return jsonify({
            "success": True,
            "order_id": order_id
        })

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# -------------------------
# ОТМЕНА РЕЗЕРВИРОВАНИЯ
# -------------------------

@app.route(
    "/api/promocodes/cancel",
    methods=["POST"]
)
@bot_required
def cancel_promocode():
    data = request.get_json(silent=True) or {}

    order_id = str(
        data.get("order_id", "")
    ).strip()

    if not order_id:
        return jsonify({
            "success": False,
            "error": "Не указан номер заказа"
        }), 400

    db = connect_db()

    try:
        redemption = db.execute("""
            SELECT *
            FROM promo_redemptions
            WHERE order_id = %s
            FOR UPDATE
        """, (
            order_id,
        )).fetchone()

        if not redemption:
            return jsonify({
                "success": True,
                "already_cancelled": True
            })

        if redemption["status"] == "redeemed":
            db.rollback()

            return jsonify({
                "success": False,
                "error": (
                    "Подтверждённое использование "
                    "нельзя отменить"
                )
            }), 409

        if redemption["status"] == "reserved":
            db.execute("""
                UPDATE promo_redemptions
                SET status = 'cancelled'
                WHERE order_id = %s
            """, (
                order_id,
            ))

        db.commit()

        return jsonify({
            "success": True,
            "order_id": order_id
        })

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# -------------------------
# СТАРЫЙ МЕТОД REDEEM
# ОСТАВЛЕН ДЛЯ СОВМЕСТИМОСТИ
# -------------------------

@app.route(
    "/api/promocodes/redeem",
    methods=["POST"]
)
@bot_required
def redeem_promocode():
    data = request.get_json(silent=True) or {}

    try:
        product_id = int(
            data.get("product_id")
        )

    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "Не указан товар"
        }), 400

    code = normalize_code(
        data.get("code")
    )

    if not code:
        return jsonify({
            "success": False,
            "error": "Не указан промокод"
        }), 400

    db = connect_db()

    try:
        product, quantity, error = get_product_and_quantity(
            db,
            product_id,
            data
        )

        if error:
            db.rollback()

            return jsonify({
                "success": False,
                "error": error
            }), 400 if error != "Товар не найден" else 404

        promo, error = get_valid_promo(
            db,
            code
        )

        if error:
            db.rollback()

            return jsonify({
                "success": False,
                "error": error
            }), 400

        if promo["max_uses"] is not None:
            reserved = db.execute("""
                SELECT COUNT(*) AS total
                FROM promo_redemptions
                WHERE promo_id = %s
                  AND status = 'reserved'
            """, (
                promo["id"],
            )).fetchone()["total"]

            if (
                promo["used_count"] + reserved
                >= promo["max_uses"]
            ):
                db.rollback()

                return jsonify({
                    "success": False,
                    "error": "Лимит использований исчерпан"
                }), 400

        result = calculate_discount(
            product["price"],
            promo["discount_percent"],
            quantity
        )

        if result is None:
            db.rollback()

            return jsonify({
                "success": False,
                "error": (
                    "На этот товар скидка не распространяется"
                )
            }), 400

        db.execute("""
            UPDATE promocodes
            SET used_count = used_count + 1
            WHERE id = %s
        """, (
            promo["id"],
        ))

        db.commit()

        return jsonify({
            "success": True,
            "code": promo["code"],
            "product_id": product["id"],
            "product_name": product["name"],
            "category": product["category"],
            **result
        })

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# -------------------------
# ЗАПУСК
# -------------------------

init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )