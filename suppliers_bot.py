"""
Suppliers Bot — база поставщиков.
Принимает фото визиток/буклетов и голосовые комментарии,
извлекает данные через Claude Vision + Whisper,
сохраняет в SQLite и Google Sheets.

Env vars:
  SUPPLIERS_BOT_TOKEN       — токен Telegram бота
  ANTHROPIC_API_KEY         — Claude API
  OPENAI_API_KEY            — Whisper API
  OWNER_ID                  — ваш Telegram user_id
  GOOGLE_SHEETS_ID          — ID Google таблицы (опционально)
  GOOGLE_SERVICE_ACCOUNT_JSON — JSON сервисного аккаунта строкой (опционально)
"""

import logging
import os
import asyncio
import json
import sqlite3
import datetime
import io
import csv
import base64

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("SUPPLIERS_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

DB_PATH = "suppliers.db"

# ─── Database ───────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company     TEXT,
            contact_name TEXT,
            phone       TEXT,
            email       TEXT,
            website     TEXT,
            products    TEXT,
            country     TEXT,
            city        TEXT,
            voice_comment TEXT,
            raw_text    TEXT,
            created_at  TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_supplier(data: dict) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """INSERT INTO suppliers
           (company, contact_name, phone, email, website, products,
            country, city, voice_comment, raw_text, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data.get("company", ""),
            data.get("contact_name", ""),
            data.get("phone", ""),
            data.get("email", ""),
            data.get("website", ""),
            data.get("products", ""),
            data.get("country", ""),
            data.get("city", ""),
            data.get("voice_comment", ""),
            data.get("raw_text", ""),
            datetime.datetime.now().isoformat(),
        ),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_all_suppliers() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM suppliers ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_suppliers(query: str) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    words = query.lower().split()
    if not words:
        return []
    conditions = []
    params = []
    for word in words:
        conditions.append(
            "(lower(company) LIKE ? OR lower(contact_name) LIKE ? OR "
            "lower(products) LIKE ? OR lower(email) LIKE ? OR "
            "lower(phone) LIKE ? OR lower(voice_comment) LIKE ?)"
        )
        p = f"%{word}%"
        params.extend([p, p, p, p, p, p])
    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM suppliers WHERE {where} ORDER BY created_at DESC", params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_supplier(supplier_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ─── Google Sheets ──────────────────────────────────────────────────────────

SHEET_HEADERS = [
    "ID", "Компания", "Контакт", "Телефон", "Email", "Сайт",
    "Продукты/услуги", "Страна", "Город", "Комментарий", "Дата"
]


def sync_to_sheets(supplier_id: int, data: dict):
    if not GOOGLE_SHEETS_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(GOOGLE_SHEETS_ID).sheet1

        # Добавить заголовки если таблица пустая
        try:
            first_row = sheet.row_values(1)
        except Exception:
            first_row = []
        if not first_row or first_row[0] != "ID":
            sheet.insert_row(SHEET_HEADERS, 1)

        sheet.append_row([
            supplier_id,
            data.get("company", ""),
            data.get("contact_name", ""),
            data.get("phone", ""),
            data.get("email", ""),
            data.get("website", ""),
            data.get("products", ""),
            data.get("country", ""),
            data.get("city", ""),
            data.get("voice_comment", ""),
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        ])
        logger.info(f"Synced supplier #{supplier_id} to Google Sheets")
    except Exception as e:
        logger.error(f"Google Sheets sync failed: {e}")


# ─── AI ─────────────────────────────────────────────────────────────────────

async def transcribe_voice(file_bytes: bytes) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": ("voice.ogg", file_bytes, "audio/ogg")},
            data={"model": "whisper-1"},
        )
        response.raise_for_status()
        return response.json()["text"]


def _parse_json_robust(text: str) -> dict:
    """Извлекает JSON из ответа Claude с несколькими fallback-стратегиями."""
    import re

    # 1. Прямой парсинг
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Убрать markdown-обёртку
    clean = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 3. Найти JSON-объект по фигурным скобкам
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # 4. Вытащить каждое поле регуляркой (последний шанс)
    fields = ["company", "contact_name", "phone", "email", "website",
              "products", "country", "city", "voice_comment"]
    result = {f: "" for f in fields}
    for field in fields:
        m = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            result[field] = m.group(1)
    return result


async def extract_supplier_data(photos_b64: list[str], voice_text: str) -> dict:
    content = []

    for b64 in photos_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })

    prompt = "Извлеки информацию о поставщике"
    if photos_b64:
        prompt += " из изображений (визитки, буклеты, прайс-листы)"
    if voice_text:
        prompt += f"\n\nГолосовой комментарий пользователя:\n{voice_text}"

    prompt += """

Верни ТОЛЬКО валидный JSON — без markdown, без пояснений, без переносов строк внутри значений.
Все значения — однострочные строки, спецсимволы экранировать.

{"company":"","contact_name":"","phone":"","email":"","website":"","products":"","country":"","city":"","voice_comment":""}

Заполни поля по данным. Если поле не найдено — пустая строка."""

    content.append({"type": "text", "text": prompt})

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": content}],
            },
        )
        response.raise_for_status()
        text = response.json()["content"][0]["text"].strip()

    return _parse_json_robust(text)


# ─── State ──────────────────────────────────────────────────────────────────

user_states: dict[int, dict] = {}


def get_state(user_id: int) -> dict:
    if user_id not in user_states:
        user_states[user_id] = {"mode": "idle", "photos": [], "voices": [], "extracted": {}}
    return user_states[user_id]


def reset_state(user_id: int):
    user_states[user_id] = {"mode": "idle", "photos": [], "voices": [], "extracted": {}}


# ─── Keyboards ──────────────────────────────────────────────────────────────

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Новая запись", callback_data="new_entry")],
        [
            InlineKeyboardButton("📋 Список", callback_data="list"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search"),
        ],
        [InlineKeyboardButton("📥 Экспорт CSV", callback_data="export")],
    ])


def collecting_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Готово — обработать", callback_data="done")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])


def confirm_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Сохранить", callback_data="save"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
        ],
    ])


# ─── Formatting ─────────────────────────────────────────────────────────────

def fmt_card(data: dict, sid: int = None) -> str:
    lines = []
    if sid:
        lines.append(f"*#{sid}*")
    if data.get("company"):
        lines.append(f"🏢 *{data['company']}*")
    if data.get("contact_name"):
        lines.append(f"👤 {data['contact_name']}")
    if data.get("phone"):
        lines.append(f"📞 {data['phone']}")
    if data.get("email"):
        lines.append(f"📧 {data['email']}")
    if data.get("website"):
        lines.append(f"🌐 {data['website']}")
    if data.get("products"):
        lines.append(f"📦 {data['products']}")
    location = ", ".join(filter(None, [data.get("city"), data.get("country")]))
    if location:
        lines.append(f"📍 {location}")
    if data.get("voice_comment"):
        lines.append(f"💬 _{data['voice_comment']}_")
    return "\n".join(lines) if lines else "_(данные не извлечены)_"


# ─── Handlers ───────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_state(update.effective_user.id)
    await update.message.reply_text(
        "👋 *База поставщиков*\n\n"
        "Нажмите *Новая запись*, затем отправьте:\n"
        "• 📷 фото визитки или буклета\n"
        "• 🎤 голосовой комментарий\n\n"
        "Можно несколько фото и голосовых. Когда всё — нажмите *Готово*.",
        parse_mode="Markdown",
        reply_markup=main_kb(),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_state(user_id)
    data = query.data

    # ── Новая запись ──
    if data == "new_entry":
        reset_state(user_id)
        state = get_state(user_id)
        state["mode"] = "collecting"
        await query.message.reply_text(
            "📝 *Новая запись*\n\n"
            "Отправляйте фото и голосовые — в любом порядке и количестве.\n"
            "Когда закончите — нажмите *Готово*.",
            parse_mode="Markdown",
            reply_markup=collecting_kb(),
        )

    # ── Готово — обработать ──
    elif data == "done":
        if state["mode"] != "collecting":
            await query.message.reply_text("Сначала нажмите «Новая запись».")
            return
        if not state["photos"] and not state["voices"]:
            await query.message.reply_text(
                "Нет данных. Отправьте фото или голосовое сообщение.",
                reply_markup=collecting_kb(),
            )
            return

        state["mode"] = "processing"
        msg = await query.message.reply_text("⏳ Обрабатываю данные...")
        try:
            voice_text = "\n".join(state["voices"])
            extracted = await extract_supplier_data(state["photos"], voice_text)
            state["extracted"] = extracted
            state["mode"] = "confirming"
            await msg.edit_text(
                f"Проверьте данные:\n\n{fmt_card(extracted)}\n\nСохранить в базу?",
                parse_mode="Markdown",
                reply_markup=confirm_kb(),
            )
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            state["mode"] = "collecting"
            await msg.edit_text(
                f"⚠️ Ошибка обработки: {e}\n\nПопробуйте ещё раз.",
                reply_markup=collecting_kb(),
            )

    # ── Сохранить ──
    elif data == "save":
        if state["mode"] != "confirming" or not state["extracted"]:
            return
        extracted = state["extracted"].copy()
        supplier_id = save_supplier(extracted)
        asyncio.create_task(asyncio.to_thread(sync_to_sheets, supplier_id, extracted))
        reset_state(user_id)
        await query.message.reply_text(
            f"✅ Сохранено!\n\n{fmt_card(extracted, supplier_id)}",
            parse_mode="Markdown",
            reply_markup=main_kb(),
        )

    # ── Отмена ──
    elif data == "cancel":
        reset_state(user_id)
        await query.message.reply_text("Отменено.", reply_markup=main_kb())

    # ── Список ──
    elif data == "list":
        suppliers = get_all_suppliers()
        if not suppliers:
            await query.message.reply_text("База пуста.", reply_markup=main_kb())
            return
        text = f"📋 *Поставщики — {len(suppliers)} шт.:*\n\n"
        for s in suppliers[:20]:
            name = s.get("company") or s.get("contact_name") or "—"
            text += f"• *#{s['id']}* {name}"
            if s.get("products"):
                text += f" — {s['products'][:50]}"
            text += "\n"
        if len(suppliers) > 20:
            text += f"\n_...и ещё {len(suppliers) - 20}. Используйте экспорт для полного списка._"
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())

    # ── Поиск ──
    elif data == "search":
        state["mode"] = "searching"
        await query.message.reply_text("🔍 Введите запрос для поиска:")

    # ── Экспорт ──
    elif data == "export":
        suppliers = get_all_suppliers()
        if not suppliers:
            await query.message.reply_text("База пуста.", reply_markup=main_kb())
            return
        output = io.StringIO()
        fields = ["id", "company", "contact_name", "phone", "email", "website",
                  "products", "country", "city", "voice_comment", "created_at"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for s in suppliers:
            writer.writerow(s)
        csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM для Excel
        filename = f"suppliers_{datetime.date.today()}.csv"
        await query.message.reply_document(
            document=InputFile(io.BytesIO(csv_bytes), filename=filename),
            caption=f"📥 База поставщиков — {len(suppliers)} записей",
            reply_markup=main_kb(),
        )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_state(user_id)
    if state["mode"] != "collecting":
        await update.message.reply_text(
            "Нажмите «Новая запись» чтобы начать.", reply_markup=main_kb()
        )
        return

    photo = update.message.photo[-1]  # наибольшее разрешение
    file = await context.bot.get_file(photo.file_id)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(file.file_path)
    b64 = base64.b64encode(resp.content).decode()
    state["photos"].append(b64)

    count_p = len(state["photos"])
    count_v = len(state["voices"])
    status = f"📷 Фото: {count_p}"
    if count_v:
        status += f"  🎤 Голосовых: {count_v}"
    await update.message.reply_text(status, reply_markup=collecting_kb())


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_state(user_id)
    if state["mode"] != "collecting":
        await update.message.reply_text(
            "Нажмите «Новая запись» чтобы начать.", reply_markup=main_kb()
        )
        return

    msg = await update.message.reply_text("🎤 Распознаю голос...")
    file = await context.bot.get_file(update.message.voice.file_id)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(file.file_path)
    try:
        text = await transcribe_voice(resp.content)
        state["voices"].append(text)
        count_p = len(state["photos"])
        count_v = len(state["voices"])
        status = f"🎤 Голосовых: {count_v}"
        if count_p:
            status += f"  📷 Фото: {count_p}"
        await msg.edit_text(
            f"{status}\n\n_{text}_", parse_mode="Markdown", reply_markup=collecting_kb()
        )
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка распознавания: {e}", reply_markup=collecting_kb())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_state(user_id)

    if state["mode"] == "searching":
        state["mode"] = "idle"
        query_text = update.message.text.strip()
        results = search_suppliers(query_text)
        if not results:
            await update.message.reply_text(
                f"Ничего не найдено по запросу «{query_text}».", reply_markup=main_kb()
            )
            return
        text = f"🔍 *Найдено: {len(results)}*\n\n"
        for s in results[:10]:
            text += fmt_card(s, s["id"]) + "\n\n─────\n\n"
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())
    else:
        await update.message.reply_text(
            "Используйте кнопки ниже.", reply_markup=main_kb()
        )


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logger.info("Suppliers bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
