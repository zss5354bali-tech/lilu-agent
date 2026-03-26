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
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "zss5354bali@gmail.com")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "").replace(" ", "")

IMAP_SERVER = "imap.mail.ru"

# Per-user state
histories = {}    # chat history
voice_mode = {}   # voice/text mode
memory = {}       # permanent memory
last_emails = {}  # last fetched emails for deletion

SYSTEM_PROMPT = """Ты Lilu — персональный AI-ассистент Сергея Сергеевича Жмакова.

СТИЛЬ:
- Обращайся ТОЛЬКО "Сергей Сергеевич"
- Деловой, чёткий тон. Без "солнышко", "дорогой", лишних эмоций
- В голосовых ответах — максимум 2-3 предложения
- Ты помнишь весь контекст разговора — используй его!

ВАЖНО ПРО КОНТЕКСТ:
- Если в предыдущих сообщениях был найден email адрес — используй его
- Если уже обсуждался какой-то контакт — помни об этом
- Не теряй информацию из предыдущих сообщений

ВОЗМОЖНОСТИ:
- Отвечаешь на вопросы, ищешь информацию
- Пишешь и переводишь тексты, деловые письма
- Полностью управляешь почтой alfa-sz@mail.ru
- Запоминаешь важную информацию навсегда
- Анализируешь фото и документы

ПОЧТОВЫЕ КОМАНДЫ (вставляй ТОЛЬКО команду без лишнего текста вокруг неё):
[EMAIL_CHECK] — проверить новые письма
[EMAIL_SEARCH:запрос] — найти письма по отправителю или теме
[EMAIL_DELETE_FROM:отправитель] — удалить ВСЕ письма от отправителя
[EMAIL_SEND:адрес@mail.com:Тема:Текст письма] — отправить письмо
[EMAIL_DELETE:номер] — удалить письмо по номеру из списка
[MEMORY_SAVE:ключ:значение] — сохранить важную информацию

ПАМЯТЬ О СЕРГЕЕ СЕРГЕЕВИЧЕ:
{memory}

СПРАВКА:
- Гражданин России, живёт на Бали (Индонезия), инвесторский КИТАС
- Платформа AkuMau — маркетплейс товаров и услуг на Бали
- Почта: alfa-sz@mail.ru (основная)
- Gmail: zss5354bali@gmail.com (для отправки)"""

class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    def handle_data(self, d):
        self.text.append(d)
    def get_text(self):
        return ' '.join(self.text).strip()

def strip_html(text):
    if not text or '<' not in text:
        return text or ""
    try:
        s = HTMLStripper()
        s.feed(text)
        return s.get_text()
    except:
        return re.sub(r'<[^>]+>', ' ', text)

def decode_str(s):
    if not s: return ""
    result = ""
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="ignore")
        else:
            result += str(part)
    return result

def get_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    break
                except: pass
            elif ct == "text/html" and not body:
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
    return re.sub(r'\s+', ' ', body).strip()[:400]

def imap_connect():
    m = imaplib.IMAP4_SSL(IMAP_SERVER)
    m.login(MAIL_EMAIL, MAIL_PASSWORD)
    m.select("INBOX")
    return m

def get_emails(uid, limit=5):
    try:
        m = imap_connect()
        _, data = m.search(None, "UNSEEN")
        ids = data[0].split()
        if not ids:
            m.logout()
            return "📭 Новых писем нет."
        result = f"📬 Новых писем: {len(ids)}\n\n"
        last_emails[uid] = []
        for i, mid in enumerate(ids[-limit:]):
            _, md = m.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(md[0][1])
            subj = decode_str(msg.get("Subject", "Без темы"))
            frm = decode_str(msg.get("From", ""))
            date = msg.get("Date", "")[:16]
            body = get_body(msg)
            last_emails[uid].append({"id": mid, "subject": subj, "from": frm})
            result += f"{i+1}. *{subj}*\nОт: {frm}\n{date}\n{body}\n\n"
        m.logout()
        return result
    except Exception as e:
        return f"⚠️ Ошибка чтения: {e}"

