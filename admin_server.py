import os
import io
import hmac
import json
import time
import hashlib
import mimetypes
import urllib.request
import urllib.parse
from functools import wraps

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7580041056"))

ALLOWED_CATEGORIES = ("blox", "brainrot", "help", "reviews")


def connect_db():
    if not DATABASE_URL:
        raise RuntimeError("Не задана переменная DATABASE_URL в Render")

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

        starter_products = [
            ("Lightning", "25 Robux", "⚡", "blox"),
            ("Shadow", "10 Robux", "🌑", "blox"),
            ("Mammoth", "15 Robux", "🐘", "blox"),
            ("Portal", "15 Robux", "🌀", "blox"),
        ]

        for name, price, emoji, category in starter_products:
            exists = db.execute("""
                SELECT id FROM products
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


def check_telegram_admin():
    bot_token = os.environ.get("BOT_TOKEN")
    init_data = request.headers.get("X-Telegram-Init-Data", "")

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

        if not hmac.compare_digest(calculated_hash, received_hash):
            return False

        auth_date = int(parsed.get("auth_date", "0"))
        now = time.time()

        if now - auth_date > 86400:
            return False

        if auth_date > now + 60:
            return False

        user = json.loads(parsed.get("user", "{}"))

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


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data"
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


@app.route("/api/products", methods=["GET"])
def get_products():
    db = connect_db()

    try:
        rows = db.execute(
            "SELECT * FROM products ORDER BY id DESC"
        ).fetchall()

        products = []

        for row in rows:
            product = dict(row)
            image_file_id = product.pop("image_file_id", None)

            if image_file_id:
                product["image_url"] = (
                    request.host_url.rstrip("/")
                    + f"/api/products/{product['id']}/image"
                )

            products.append(product)

        return jsonify(products)

    finally:
        db.close()


@app.route("/api/products/<int:product_id>/image", methods=["GET"])
def product_image(product_id):
    bot_token = os.environ.get("BOT_TOKEN")

    if not bot_token:
        return jsonify({"error": "BOT_TOKEN не настроен"}), 500

    db = connect_db()

    try:
        row = db.execute(
            "SELECT image_file_id FROM products WHERE id = %s",
            (product_id,)
        ).fetchone()
    finally:
        db.close()

    if not row or not row["image_file_id"]:
        return jsonify({"error": "Фотография не найдена"}), 404

    try:
        file_id = row["image_file_id"]

        get_file_url = (
            f"https://api.telegram.org/bot{bot_token}/getFile?"
            + urllib.parse.urlencode({"file_id": file_id})
        )

        with urllib.request.urlopen(
            get_file_url,
            timeout=20
        ) as response:
            file_data = json.loads(response.read().decode("utf-8"))

        if not file_data.get("ok"):
            return jsonify({"error": "Telegram не нашёл фотографию"}), 404

        file_path = file_data["result"]["file_path"]

        image_url = (
            f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        )

        with urllib.request.urlopen(
            image_url,
            timeout=30
        ) as response:
            image_bytes = response.read()
            content_type = response.headers.get("Content-Type")

        if not content_type or not content_type.startswith("image/"):
            content_type = (
                mimetypes.guess_type(file_path)[0] or "image/jpeg"
            )

        return send_file(
            io.BytesIO(image_bytes),
            mimetype=content_type,
            max_age=3600
        )

    except Exception as error:
        print("Ошибка получения фотографии:", error)

        return jsonify({
            "error": "Не удалось загрузить фотографию"
        }), 502


def validate_product_data(data):
    name = str(data.get("name", "")).strip()
    price = str(data.get("price", "")).strip()
    emoji = str(data.get("emoji", "🎁")).strip() or "🎁"
    category = str(data.get("category", "blox")).strip()
    description = str(data.get("description", "")).strip()

    image_file_id = data.get("image_file_id")

    if image_file_id is not None:
        image_file_id = str(image_file_id).strip() or None

    if not name:
        return None, "Укажи название"

    if not price and category != "reviews":
        return None, "Укажи цену"

    if not price:
        price = "Отзыв"

    if category not in ALLOWED_CATEGORIES:
        return None, "Неверная категория"

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
            (name, price, emoji, category, image_file_id, description)
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


@app.route("/api/products/<int:product_id>", methods=["PUT"])
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
            db.execute("""
                UPDATE products
                SET name = %s,
                    price = %s,
                    emoji = %s,
                    category = %s,
                    description = %s,
                    image_file_id = %s
                WHERE id = %s
            """, (
                product["name"],
                product["price"],
                product["emoji"],
                product["category"],
                product["description"],
                product["image_file_id"],
                product_id
            ))
        else:
            db.execute("""
                UPDATE products
                SET name = %s,
                    price = %s,
                    emoji = %s,
                    category = %s,
                    description = %s
                WHERE id = %s
            """, (
                product["name"],
                product["price"],
                product["emoji"],
                product["category"],
                product["description"],
                product_id
            ))

        db.commit()

        return jsonify({"success": True})

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
@admin_required
def delete_product(product_id):
    db = connect_db()

    try:
        db.execute(
            "DELETE FROM products WHERE id = %s",
            (product_id,)
        )

        db.commit()

        return jsonify({"success": True})

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )