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
from html.parser import HTMLParser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MAIL_EMAIL = os.getenv("MAIL_EMAIL", "alfa-sz@mail.ru")
MAIL_IMAP = "imap.mail.ru"
GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "zss5354bali@gmail.com")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")

histories = {}
voice_mode = {}
memory = {}
last_emails = {}

SYSTEM_PROMPT = """Ты Lilu — персональный AI-ассистент Сергея Сергеевича Жмакова.

СТИЛЬ ОБЩЕНИЯ:
- Всегда обращайся "Сергей Сергеевич" — никаких "солнышко", "дорогой", "привет дружище"
- Деловой, чёткий, профессиональный тон
- Без лишних эмоций и восклицаний
- Отвечай по делу, кратко и точно
- В голосовых ответах — максимум 2-3 предложения


ВОЗМОЖНОСТИ:
- Отвечаешь на вопросы, ищешь информацию
- Пишешь тексты, посты, переводишь
- Управляешь почтой alfa-sz@mail.ru
- Запоминаешь важную информацию
- Анализируешь фото

ПОЧТОВЫЕ КОМАНДЫ (вставляй в ответ когда нужно):
[EMAIL_CHECK] — проверить 5 новых писем
[EMAIL_SEARCH:от кого] — найти письма от отправителя (например linkedin)
[EMAIL_DELETE_FROM:от кого] — удалить ВСЕ письма от отправителя
[EMAIL_SEND:кому@mail.com:Тема:Текст] — отправить письмо
[EMAIL_DELETE:номер] — удалить письмо по номеру
[MEMORY_SAVE:ключ:значение] — запомнить информацию

ПАМЯТЬ:
{memory}

Сергей Сергеевич — гражданин России, живёт на Бали (Индонезия), инвесторский КИТАС.
Владелец платформы AkuMau — маркетплейс товаров и услуг на Бали.
Почта: alfa-sz@mail.ru

ПАМЯТЬ О СЕРГЕЕ СЕРГЕЕВИЧЕ:
{memory}"""

class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    def handle_data(self, data):
        self.text.append(data)
    def get_text(self):
        return ' '.join(self.text).strip()

def strip_html(text):
    if not text: return ""
    if '<' not in text: return text
    s = HTMLStripper()
    try:
        s.feed(text)
        return s.get_text()[:300]
    except:
        return re.sub(r'<[^>]+>', ' ', text)[:300]

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

def get_mail_connection():
    mail_password = os.getenv("MAIL_PASSWORD")
    mail = imaplib.IMAP4_SSL(MAIL_IMAP)
    mail.login(MAIL_EMAIL, mail_password)
    mail.select("INBOX")
    return mail

def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    break
                except: pass
            elif part.get_content_type() == "text/html" and not body:
                try:
                    body = strip_html(part.get_payload(decode=True).decode("utf-8", errors="ignore"))
                except: pass
    else:
        try:
            raw = msg.get_payload(decode=True)
            if raw:
                body = raw.decode("utf-8", errors="ignore")
                if '<' in body:
                    body = strip_html(body)
        except: pass
    return body[:300].strip()

def get_emails(user_id, limit=5):
    try:
        mail = get_mail_connection()
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
            sender = decode_str(msg.get("From", ""))
            date = msg.get("Date", "")[:16]
            body = get_email_body(msg)
            last_emails[user_id].append({"id": msg_id, "subject": subject, "from": sender})
            result += f"*{i+1}. {subject}*\nОт: {sender}\n{date}\n_{body}_\n\n"
        mail.logout()
        return result
    except Exception as e:
        return f"⚠️ Ошибка чтения почты: {e}"

def search_emails(user_id, sender_query):
    try:
        mail = get_mail_connection()
        search_term = sender_query.upper().encode("utf-8")
        _, data = mail.search(None, f'FROM "{sender_query}"')
        ids = data[0].split()
        if not ids:
            mail.logout()
            return f"📭 Писем от '{sender_query}' не найдено", []
        result = f"🔍 Найдено писем от *{sender_query}*: {len(ids)}\n\n"
        last_emails[user_id] = []
        for i, msg_id in enumerate(ids[-5:]):
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_str(msg.get("Subject", "Без темы"))
            sender = decode_str(msg.get("From", ""))
            last_emails[user_id].append({"id": msg_id, "subject": subject, "from": sender})
            result += f"*{i+1}. {subject}*\nОт: {sender}\n\n"
        mail.logout()
        return result, ids
    except Exception as e:
        return f"⚠️ Ошибка поиска: {e}", []

