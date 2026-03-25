import logging
import os
import httpx
import base64
import imaplib
import smtplib
import email
import json
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Mail settings
MAIL_EMAIL = os.getenv("MAIL_EMAIL", "alfa-sz@mail.ru")
MAIL_IMAP = "imap.mail.ru"

# Gmail for sending
GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "zss5354bali@gmail.com")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "aqxlxmambppxawyj")

histories = {}
voice_mode = {}
memory = {}  # Permanent memory per user

SYSTEM_PROMPT = """Ты Lilu — личный AI-ассистент Сергея Жмакова. Живёшь в Telegram.

ВОЗМОЖНОСТИ:
- Отвечаешь на вопросы, ищешь информацию
- Пишешь тексты, посты, переводишь на любой язык
- Управляешь почтой (читать, писать, удалять)
- Помнишь важную информацию о Сергее
- Анализируешь фото и документы

ПОЧТОВЫЕ КОМАНДЫ (вставляй в ответ когда нужно):
[EMAIL_CHECK] — проверить новые письма
[EMAIL_SEND:кому@mail.com:Тема:Текст] — отправить письмо
[EMAIL_DELETE:номер] — удалить письмо по номеру из последней проверки
[MEMORY_SAVE:ключ:значение] — запомнить важную информацию
[MEMORY_GET:ключ] — вспомнить информацию

ПАМЯТЬ СЕРГЕЯ:
{memory}

ХАРАКТЕР:
- Умная, дружелюбная, с лёгким юмором
- Говоришь по-русски
- В голосовых ответах — кратко, 2-3 предложения
- Называй его Сергей или просто по имени

Сергей живёт на Бали, гражданин России, есть инвесторский КИТАС в Индонезии.
У него бизнес-платформа AkuMau для поиска товаров и услуг на Бали."""

# Store last fetched emails for deletion
last_emails = {}

def decode_str(s):
    if not s: return ""
    parts = decode_header(s)
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="ignore")
        else:
            result += str(part)
    return result

def get_emails(user_id, limit=5):
    try:
        mail_password = os.getenv("MAIL_PASSWORD")
        mail = imaplib.IMAP4_SSL(MAIL_IMAP)
        mail.login(MAIL_EMAIL, mail_password)
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        ids = data[0].split()
        if not ids:
            mail.logout()
            return "📭 Новых писем нет!"
        
        result = f"📬 *Новых писем: {len(ids)}*\n\n"
        last_emails[user_id] = []
        
        for i, msg_id in enumerate(ids[-limit:]):
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_str(msg.get("Subject", "Без темы"))
            sender = decode_str(msg.get("From", "Неизвестно"))
            date = msg.get("Date", "")[:16]
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")[:300]
                        break
            else:
                try:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")[:300]
                except:
                    body = ""
            
            last_emails[user_id].append({"id": msg_id, "subject": subject, "from": sender})
            result += f"*{i+1}. {subject}*\nОт: {sender}\n{date}\n_{body.strip()[:150]}_\n\n"
        
        mail.logout()
        return result
    except Exception as e:
        return f"⚠️ Ошибка чтения почты: {e}"

def delete_email(user_id, num):
    try:
        if user_id not in last_emails or num > len(last_emails[user_id]):
            return "⚠️ Письмо не найдено. Сначала проверь почту."
        
        mail_password = os.getenv("MAIL_PASSWORD")
        mail_info = last_emails[user_id][num-1]
        mail = imaplib.IMAP4_SSL(MAIL_IMAP)
        mail.login(MAIL_EMAIL, mail_password)
        mail.select("INBOX")
        mail.store(mail_info["id"], '+FLAGS', '\\Deleted')
        mail.expunge()
        mail.logout()
        return f"🗑 Письмо '{mail_info['subject']}' удалено!"
    except Exception as e:
        return f"⚠️ Ошибка удаления: {e}"

def send_email(to, subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Sergey Zhmakov <{GMAIL_EMAIL}>"
        msg["To"] = to
        msg["Reply-To"] = MAIL_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_EMAIL, GMAIL_PASSWORD.replace(" ", ""))
            server.send_message(msg)
        return f"✅ Письмо отправлено на {to}"
    except Exception as e:
        return f"⚠️ Ошибка отправки: {e}"

async def ask_claude(user_id, message, image_data=None):
    if user_id not in histories:
        histories[user_id] = []
    
    mem_str = json.dumps(memory.get(user_id, {}), ensure_ascii=False) if memory.get(user_id) else "пусто"
    system = SYSTEM_PROMPT.replace("{memory}", mem_str)
    
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
        {"type": "text", "text": message or "Что на фото?"}
    ] if image_data else message
    
    histories[user_id].append({"role": "user", "content": content})
    if len(histories[user_id]) > 30:
        histories[user_id] = histories[user_id][-30:]
    
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1500, "system": system, "messages": histories[user_id]})
        reply = r.json()["content"][0]["text"]
        histories[user_id].append({"role": "assistant", "content": reply})
        return reply

