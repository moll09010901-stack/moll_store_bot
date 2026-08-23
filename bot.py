import os
import re
import random
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# НАСТРОЙКИ
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_GROUP_ID = int(os.getenv(
    "ADMIN_GROUP_ID",
    "-1004485133964"
))

CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()
CARD_OWNER = os.getenv("CARD_OWNER", "").strip()

ADMIN_IDS_TEXT = os.getenv("ADMIN_IDS", "").strip()

ADMIN_IDS = set()

if ADMIN_IDS_TEXT:
    for admin_id in ADMIN_IDS_TEXT.split(","):
        admin_id = admin_id.strip()
        if admin_id.isdigit():
            ADMIN_IDS.add(int(admin_id))

USP_IMAGE_PATH = "usp_ghosts.jpg"

GOLD_PRICE_SOM = 100
MARKET_PERCENT = Decimal("1.20")

DB_NAME = "moll_store.db"


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            gold INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount_som INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            gold_amount INTEGER,
            market_price TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def ensure_user(user):
    conn = get_db()

    conn.execute("""
        INSERT INTO users (user_id, username, full_name, gold, created_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name
    """, (
        user.id,
        user.username or "",
        user.full_name or "",
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_gold(user_id: int):
    # У администратора бесконечный баланс
    if is_admin(user_id):
        return None

    conn = get_db()

    row = conn.execute(
        "SELECT gold FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    conn.close()

    if row:
        return int(row["gold"])

    return 0


def add_gold(user_id: int, amount: int):
    conn = get_db()

    conn.execute("""
        UPDATE users
        SET gold = gold + ?
        WHERE user_id = ?
    """, (amount, user_id))

    conn.commit()
    conn.close()


def remove_gold(user_id: int, amount: int):
    if is_admin(user_id):
        return True

    current_gold = get_gold(user_id)

    if current_gold < amount:
        return False

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET gold = gold - ?
        WHERE user_id = ?
    """, (amount, user_id))

    conn.commit()
    conn.close()

    return True


# =========================================================
# МЕНЮ
# =========================================================
def main_menu(user_id):
    keyboard = [
        [
            KeyboardButton("💳 Пополнить баланс"),
            KeyboardButton("🧮 Калькулятор"),
        ],
        [
            KeyboardButton("🎮 Standoff 2"),
            KeyboardButton("💰 Мой баланс"),
        ],
    ]

    if is_admin(user_id):
        keyboard.append([
            KeyboardButton("📊 Статистика"),
        ])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def format_number(number):
    return f"{number:,}".replace(",", " ")


def parse_som(text: str):
    """
    Принимает:
    56000
    56 000
    56 тысяч
    56 тыс
    100000 сум
    """

    text = text.lower().replace(",", "").strip()

    multiplier = 1

    if "тысяч" in text or "тысяча" in text or "тыс" in text:
        multiplier = 1000

    numbers = re.findall(r"\d+", text)

    if not numbers:
        return None

    try:
        value = int(numbers[0]) * multiplier
        return value
    except Exception:
        return None


def parse_gold(text: str):
    """
    Ищет количество Gold.
    Например:
    180
    180 голды
    хочу 200 gold
    """

    numbers = re.findall(r"\d+", text.lower())

    if not numbers:
        return None

    try:
        return int(numbers[0])
    except Exception:
        return None


def is_money_text(text: str):
    text = text.lower()

    money_words = [
        "сум",
        "сом",
        "тыс",
        "тысяч",
        "тысяча",
    ]

    return any(word in text for word in money_words)


def is_gold_text(text: str):
    text = text.lower()

    gold_words = [
        "gold",
        "голд",
        "голды",
        "голду",
    ]

    return any(word in text for word in gold_words)


def calculate_market_price(gold_amount: int):
    """
    +20%
    + случайные копейки от 0.01 до 0.99
    """

    base = Decimal(gold_amount) * MARKET_PERCENT

    kopecks = Decimal(random.randint(1, 99)) / Decimal(100)

    final_price = base + kopecks

    return final_price.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


def user_name_text(user):
    if user.username:
        return f"@{user.username}"

    return user.full_name


# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(user)

    context.user_data.clear()

    welcome_text = (
        "👋 Здравствуйте!\n\n"
        "Добро пожаловать в MOLL STORE! 💙\n\n"
        "Мы рады видеть вас в нашем сервисе. "
        "Здесь вы можете пополнить баланс, рассчитать стоимость Gold "
        "и оформить заявку для Standoff 2.\n\n"
        "Выберите нужный раздел ниже 👇"
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=main_menu(user.id)
    )


# =========================================================
# МОЙ БАЛАНС
# =========================================================

async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(user)

    if is_admin(user.id):
        await update.message.reply_text(
            "👑 Ваш баланс\n\n"
            "♾️ Бесконечный Gold\n\n"
            "Вы являетесь администратором."
        )
        return

    gold = get_gold(user.id)

    await update.message.reply_text(
        f"💰 Ваш баланс\n\n"
        f"🪙 {format_number(gold)} Gold"
    )


# =========================================================
# СТАТИСТИКА
# =========================================================

async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Статистика только для администратора
    if not is_admin(user.id):
        await update.message.reply_text(
            "🔒 Статистика доступна только администратору."
        )
        return

    conn = get_db()

    users_count = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    confirmed_topups = conn.execute("""
        SELECT COUNT(*)
        FROM topups
        WHERE status = 'confirmed'
    """).fetchone()[0]

    total_money = conn.execute("""
        SELECT COALESCE(SUM(amount_som), 0)
        FROM topups
        WHERE status = 'confirmed'
    """).fetchone()[0]

    total_gold_added = conn.execute("""
        SELECT COALESCE(SUM(CAST(amount_som / 100 AS INTEGER)), 0)
        FROM topups
        WHERE status = 'confirmed'
    """).fetchone()[0]

    confirmed_withdrawals = conn.execute("""
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status = 'confirmed'
    """).fetchone()[0]

    total_gold_withdrawn = conn.execute("""
        SELECT COALESCE(SUM(gold_amount), 0)
        FROM withdrawals
        WHERE status = 'confirmed'
    """).fetchone()[0]

    conn.close()

    text = (
        "📊 СТАТИСТИКА MOLL STORE\n\n"
        f"👥 Пользователей: {format_number(users_count)}\n"
        f"💳 Подтверждённых пополнений: {format_number(confirmed_topups)}\n"
        f"💵 Всего подтверждено денег: {format_number(total_money)} сум\n"
        f"🪙 Всего начислено Gold: {format_number(total_gold_added)}\n"
        f"🎮 Подтверждённых заявок Standoff 2: {format_number(confirmed_withdrawals)}\n"
        f"💰 Всего списано Gold: {format_number(total_gold_withdrawn)}"
    )

    await update.message.reply_text(text)


# =========================================================
# ПОПОЛНЕНИЕ БАЛАНСА
# =========================================================

async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "waiting_topup_amount"

    card_text = (
        "💳 Пополнение баланса\n\n"
    )

    if CARD_NUMBER:
        card_text += f"Номер карты: {CARD_NUMBER}\n"

    if CARD_OWNER:
        card_text += f"Получатель: {CARD_OWNER}\n"

    card_text += (
        "\nВведите сумму, которую хотите пополнить.\n\n"
        "После оплаты отправьте, пожалуйста, чек."
    )

    await update.message.reply_text(card_text)


async def topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    amount = parse_som(text)

    if not amount or amount <= 0:
        await update.message.reply_text(
            "❌ Введите корректную сумму."
        )
        return

    context.user_data["topup_amount"] = amount
    context.user_data["state"] = "waiting_topup_receipt"

    await update.message.reply_text(
        f"💳 Сумма пополнения: {format_number(amount)} сум\n\n"
        "📸 Теперь отправьте чек об оплате."
    )


async def topup_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    amount = context.user_data.get("topup_amount")

    if not amount:
        context.user_data.clear()
        await update.message.reply_text(
            "❌ Сначала выберите пополнение и укажите сумму."
        )
        return

    # Принимаем фото или документ
    photo_file_id = None
    document_file_id = None

    if update.message.photo:
        photo_file_id = update.message.photo[-1].file_id

    elif update.message.document:
        document_file_id = update.message.document.file_id

    else:
        await update.message.reply_text(
            "❌ Отправьте чек фотографией или документом."
        )
        return

    # Создаём заявку
    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO topups (
            user_id,
            amount_som,
            status,
            created_at
        )
        VALUES (?, ?, 'pending', ?)
    """, (
        user.id,
        amount,
        datetime.now().isoformat()
    ))

    request_id = cursor.lastrowid

    conn.commit()
    conn.close()

    username = user_name_text(user)

    admin_text = (
        "💳 НОВАЯ ЗАЯВКА НА ПОПОЛНЕНИЕ\n\n"
        f"🆔 Заявка: #{request_id}\n"
        f"👤 Пользователь: {username}\n"
        f"💵 Сумма: {format_number(amount)} сум\n"
        f"🪙 Будет начислено: {format_number(amount // GOLD_PRICE_SOM)} Gold"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"topup_confirm_{request_id}"
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"topup_reject_{request_id}"
            ),
        ]
    ])

    try:
        if photo_file_id:
            await context.bot.send_photo(
                chat_id=ADMIN_GROUP_ID,
                photo=photo_file_id,
                caption=admin_text,
                reply_markup=keyboard
            )
        else:
            await context.bot.send_document(
                chat_id=ADMIN_GROUP_ID,
                document=document_file_id,
                caption=admin_text,
                reply_markup=keyboard
            )

    except Exception as error:
        print("Ошибка отправки заявки в группу:", error)

        await update.message.reply_text(
            "⚠️ Чек получен, но возникла ошибка при отправке заявки администрации."
        )

        return

    context.user_data.clear()

    await update.message.reply_text(
        "⏳ Ваш чек получен.\n\n"
        "Пожалуйста, подождите. Ваша заявка будет рассмотрена "
        "примерно в течение 2 часов."
    )


# =========================================================
# КАЛЬКУЛЯТОР
# =========================================================

async def calculator_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "calculator"

    await update.message.reply_text(
        "🧮 Калькулятор Gold\n\n"
        "🪙 1 Gold = 100 сум\n\n"
        "Введите количество Gold или сумму в сумах."
    )


async def calculator_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Если человек явно пишет Gold
    if is_gold_text(text):
        gold = parse_gold(text)

        if not gold or gold <= 0:
            await update.message.reply_text(
                "❌ Введите корректное количество Gold."
            )
            return

        som = gold * GOLD_PRICE_SOM

        await update.message.reply_text(
            f"🪙 {format_number(gold)} Gold = "
            f"{format_number(som)} сум"
        )

        return

    # Если человек явно пишет сумму
    if is_money_text(text):
        som = parse_som(text)

        if not som or som <= 0:
            await update.message.reply_text(
                "❌ Введите корректную сумму."
            )
            return

        gold = som // GOLD_PRICE_SOM

        await update.message.reply_text(
            f"💰 За {format_number(som)} сум "
            f"вы можете получить {format_number(gold)} Gold."
        )

        return

    # Если просто число — считаем как Gold
    number = parse_gold(text)

    if number and number > 0:
        som = number * GOLD_PRICE_SOM

        await update.message.reply_text(
            f"🪙 {format_number(number)} Gold = "
            f"{format_number(som)} сум"
        )
        return

    await update.message.reply_text(
        "❌ Не удалось выполнить расчёт."
    )


