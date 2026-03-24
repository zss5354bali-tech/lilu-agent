import logging
import os
import httpx
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

histories = {}
voice_mode = {}

SYSTEM_PROMPT = """Ты Lilu — личный AI-ассистент Сергея. Ты живёшь в Telegram.
Твои возможности: отвечаешь на вопросы, ищешь информацию, пишешь тексты, переводишь, помогаешь с задачами, анализируешь фото.
Характер: умная, дружелюбная, с юмором. Говоришь по-русски. Краткая и по делу — особенно в голосовых ответах (2-3 предложения).
Сергей живёт на Бали, у него платформа AkuMau для поиска товаров и услуг на Бали."""

async def ask_claude(user_id, message, image_data=None):
    if user_id not in histories:
        histories[user_id] = []
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}}, {"type": "text", "text": message or "Что на фото?"}] if image_data else message
    histories[user_id].append({"role": "user", "content": content})
    if len(histories[user_id]) > 20:
        histories[user_id] = histories[user_id][-20:]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1024, "system": SYSTEM_PROMPT, "messages": histories[user_id]})
        reply = r.json()["content"][0]["text"]
        histories[user_id].append({"role": "assistant", "content": reply})
        return reply

async def transcribe(voice_bytes):
    async with httpx.AsyncClient(timeout=60) as client:
        # Try with mp3 extension - Whisper is more reliable with it
        r = await client.post("https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": ("audio.mp3", voice_bytes, "audio/mpeg")},
            data={"model": "whisper-1"})
        logger.info(f"Whisper response: {r.text}")
        data = r.json()
        return data.get("text", "")

async def tts(text):
    clean = text.replace("*","").replace("_","").replace("`","").replace("#","")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": "tts-1", "input": clean[:4096], "voice": "nova", "response_format": "mp3"})
        return r.content

def is_owner(uid):
    return OWNER_ID == 0 or uid == OWNER_ID

async def send_reply(update, ctx, text, uid):
    if voice_mode.get(uid):
        try:
            await ctx.bot.send_chat_action(update.effective_chat.id, "record_voice")
            audio = await tts(text)
            await update.message.reply_audio(audio, filename="lilu.mp3")
            return
        except Exception as e:
            logger.error(f"TTS: {e}")
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i+4096])

async def start(update, ctx):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ закрыт.")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔤 Текст", callback_data="mode_text"), InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")]])
    await update.message.reply_text("👋 Привет, Сергей! Я *Lilu* — твой личный ассистент.\n\nМогу отвечать текстом или голосом 🎤\nПросто пиши или отправляй голосовые!", parse_mode="Markdown", reply_markup=kb)

async def set_mode(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if q.data == "mode_voice":
        voice_mode[uid] = True
        await q.edit_message_text("🎤 *Голосовой режим!* Отвечаю голосом.", parse_mode="Markdown")
    else:
        voice_mode[uid] = False
        await q.edit_message_text("🔤 *Текстовый режим!* Отвечаю текстом.", parse_mode="Markdown")

async def mode_cmd(update, ctx):
    uid = update.effective_user.id
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔤 Текст", callback_data="mode_text"), InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")]])
    cur = "🎤 Голосовой" if voice_mode.get(uid) else "🔤 Текстовый"
    await update.message.reply_text(f"Режим: *{cur}*", parse_mode="Markdown", reply_markup=kb)

async def clear(update, ctx):
    histories.pop(update.effective_user.id, None)
    await update.message.reply_text("🧹 Память очищена!")

async def handle_text(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid): return
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        reply = await ask_claude(uid, update.message.text)
        await send_reply(update, ctx, reply, uid)
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

async def handle_voice(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid): return
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        # Download voice file
        voice_obj = update.message.voice
        logger.info(f"Voice: duration={voice_obj.duration}, mime={voice_obj.mime_type}, size={voice_obj.file_size}")
        
        f = await ctx.bot.get_file(voice_obj.file_id)
        async with httpx.AsyncClient() as client:
            voice_bytes = (await client.get(f.file_path)).content
        
        logger.info(f"Downloaded {len(voice_bytes)} bytes")
        
        text = await transcribe(voice_bytes)
        logger.info(f"Transcribed: '{text}'")
        
        if not text or text.strip() == "":
            await update.message.reply_text("🤔 Не расслышала — говори чуть громче и чётче!")
            return
        
        await update.message.reply_text(f"🎤 _{text}_", parse_mode="Markdown")
        reply = await ask_claude(uid, text)
        
        try:
            audio = await tts(reply)
            await update.message.reply_audio(audio, filename="lilu.mp3")
        except Exception as e:
            logger.error(f"TTS error: {e}")
            await update.message.reply_text(reply)
            
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text(f"⚠️ {e}")

async def handle_photo(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid): return
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        photo = update.message.photo[-1]
        f = await ctx.bot.get_file(photo.file_id)
        async with httpx.AsyncClient() as client:
            img = base64.b64encode((await client.get(f.file_path)).content).decode()
        reply = await ask_claude(uid, update.message.caption or "Что на фото?", img)
        await send_reply(update, ctx, reply, uid)
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CallbackQueryHandler(set_mode, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Lilu запущена!")
    app.run_polling()

if __name__ == "__main__":
    main()