def search_emails(uid, query, limit=5):
    try:
        m = imap_connect()
        _, data = m.search(None, f'FROM "{query}"')
        ids = data[0].split()
        if not ids:
            _, data = m.search(None, f'SUBJECT "{query}"')
            ids = data[0].split()
        if not ids:
            m.logout()
            return f"📭 Писем по запросу '{query}' не найдено."
        result = f"🔍 Найдено: {len(ids)} писем по '{query}'\n\n"
        last_emails[uid] = []
        for i, mid in enumerate(ids[-limit:]):
            _, md = m.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(md[0][1])
            subj = decode_str(msg.get("Subject", "Без темы"))
            frm = decode_str(msg.get("From", ""))
            last_emails[uid].append({"id": mid, "subject": subj, "from": frm})
            result += f"{i+1}. *{subj}*\nОт: {frm}\n\n"
        m.logout()
        return result
    except Exception as e:
        return f"⚠️ Ошибка поиска: {e}"

def delete_from(sender):
    try:
        m = imap_connect()
        _, data = m.search(None, f'FROM "{sender}"')
        ids = data[0].split()
        if not ids:
            m.logout()
            return f"📭 Писем от '{sender}' не найдено."
        for mid in ids:
            m.store(mid, '+FLAGS', '\\Deleted')
        m.expunge()
        m.logout()
        return f"🗑 Удалено {len(ids)} писем от '{sender}'."
    except Exception as e:
        return f"⚠️ Ошибка удаления: {e}"

def delete_by_num(uid, num):
    try:
        if uid not in last_emails or num < 1 or num > len(last_emails[uid]):
            return "⚠️ Письмо не найдено. Сначала проверьте почту."
        info = last_emails[uid][num-1]
        m = imap_connect()
        m.store(info["id"], '+FLAGS', '\\Deleted')
        m.expunge()
        m.logout()
        return f"🗑 Удалено: '{info['subject']}'"
    except Exception as e:
        return f"⚠️ Ошибка: {e}"

def send_email(to, subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Sergey Zhmakov <{GMAIL_EMAIL}>"
        msg["To"] = to.strip()
        msg["Reply-To"] = MAIL_EMAIL
        msg["Subject"] = subject.strip()
        msg.attach(MIMEText(body.strip(), "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_EMAIL, GMAIL_PASSWORD)
            s.send_message(msg)
        return f"✅ Письмо отправлено на {to}"
    except Exception as e:
        return f"⚠️ Ошибка отправки: {e}"

async def ask_claude(uid, message, image_data=None):
    if uid not in histories:
        histories[uid] = []
    mem_str = json.dumps(memory.get(uid, {}), ensure_ascii=False) if memory.get(uid) else "пусто"
    system = SYSTEM_PROMPT.replace("{memory}", mem_str)
    if image_data:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": message or "Что на фото?"}
        ]
    else:
        content = message
    histories[uid].append({"role": "user", "content": content})
    if len(histories[uid]) > 40:
        histories[uid] = histories[uid][-40:]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1500, "system": system, "messages": histories[uid]}
        )
        reply = r.json()["content"][0]["text"]
        histories[uid].append({"role": "assistant", "content": reply})
        return reply