# =========================================================
# STANDOFF 2
# =========================================================

async def standoff_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(user)

    context.user_data.clear()
    context.user_data["state"] = "waiting_withdraw_gold"

    if is_admin(user.id):
        balance_text = "♾️ Бесконечный Gold"
    else:
        gold = get_gold(user.id)
        balance_text = f"🪙 Ваш баланс: {format_number(gold)} Gold"

    await update.message.reply_text(
        "🎮 Standoff 2\n\n"
        f"{balance_text}\n\n"
        "Напишите количество Gold."
    )


async def standoff_gold_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    gold_amount = parse_gold(update.message.text)

    if not gold_amount or gold_amount <= 0:
        await update.message.reply_text(
            "❌ Напишите корректное количество Gold."
        )
        return

    if not is_admin(user.id):
        current_gold = get_gold(user.id)

        if gold_amount > current_gold:
            await update.message.reply_text(
                "❌ На вашем балансе недостаточно Gold."
            )
            return

    market_price = calculate_market_price(gold_amount)

    context.user_data["withdraw_gold"] = gold_amount
    context.user_data["market_price"] = str(market_price)
    context.user_data["state"] = "waiting_market_screenshot"

    caption = (
        "🎮 Заявка Standoff 2\n\n"
        f"🪙 Количество: {format_number(gold_amount)} Gold\n"
        f"📈 Цена для рынка (+20%): {market_price}\n\n"
        "📸 Сделайте всё так же, как показано на скриншоте.\n\n"
        "Выставьте USP Ghost на указанную сумму и отправьте "
        "нам скриншот с рынка с вкладкой «Мои запросы».\n\n"
        "Убедитесь, что предмет и наклейки соответствуют "
        "примеру на изображении."
    )

    try:
        with open(USP_IMAGE_PATH, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=caption
            )

    except FileNotFoundError:
        await update.message.reply_text(
            caption +
            "\n\n⚠️ Файл usp_ghosts.jpg не найден в проекте."
        )

    await update.message.reply_text(
        "📸 Теперь отправьте скриншот вашего запроса с рынка."
    )