def delete_emails_from(sender_query):
    try:
        mail = get_mail_connection()
        _, data = mail.search(None, f'FROM "{sender_query}"')
        ids = data[0].split()
        if not ids:
            mail.logout()
            return f"📭 Писем от '{sender_query}' не найдено"
        count = len(ids)
        for msg_id in ids:
            mail.store(msg_id, '+FLAGS', '\\Deleted')
        mail.expunge()
        mail.logout()
        return f"🗑 Удалено {count} писем от '{sender_query}'!"
    except Exception as e:
        return f"⚠️ Ошибка удаления: {e}"

def delete_email_by_num(user_id, num):
    try:
        if user_id not in last_emails or num > len(last_emails[user_id]):
            return "⚠️ Письмо не найдено. Сначала проверь почту."
        mail_info = last_emails[user_id][num-1]
        mail = get_mail_connection()
        mail.store(mail_info["id"], '+FLAGS', '\\Deleted')
        mail.expunge()
        mail.logout()
        return f"🗑 Письмо '{mail_info['subject']}' удалено!"
    except Exception as e:
        return f"⚠️ Ошибка: {e}"

def send_email(to, subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Sergey <{GMAIL_EMAIL}>"
        msg["To"] = to
        msg["Reply-To"] = MAIL_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        pwd = GMAIL_PASSWORD.replace(" ", "")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_EMAIL, pwd)
            server.send_message(msg)
        return f"✅ Письмо отправлено на {to}"
    except Exception as e:
        return f"⚠️ Ошибка отправки: {e}"

async def ask_claude(user_id, message, image_data=None):
    if user_id not in histories:
        histories[user_id] = []
    mem_str = json.dumps(memory.get(user_id, {}), ensure_ascii=False) if memory.get(user_id) else "пусто"
    system = SYSTEM_PROMPT.replace("{memory}", mem_str)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}}, {"type": "text", "text": message or "Что на фото?"}] if image_data else message
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
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(get_emails(uid), parse_mode="Markdown")
        handled = True

    elif "[EMAIL_DELETE_FROM:" in reply:
        sender = reply.split("[EMAIL_DELETE_FROM:")[1].split("]")[0].strip()
        result = delete_emails_from(sender)
        clean = reply.split("[EMAIL_DELETE_FROM:")[0].strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(result)
        handled = True

    elif "[EMAIL_SEARCH:" in reply:
        sender = reply.split("[EMAIL_SEARCH:")[1].split("]")[0].strip()
        result, _ = search_emails(uid, sender)
        clean = reply.split("[EMAIL_SEARCH:")[0].strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(result, parse_mode="Markdown")
        handled = True

    elif "[EMAIL_SEND:" in reply:
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

    elif "[EMAIL_DELETE:" in reply:
        try:
            num = int(reply.split("[EMAIL_DELETE:")[1].split("]")[0])
            result = delete_email_by_num(uid, num)
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
            await update.message.reply_text(f"💾 Запомнила: {key.strip()}")
        except Exception as e:
            logger.error(f"Memory: {e}")

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
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔤 Текст", callback_data="mode_text"), InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")]])
    await update.message.reply_text("👋 Привет, Сергей! Я *Lilu*.\n\n📧 Читаю, пишу, удаляю письма\n🔍 Ищу письма по отправителю\n🧠 Запоминаю важное\n🎤 Говорю голосом\n\nПросто скажи что нужно!", parse_mode="Markdown", reply_markup=kb)

async def set_mode(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    voice_mode[uid] = q.data == "mode_voice"
    await q.edit_message_text(f"*{'🎤 Голосовой' if voice_mode[uid] else '🔤 Текстовый'} режим!*", parse_mode="Markdown")

async def mode_cmd(update, ctx):
    uid = update.effective_user.id
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔤 Текст", callback_data="mode_text"), InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")]])
    await update.message.reply_text(f"Режим: *{'🎤 Голосовой' if voice_mode.get(uid) else '🔤 Текстовый'}*", parse_mode="Markdown", reply_markup=kb)

async def clear(update, ctx):
    histories.pop(update.effective_user.id, None)
    await update.message.reply_text("🧹 История очищена!")

async def memory_cmd(update, ctx):
    uid = update.effective_user.id
    mem = memory.get(uid, {})
    if not mem:
        await update.message.reply_text("🧠 Память пуста")
        return
    text = "🧠 *Помню о тебе:*\n\n" + "\n".join(f"• *{k}*: {v}" for k, v in mem.items())
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
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(set_mode, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Lilu v3 запущена!")
    app.run_polling()

if __name__ == "__main__":
    main()
