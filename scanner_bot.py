"""
Scanner Bot — сканер визиток и буклетов.
Принимает фото и голосовые комментарии,
извлекает данные через Claude Vision + Whisper,
показывает результат. Без базы данных.

Env vars:
  SCANNER_BOT_TOKEN   — токен Telegram бота
  ANTHROPIC_API_KEY   — Claude API
  OPENAI_API_KEY      — Whisper API
"""

import logging
import os
import json
import re
import base64
import io

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN        = os.getenv("SCANNER_BOT_TOKEN", "8692863987:AAFBRaEG9rwNcynoZsAZrtPN1ksKxhQD2eg")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")


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

    # 3. Найти JSON по фигурным скобкам
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # 4. Вытащить каждое поле регуляркой
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
        user_states[user_id] = {"mode": "idle", "photos": [], "voices": []}
    return user_states[user_id]


def reset_state(user_id: int):
    user_states[user_id] = {"mode": "idle", "photos": [], "voices": []}


# ─── Keyboards ──────────────────────────────────────────────────────────────

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Новая запись", callback_data="new_entry")],
    ])


def collecting_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Готово — обработать", callback_data="done")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])


# ─── Formatting ─────────────────────────────────────────────────────────────

def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_card(data: dict) -> str:
    lines = []
    if data.get("company"):
        lines.append(f"🏢 <b>{esc(data['company'])}</b>")
    if data.get("contact_name"):
        lines.append(f"👤 {esc(data['contact_name'])}")
    if data.get("phone"):
        lines.append(f"📞 {esc(data['phone'])}")
    if data.get("email"):
        lines.append(f"📧 {esc(data['email'])}")
    if data.get("website"):
        lines.append(f"🌐 {esc(data['website'])}")
    if data.get("products"):
        lines.append(f"📦 {esc(data['products'])}")
    location = ", ".join(filter(None, [data.get("city"), data.get("country")]))
    if location:
        lines.append(f"📍 {esc(location)}")
    if data.get("voice_comment"):
        lines.append(f"💬 <i>{esc(data['voice_comment'])}</i>")
    return "\n".join(lines) if lines else "<i>(данные не извлечены)</i>"


# ─── Handlers ───────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_state(update.effective_user.id)
    await update.message.reply_text(
        "👋 <b>Сканер визиток</b>\n\n"
        "Нажмите <b>Новая запись</b>, затем отправьте:\n"
        "• 📷 фото визитки или буклета\n"
        "• 🎤 голосовой комментарий\n\n"
        "Можно несколько фото и голосовых. Когда всё — нажмите <b>Готово</b>.",
        parse_mode="HTML",
        reply_markup=main_kb(),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_state(user_id)
    data = query.data

    if data == "new_entry":
        reset_state(user_id)
        state = get_state(user_id)
        state["mode"] = "collecting"
        await query.message.reply_text(
            "📝 <b>Новая запись</b>\n\n"
            "Отправляйте фото и голосовые — в любом порядке и количестве.\n"
            "Когда закончите — нажмите <b>Готово</b>.",
            parse_mode="HTML",
            reply_markup=collecting_kb(),
        )

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
            reset_state(user_id)
            await msg.edit_text(
                f"✅ <b>Готово!</b>\n\n{fmt_card(extracted)}\n\n"
                "Хотите отсканировать ещё одну?",
                parse_mode="HTML",
                reply_markup=main_kb(),
            )
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            state["mode"] = "collecting"
            await msg.edit_text(
                f"⚠️ Ошибка обработки: {e}\n\nПопробуйте ещё раз.",
                reply_markup=collecting_kb(),
            )

    elif data == "cancel":
        reset_state(user_id)
        await query.message.reply_text("Отменено.", reply_markup=main_kb())


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_state(user_id)
    if state["mode"] != "collecting":
        await update.message.reply_text(
            "Нажмите «Новая запись» чтобы начать.", reply_markup=main_kb()
        )
        return

    photo = update.message.photo[-1]
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
            f"{status}\n\n<i>{esc(text)}</i>", parse_mode="HTML", reply_markup=collecting_kb()
        )
    except Exception as e:
        await msg.edit_text(f"⚠️ Ошибка распознавания: {e}", reply_markup=collecting_kb())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Используйте кнопки ниже.", reply_markup=main_kb()
    )


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logger.info("Scanner bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