async def market_screenshot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    gold_amount = context.user_data.get("withdraw_gold")
    market_price = context.user_data.get("market_price")

    if not gold_amount or not market_price:
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Сначала начните новую заявку через Standoff 2."
        )
        return

    photo_file_id = None

    if update.message.photo:
        photo_file_id = update.message.photo[-1].file_id

    elif update.message.document:
        document_file_id = update.message.document.file_id
    else:
        await update.message.reply_text(
            "❌ Отправьте скриншот фотографией или файлом."
        )
        return

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO withdrawals (
            user_id,
            gold_amount,
            market_price,
            status,
            created_at
        )
        VALUES (?, ?, ?, 'pending', ?)
    """, (
        user.id,
        gold_amount,
        market_price,
        datetime.now().isoformat()
    ))

    request_id = cursor.lastrowid

    conn.commit()
    conn.close()

    username = user_name_text(user)

    admin_text = (
        "🎮 НОВАЯ ЗАЯВКА STANDOFF 2\n\n"
        f"🆔 Заявка: #{request_id}\n"
        f"👤 Пользователь: {username}\n"
        f"🪙 Gold: {format_number(gold_amount)}\n"
        f"📈 Цена на рынке: {market_price}\n\n"
        "Выберите действие:"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"withdraw_confirm_{request_id}"
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"withdraw_reject_{request_id}"
            ),
        ]
    ])

    try:
        if update.message.photo:
            await context.bot.send_photo(
                chat_id=ADMIN_GROUP_ID,
                photo=photo_file_id,
                caption=admin_text,
                reply_markup=keyboard
            )
        else:
            await context.bot.send_document(
                chat_id=ADMIN_GROUP_ID,
                document=document_file_id,
                caption=admin_text,
                reply_markup=keyboard
            )

    except Exception as error:
        print("Ошибка отправки заявки Standoff 2:", error)

        await update.message.reply_text(
            "⚠️ Скриншот получен, но не удалось отправить заявку администрации."
        )

        return

    context.user_data.clear()

    await update.message.reply_text(
        "⏳ Ваша заявка отправлена администрации.\n\n"
        "Пожалуйста, ожидайте решения."
    )


# =========================================================
# АДМИН КНОПКИ
# =========================================================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    if not query:
        return

    admin_user = query.from_user

    if not is_admin(admin_user.id):
        await query.answer(
            "У вас нет доступа.",
            show_alert=True
        )
        return

    await query.answer()

    data = query.data

    # -----------------------------------------------------
    # ПОДТВЕРЖДЕНИЕ ПОПОЛНЕНИЯ
    # -----------------------------------------------------

    if data.startswith("topup_confirm_"):
        request_id = int(data.split("_")[-1])

        conn = get_db()

        request = conn.execute("""
            SELECT *
            FROM topups
            WHERE id = ?
        """, (request_id,)).fetchone()

        if not request:
            conn.close()

            await query.answer(
                "Заявка не найдена.",
                show_alert=True
            )
            return

        if request["status"] != "pending":
            conn.close()

            await query.answer(
                "Эта заявка уже обработана.",
                show_alert=True
            )
            return

        amount_som = int(request["amount_som"])
        user_id = int(request["user_id"])

        gold_to_add = amount_som // GOLD_PRICE_SOM

        conn.execute("""
            UPDATE topups
            SET status = 'confirmed'
            WHERE id = ?
        """, (request_id,))

        conn.execute("""
            UPDATE users
            SET gold = gold + ?
            WHERE user_id = ?
        """, (
            gold_to_add,
            user_id
        ))

        conn.commit()
        conn.close()

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ Ваше пополнение подтверждено!\n\n"
                    f"💵 Подтверждено: {format_number(amount_som)} сум\n"
                    f"🪙 Начислено: {format_number(gold_to_add)} Gold\n\n"
                    "Спасибо за использование MOLL STORE 💙"
                )
            )
        except Exception as error:
            print("Не удалось написать пользователю:", error)

        await query.edit_message_caption(
            caption=(
                "✅ ЗАЯВКА НА ПОПОЛНЕНИЕ ПОДТВЕРЖДЕНА\n\n"
                f"🆔 Заявка: #{request_id}\n"
                f"💵 Сумма: {format_number(amount_som)} сум\n"
                f"🪙 Начислено: {format_number(gold_to_add)} Gold"
            )
            if query.message.caption
            else None,
            reply_markup=None
        )

        return

    # -----------------------------------------------------
    # ОТКЛОНЕНИЕ ПОПОЛНЕНИЯ
    # -----------------------------------------------------

    if data.startswith("topup_reject_"):
        request_id = int(data.split("_")[-1])

        conn = get_db()

        request = conn.execute("""
            SELECT *
            FROM topups
            WHERE id = ?
        """, (request_id,)).fetchone()

        if not request or request["status"] != "pending":
            conn.close()

            await query.answer(
                "Эта заявка уже обработана.",
                show_alert=True
            )
            return

        conn.execute("""
            UPDATE topups
            SET status = 'rejected'
            WHERE id = ?
        """, (request_id,))

        conn.commit()
        conn.close()

        try:
            await context.bot.send_message(
                chat_id=request["user_id"],
                text=(
                    "❌ Ваша заявка на пополнение была отклонена.\n\n"
                    "Gold не был начислен."
                )
            )
        except Exception as error:
            print("Ошибка сообщения пользователю:", error)

        await query.edit_message_reply_markup(reply_markup=None)

        return

    # -----------------------------------------------------
    # ПОДТВЕРЖДЕНИЕ STANDOFF 2
    # -----------------------------------------------------

    if data.startswith("withdraw_confirm_"):
        request_id = int(data.split("_")[-1])

        conn = get_db()

        request = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
        """, (request_id,)).fetchone()

        if not request:
            conn.close()

            await query.answer(
                "Заявка не найдена.",
                show_alert=True
            )
            return

        if request["status"] != "pending":
            conn.close()

            await query.answer(
                "Эта заявка уже обработана.",
                show_alert=True
            )
            return

        user_id = int(request["user_id"])
        gold_amount = int(request["gold_amount"])

        # Администратор не теряет Gold
        if not is_admin(user_id):
            user_row = conn.execute("""
                SELECT gold
                FROM users
                WHERE user_id = ?
            """, (user_id,)).fetchone()

            if not user_row or int(user_row["gold"]) < gold_amount:
                conn.close()

                await query.answer(
                    "У пользователя уже недостаточно Gold.",
                    show_alert=True
                )
                return

            conn.execute("""
                UPDATE users
                SET gold = gold - ?
                WHERE user_id = ?
            """, (
                gold_amount,
                user_id
            ))

        conn.execute("""
            UPDATE withdrawals
            SET status = 'confirmed'
            WHERE id = ?
        """, (request_id,))

        conn.commit()
        conn.close()

        if is_admin(user_id):
            balance_text = "♾️ Бесконечный Gold"
        else:
            new_balance = get_gold(user_id)
            balance_text = f"💳 Ваш текущий баланс: {format_number(new_balance)} Gold"

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ Ваша заявка подтверждена!\n\n"
                    f"🪙 Списано: {format_number(gold_amount)} Gold\n"
                    f"{balance_text}\n\n"
                    "Спасибо за использование MOLL STORE 💙"
                )
            )
        except Exception as error:
            print("Ошибка сообщения пользователю:", error)

        await query.edit_message_reply_markup(reply_markup=None)

        return

    # -----------------------------------------------------
    # ОТКЛОНЕНИЕ STANDOFF 2
    # -----------------------------------------------------

    if data.startswith("withdraw_reject_"):
        request_id = int(data.split("_")[-1])

        conn = get_db()

        request = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
        """, (request_id,)).fetchone()

        if not request or request["status"] != "pending":
            conn.close()

            await query.answer(
                "Эта заявка уже обработана.",
                show_alert=True
            )
            return

        conn.execute("""
            UPDATE withdrawals
            SET status = 'rejected'
            WHERE id = ?
        """, (request_id,))

        conn.commit()
        conn.close()

        try:
            await context.bot.send_message(
                chat_id=request["user_id"],
                text=(
                    "❌ Ваша заявка Standoff 2 была отклонена.\n\n"
                    "Gold с вашего баланса не списан."
                )
            )
        except Exception as error:
            print("Ошибка сообщения пользователю:", error)

        await query.edit_message_reply_markup(reply_markup=None)

        return


# =========================================================
# ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ
# =========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    user = update.effective_user

    ensure_user(user)

    # -----------------------------------------------------
    # КНОПКИ ГЛАВНОГО МЕНЮ
    # -----------------------------------------------------

    if update.message.text == "💳 Пополнить баланс":
        await topup_start(update, context)
        return

    if update.message.text == "🧮 Калькулятор":
        await calculator_start(update, context)
        return

    if update.message.text == "🎮 Standoff 2":
        await standoff_start(update, context)
        return

    if update.message.text == "💰 Мой баланс":
        await my_balance(update, context)
        return

    if update.message.text == "📊 Статистика":
        await statistics(update, context)
        return

    # -----------------------------------------------------
    # ТЕКУЩЕЕ СОСТОЯНИЕ ПОЛЬЗОВАТЕЛЯ
    # -----------------------------------------------------

    state = context.user_data.get("state")

    if state == "waiting_topup_amount":
        if update.message.text:
            await topup_amount(update, context)
        else:
            await update.message.reply_text(
                "❌ Сначала введите сумму пополнения."
            )
        return

    if state == "waiting_topup_receipt":
        await topup_receipt(update, context)
        return

    if state == "calculator":
        if update.message.text:
            await calculator_message(update, context)
        return

    if state == "waiting_withdraw_gold":
        if update.message.text:
            await standoff_gold_amount(update, context)
        else:
            await update.message.reply_text(
                "❌ Напишите количество Gold."
            )
        return

    if state == "waiting_market_screenshot":
        await market_screenshot(update, context)
        return

    # -----------------------------------------------------
    # ОБЫЧНОЕ СООБЩЕНИЕ
    # -----------------------------------------------------

    if update.message.text:
        await update.message.reply_text(
            "Выберите нужный раздел в меню 👇",
            reply_markup=main_menu()
        )


# =========================================================
# ЗАПУСК
# =========================================================

def main():
    if not BOT_TOKEN:
        print("ОШИБКА: BOT_TOKEN не найден в .env")
        return

    init_db()

    print("Бот MOLL STORE запускается...")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(admin_callback)
    )

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_message
        )
    )

    print("Бот MOLL STORE запущен!")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()