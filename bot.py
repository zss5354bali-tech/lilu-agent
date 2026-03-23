import logging
import os
import httpx
import json
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # Only owner can use

# Conversation history per user
histories = {}

SYSTEM_PROMPT = """Ты Lilu — личный AI-ассистент Сергея. Ты живёшь в Telegram.

Твои возможности:
- Отвечаешь на любые вопросы
- Ищешь информацию (когда тебя просят)
- Пишешь тексты, посты для соцсетей, переводишь
- Помогаешь с планированием и задачами
- Анализируешь ссылки и документы которые присылает Сергей

Твой характер:
- Умная, дружелюбная, с юмором
- Говоришь по-русски если Сергей пишет по-русски
- Краткая и по делу, без лишней воды
- Если нужно — можешь быть подробной

Сергей живёт на Бали, занимается бизнесом. У него есть платформа AkuMau для поиска товаров и услуг на Бали.

Когда Сергей просит тебя что-то опубликовать в соцсетях — скажи что эта функция скоро появится и сейчас в разработке.

Отвечай живо и естественно, как умный друг-помощник."""

async def ask_claude(user_id: int, message: str, image_data: str = None) -> str:
    if user_id not in histories:
        histories[user_id] = []
    
    # Build message content
    if image_data:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": message or "Что на этом изображении?"}
        ]
    else:
        content = message
    
    histories[user_id].append({"role": "user", "content": content})
    
    # Keep last 20 messages
    if len(histories[user_id]) > 20:
        histories[user_id] = histories[user_id][-20:]
    
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "messages": histories[user_id]
            }
        )
        
        data = response.json()
        reply = data["content"][0]["text"]
        histories[user_id].append({"role": "assistant", "content": reply})
        return reply

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if OWNER_ID and user_id != OWNER_ID:
        await update.message.reply_text("⛔ Доступ закрыт.")
        return
    
    await update.message.reply_text(
        "👋 Привет, Сергей! Я Lilu — твой личный ассистент.\n\n"
        "Просто напиши мне что нужно сделать — отвечу, найду, напишу, переведу.\n\n"
        "Можешь присылать текст, фото, ссылки. Всё понимаю! 🧠"
    )

async def clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    histories.pop(user_id, None)
    await update.message.reply_text("🧹 Память очищена! Начинаем с чистого листа.")

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if OWNER_ID and user_id != OWNER_ID:
        return
    
    text = update.message.text
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    
    try:
        reply = await ask_claude(user_id, text)
        # Split long messages
        if len(reply) > 4096:
            for i in range(0, len(reply), 4096):
                await update.message.reply_text(reply[i:i+4096])
        else:
            await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"⚠️ Ошибка: {str(e)}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if OWNER_ID and user_id != OWNER_ID:
        return
    
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    
    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(file.file_path)
            import base64
            image_data = base64.b64encode(resp.content).decode()
        
        caption = update.message.caption or "Что на этом фото?"
        reply = await ask_claude(user_id, caption, image_data)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text(f"⚠️ Не смог обработать фото: {str(e)}")

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if OWNER_ID and user_id != OWNER_ID:
        return
    await update.message.reply_text("🎤 Голосовые сообщения пока не поддерживаю, напиши текстом!")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🤖 Lilu запущена!")
    app.run_polling()

if __name__ == "__main__":
    main()
