
import os
import json
import hmac
import hashlib
import sqlite3
import time
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 7580041056

DATABASE = "bloxshop.db"
ALLOWED_ORIGIN = "https://leonidfilipov2015-alt.github.io"


def init_db():
    with sqlite3.connect(DATABASE) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price TEXT NOT NULL,
                emoji TEXT DEFAULT '🎁',
                category TEXT NOT NULL
            )
        """)


def check_telegram_user(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        data = dict(parse_qsl(init_data))
        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        auth_date = int(data.get("auth_date", 0))

        if time.time() - auth_date > 86400:
            return None

        return json.loads(data["user"])

    except (ValueError, KeyError, TypeError):
        return None


@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin")

    if origin == ALLOWED_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Vary"] = "Origin"

    return response


def is_admin():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user = check_telegram_user(init_data)

    return user if user and user.get("id") == ADMIN_ID else None


@app.route("/api/products", methods=["GET"])
def get_products():
    with sqlite3.connect(DATABASE) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM products ORDER BY id DESC"
        ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.route("/api/products", methods=["POST", "OPTIONS"])
def add_product():
    if request.method == "OPTIONS":
        return "", 204

    if not is_admin():
        return jsonify({"error": "Нет доступа"}), 403

    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    price = str(data.get("price", "")).strip()
    emoji = str(data.get("emoji", "🎁")).strip()
    category = str(data.get("category", "")).strip()

    if not name or not price or category not in ("blox", "brainrot"):
        return jsonify({"error": "Проверь название, цену и категорию"}), 400

    with sqlite3.connect(DATABASE) as db:
        cursor = db.execute(
            "INSERT INTO products (name, price, emoji, category) VALUES (?, ?, ?, ?)",
            (name, price, emoji, category)
        )
        product_id = cursor.lastrowid

    return jsonify({"success": True, "id": product_id})


@app.route("/api/products/<int:product_id>", methods=["PUT", "DELETE", "OPTIONS"])
def edit_product(product_id):
    if request.method == "OPTIONS":
        return "", 204

    if not is_admin():
        return jsonify({"error": "Нет доступа"}), 403

    with sqlite3.connect(DATABASE) as db:
        if request.method == "DELETE":
            db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            return jsonify({"success": True})

        data = request.get_json(silent=True) or {}

        name = str(data.get("name", "")).strip()
        price = str(data.get("price", "")).strip()
        emoji = str(data.get("emoji", "🎁")).strip()
        category = str(data.get("category", "")).strip()

        if not name or not price or category not in ("blox", "brainrot"):
            return jsonify({"error": "Проверь данные товара"}), 400

        db.execute(
            """UPDATE products
            SET name = ?, price = ?, emoji = ?, category = ?
            WHERE id = ?""",
            (name, price, emoji, category, product_id)
        )

    return jsonify({"success": True})


if __name__ == "__main__":
    init_db()

    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN!")

    print("Blox Shop Admin Server запущен!")
    app.run(host="127.0.0.1", port=5000, debug=False)