async def process_commands(reply, update, ctx, uid):
    handled = False
    clean = reply
    
    if "[EMAIL_CHECK]" in reply:
        clean = clean.replace("[EMAIL_CHECK]", "").strip()
        emails = get_emails(uid)
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(emails, parse_mode="Markdown")
        handled = True
    
    if "[EMAIL_SEND:" in reply:
        try:
            cmd = reply.split("[EMAIL_SEND:")[1].split("]")[0]
            parts = cmd.split(":", 2)
            if len(parts) == 3:
                result = send_email(parts[0].strip(), parts[1].strip(), parts[2].strip())
                clean = reply.split("[EMAIL_SEND:")[0].strip()
                if clean: await update.message.reply_text(clean)
                await update.message.reply_text(result)
                handled = True
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
            handled = True
    
    if "[EMAIL_DELETE:" in reply:
        try:
            num = int(reply.split("[EMAIL_DELETE:")[1].split("]")[0])
            result = delete_email(uid, num)
            clean = reply.split("[EMAIL_DELETE:")[0].strip()
            if clean: await update.message.reply_text(clean)
            await update.message.reply_text(result)
            handled = True
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
            handled = True
    
    if "[MEMORY_SAVE:" in reply:
        try:
            cmd = reply.split("[MEMORY_SAVE:")[1].split("]")[0]
            key, value = cmd.split(":", 1)
            if uid not in memory: memory[uid] = {}
            memory[uid][key.strip()] = value.strip()
            clean = reply.split("[MEMORY_SAVE:")[0].strip()
            if clean: await update.message.reply_text(clean)
            await update.message.reply_text(f"💾 Запомнила: {key.strip()} = {value.strip()}")
            handled = True
        except Exception as e:
            logger.error(f"Memory save error: {e}")
    
    return handled, clean

async def transcribe(voice_bytes):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": ("audio.mp3", voice_bytes, "audio/mpeg")},
            data={"model": "whisper-1"})
        return r.json().get("text", "")

async def tts(text):
    clean = re.sub(r'[\*\_\`\#\[\]]', '', text)
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
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔤 Текст", callback_data="mode_text"),
        InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")
    ]])
    await update.message.reply_text(
        "👋 Привет, Сергей! Я *Lilu* — твой личный ассистент.\n\n"
        "📧 Почта: читаю, пишу, удаляю\n"
        "🧠 Память: запоминаю важное\n"
        "🎤 Голос: говори — отвечу голосом\n\n"
        "Просто скажи что нужно!",
        parse_mode="Markdown", reply_markup=kb)

async def set_mode(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    voice_mode[uid] = q.data == "mode_voice"
    mode = "🎤 Голосовой" if voice_mode[uid] else "🔤 Текстовый"
    await q.edit_message_text(f"*{mode} режим включён!*", parse_mode="Markdown")

async def mode_cmd(update, ctx):
    uid = update.effective_user.id
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔤 Текст", callback_data="mode_text"), InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")]])
    cur = "🎤 Голосовой" if voice_mode.get(uid) else "🔤 Текстовый"
    await update.message.reply_text(f"Режим: *{cur}*", parse_mode="Markdown", reply_markup=kb)

async def clear(update, ctx):
    uid = update.effective_user.id
    histories.pop(uid, None)
    await update.message.reply_text("🧹 История очищена! Память сохранена.")

async def remember_cmd(update, ctx):
    uid = update.effective_user.id
    mem = memory.get(uid, {})
    if not mem:
        await update.message.reply_text("🧠 Память пуста")
        return
    text = "🧠 *Что я помню о тебе:*\n\n"
    for k, v in mem.items():
        text += f"• *{k}*: {v}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def mail_cmd(update, ctx):
    if not is_owner(update.effective_user.id): return
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    await update.message.reply_text(get_emails(update.effective_user.id), parse_mode="Markdown")

async def handle_text(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid): return
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        reply = await ask_claude(uid, update.message.text)
        handled, clean = await process_commands(reply, update, ctx, uid)
        if not handled:
            await send_reply(update, ctx, reply, uid)
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

async def handle_voice(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid): return
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        f = await ctx.bot.get_file(update.message.voice.file_id)
        async with httpx.AsyncClient() as client:
            voice_bytes = (await client.get(f.file_path)).content
        text = await transcribe(voice_bytes)
        if not text.strip():
            await update.message.reply_text("🤔 Не расслышала, повтори?")
            return
        await update.message.reply_text(f"🎤 _{text}_", parse_mode="Markdown")
        reply = await ask_claude(uid, text)
        handled, clean = await process_commands(reply, update, ctx, uid)
        if not handled:
            try:
                audio = await tts(reply)
                await update.message.reply_audio(audio, filename="lilu.mp3")
            except:
                await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

async def handle_photo(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid): return
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        f = await ctx.bot.get_file(update.message.photo[-1].file_id)
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
    app.add_handler(CommandHandler("mail", mail_cmd))
    app.add_handler(CommandHandler("memory", remember_cmd))
    app.add_handler(CallbackQueryHandler(set_mode, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Lilu v2 запущена!")
    app.run_polling()

if __name__ == "__main__":
    main()