async def process_commands(reply, update, uid):
    handled = False

    if "[EMAIL_CHECK]" in reply:
        clean = reply.replace("[EMAIL_CHECK]", "").strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(get_emails(uid), parse_mode="Markdown")
        handled = True

    elif "[EMAIL_DELETE_FROM:" in reply:
        sender = reply.split("[EMAIL_DELETE_FROM:")[1].split("]")[0].strip()
        clean = reply.split("[EMAIL_DELETE_FROM:")[0].strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(delete_from(sender))
        handled = True

    elif "[EMAIL_SEARCH:" in reply:
        query = reply.split("[EMAIL_SEARCH:")[1].split("]")[0].strip()
        clean = reply.split("[EMAIL_SEARCH:")[0].strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(search_emails(uid, query), parse_mode="Markdown")
        handled = True

    elif "[EMAIL_SEND:" in reply:
        try:
            cmd = reply.split("[EMAIL_SEND:")[1].split("]")[0]
            parts = cmd.split(":", 2)
            if len(parts) == 3:
                clean = reply.split("[EMAIL_SEND:")[0].strip()
                if clean: await update.message.reply_text(clean)
                await update.message.reply_text(send_email(parts[0], parts[1], parts[2]))
                handled = True
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
            handled = True

    elif "[EMAIL_DELETE:" in reply:
        try:
            num = int(reply.split("[EMAIL_DELETE:")[1].split("]")[0])
            clean = reply.split("[EMAIL_DELETE:")[0].strip()
            if clean: await update.message.reply_text(clean)
            await update.message.reply_text(delete_by_num(uid, num))
            handled = True
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
            handled = True

    if "[MEMORY_SAVE:" in reply:
        try:
            cmd = reply.split("[MEMORY_SAVE:")[1].split("]")[0]
            k, v = cmd.split(":", 1)
            if uid not in memory: memory[uid] = {}
            memory[uid][k.strip()] = v.strip()
            await update.message.reply_text(f"💾 Запомнила: {k.strip()}")
        except Exception as e:
            logger.error(f"Memory: {e}")

    return handled

async def transcribe(voice_bytes):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": ("audio.mp3", voice_bytes, "audio/mpeg")},
            data={"model": "whisper-1", "language": "ru"}
        )
        return r.json().get("text", "")

async def tts(text):
    clean = re.sub(r'[\*\_\`\#\[\]]', '', text)[:4096]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": "tts-1", "input": clean, "voice": "nova", "response_format": "mp3"}
        )
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
            logger.error(f"TTS error: {e}")
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
        "Здравствуйте, Сергей Сергеевич. Я Lilu, ваш персональный ассистент.\n\n"
        "Готова к работе. Чем могу помочь?",
        reply_markup=kb
    )

async def set_mode(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    voice_mode[uid] = q.data == "mode_voice"
    mode = "голосовой" if voice_mode[uid] else "текстовый"
    await q.edit_message_text(f"Режим изменён на {mode}.")

async def mode_cmd(update, ctx):
    uid = update.effective_user.id
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔤 Текст", callback_data="mode_text"),
        InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")
    ]])
    cur = "голосовой" if voice_mode.get(uid) else "текстовый"
    await update.message.reply_text(f"Текущий режим: {cur}.", reply_markup=kb)

async def clear_cmd(update, ctx):
    histories.pop(update.effective_user.id, None)
    await update.message.reply_text("История разговора очищена.")

async def memory_cmd(update, ctx):
    uid = update.effective_user.id
    mem = memory.get(uid, {})
    if not mem:
        await update.message.reply_text("Память пуста.")
        return
    text = "Сохранено в памяти:\n\n" + "\n".join(f"• {k}: {v}" for k, v in mem.items())
    await update.message.reply_text(text)

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
        handled = await process_commands(reply, update, uid)
        if not handled:
            await send_reply(update, ctx, reply, uid)
    except Exception as e:
        logger.error(f"Text error: {e}")
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
            await update.message.reply_text("Не расслышала. Повторите, пожалуйста.")
            return
        await update.message.reply_text(f"🎤 {text}")
        reply = await ask_claude(uid, text)
        handled = await process_commands(reply, update, uid)
        if not handled:
            try:
                audio = await tts(reply)
                await update.message.reply_audio(audio, filename="lilu.mp3")
            except:
                await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Voice error: {e}")
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
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("mail", mail_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(set_mode, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Lilu запущена.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
