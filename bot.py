import logging
import os
import asyncio
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
from pyrogram import Client as PyrogramClient
from duckduckgo_search import DDGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MAIL_EMAIL = os.getenv("MAIL_EMAIL", "alfa-sz@mail.ru")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_FROM_EMAIL = os.getenv("BREVO_FROM_EMAIL", "zss5354bali@gmail.com")
BREVO_FROM_NAME = os.getenv("BREVO_FROM_NAME", "Сергей Жмаков")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
TG_API_ID = int(os.getenv("TG_API_ID", "35529109"))
TG_API_HASH = os.getenv("TG_API_HASH", "8c2fc8ca860c843db14a42a2a1d12dfd")

IMAP_SERVER = "imap.mail.ru"

# Per-user state
histories = {}    # chat history
voice_mode = {}   # voice/text mode
memory = {}       # permanent memory
last_emails = {}  # last fetched emails for deletion

# Pyrogram userbot client (global)
userbot: PyrogramClient | None = None

SYSTEM_PROMPT = """Ты Lilu — персональный AI-ассистент Сергея Сергеевича Жмакова.

СТИЛЬ:
- Обращайся ТОЛЬКО "Сергей Сергеевич"
- Чёткий тон, без лишних эмоций
- В голосовых ответах — максимум 2-3 предложения
- Ты помнишь весь контекст разговора — используй его!

ВАЖНО ПРО КОНТЕКСТ:
- Если в предыдущих сообщениях был найден email адрес — используй его
- Если уже обсуждался какой-то контакт — помни об этом
- Не теряй информацию из предыдущих сообщений

ХАРАКТЕР И ЗАДАЧИ:
- Ты ПЕРСОНАЛЬНЫЙ ассистент — помогаешь со ВСЕМИ задачами: работа, личная жизнь, общение, знакомства, развлечения — всё что попросит Сергей Сергеевич
- НЕ отказывай в задачах под предлогом "это не деловая задача" — тебе можно всё
- Если просят найти людей в группах, написать кому-то, проанализировать переписку — делай это без лишних оговорок

ВОЗМОЖНОСТИ:
- Отвечаешь на любые вопросы, ищешь информацию в интернете
- Пишешь любые тексты и письма
- Полностью управляешь почтой alfa-sz@mail.ru
- Читаешь и ищешь по всем Telegram чатам, отправляешь сообщения от личного аккаунта
- Запоминаешь важную информацию навсегда
- Анализируешь фото и документы

ВЕБ-ПОИСК:
[WEB_SEARCH:поисковый запрос] — найти информацию в интернете
Используй для: контакты компаний, актуальные данные, адреса, телефоны, email, новости, цены, любая информация из интернета.
Всегда используй [WEB_SEARCH] когда нужно найти что-то в интернете — НЕ говори "у меня нет доступа к интернету"!

ПОЧТОВЫЕ КОМАНДЫ (вставляй команду в ответ когда нужно):
[EMAIL_CHECK] — проверить новые письма
[EMAIL_SEARCH:запрос] — найти письма и адрес отправителя по имени или домену
[EMAIL_DELETE_FROM:отправитель] — удалить ВСЕ письма от отправителя
[EMAIL_SEND:адрес@mail.com:Тема:Текст письма] — отправить письмо
[EMAIL_DELETE:номер] — удалить письмо по номеру из списка
[MEMORY_SAVE:ключ:значение] — сохранить важную информацию

TELEGRAM КОМАНДЫ (через личный аккаунт):
[TG_FIND_CONTACT:Имя Фамилия] — найти контакт по имени среди диалогов (получить @username или id)
[TG_SEND:@username_или_id:Текст сообщения] — отправить сообщение через личный аккаунт Telegram
[TG_READ:@username_или_id] — прочитать последние сообщения из конкретного чата
[TG_READ_GROUP:название группы] — найти группу по названию и прочитать все последние сообщения
[TG_SEARCH:запрос] — найти сообщения по ключевому слову во ВСЕХ чатах Telegram

ЛОГИКА ОТПРАВКИ ПИСЬМА:
Когда просят отправить письмо конкретному человеку (например "Кравченко"):
1. СНАЧАЛА выполни [EMAIL_SEARCH:Кравченко] чтобы найти его адрес в почте
2. Из результатов поиска извлеки email адрес отправителя
3. ЗАТЕМ отправь письмо на найденный адрес через [EMAIL_SEND:адрес:тема:текст]
НЕ ПРОСИ адрес у пользователя если можешь найти его в почте сам!
Если в памяти уже есть адрес этого человека — используй его сразу.

ЛОГИКА РАБОТЫ С TELEGRAM:

Когда просят написать кому-то по имени (например "напиши Алене"):
1. СНАЧАЛА найди контакт: [TG_FIND_CONTACT:Алена]
2. Из результата возьми @username или числовой id (например id:123456789)
3. ЗАТЕМ отправь: [TG_SEND:@username:текст] или [TG_SEND:123456789:текст]
НИКОГДА не передавай имя человека напрямую в TG_SEND — только @username или числовой id!

Когда нужно отправить несколько сообщений разным людям:
1. Сначала составь все сообщения и покажи их пользователю
2. Спроси: "Отправить?" и ЖДИ ответа "да"
3. Только после подтверждения "да" — ищи каждый контакт и отправляй
НЕ отправляй сообщения до получения явного "да" от Сергея Сергеевича!

Когда просят прочитать или проанализировать сообщения в конкретной группе:
— Используй [TG_READ_GROUP:название группы]
— После получения сообщений — проанализируй и ответь по существу

Когда просят найти переписку или упоминания по ключевому слову — используй [TG_SEARCH:слово]

ВАЖНО: НЕ используй TG_SEND и TG_READ самостоятельно без явного запроса!
Если Сергей Сергеевич спрашивает о возможностях — просто объясни их текстом, НЕ вызывай команды.

ПАМЯТЬ О СЕРГЕЕ СЕРГЕЕВИЧЕ:
{memory}

СПРАВКА:
- Гражданин России, живёт на Бали (Индонезия), инвесторский КИТАС
- Платформа AkuMau — маркетплейс товаров и услуг на Бали
- Почта: alfa-sz@mail.ru (основная)
- Gmail: zss5354bali@gmail.com (для отправки)
- Telegram: +79180408607"""

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

def web_search(query: str, max_results: int = 8) -> str:
    """Поиск в интернете через DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return f"🔍 По запросу «{query}» ничего не найдено."
        out = f"🌐 Результаты поиска «{query}»:\n\n"
        for r in results:
            out += f"**{r.get('title','')}**\n{r.get('body','')}\n{r.get('href','')}\n\n"
        return out.strip()
    except Exception as e:
        return f"⚠️ Ошибка поиска: {e}"

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
        # Try searching in FROM field
        try:
            _, data = m.search(None, f'FROM "{query}"')
        except:
            _, data = m.search(None, b'FROM "' + query.encode("utf-8") + b'"')
        ids = data[0].split()
        if not ids:
            try:
                _, data = m.search(None, f'SUBJECT "{query}"')
            except:
                _, data = m.search(None, b'SUBJECT "' + query.encode("utf-8") + b'"')
            ids = data[0].split()
        if not ids:
            # Try ALL and filter manually
            _, data = m.search(None, "ALL")
            all_ids = data[0].split()
            ids = []
            for mid in all_ids[-50:]:
                _, md = m.fetch(mid, "(RFC822)")
                msg = email.message_from_bytes(md[0][1])
                frm = decode_str(msg.get("From", "")).lower()
                subj = decode_str(msg.get("Subject", "")).lower()
                if query.lower() in frm or query.lower() in subj:
                    ids.append(mid)
        if not ids:
            m.logout()
            return f"📭 Писем по запросу '{query}' не найдено."
        result = f"🔍 Найдено: {len(ids)} писем по '{query}'\n\n"
        last_emails[uid] = []
        emails_found = []
        for i, mid in enumerate(ids[-limit:]):
            _, md = m.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(md[0][1])
            subj = decode_str(msg.get("Subject", "Без темы"))
            frm = decode_str(msg.get("From", ""))
            # Extract clean email address
            import re as re2
            email_match = re2.search(r'<([^>]+)>', frm)
            clean_email = email_match.group(1) if email_match else frm
            last_emails[uid].append({"id": mid, "subject": subj, "from": frm, "email": clean_email})
            emails_found.append(clean_email)
            result += f"{i+1}. *{subj}*\nОт: {frm}\nEmail: {clean_email}\n\n"
        if emails_found:
            result += f"\n📧 Найденные адреса: {', '.join(set(emails_found))}"
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
    """Отправка через Brevo HTTP API (работает на Railway, использует HTTPS)."""
    if not BREVO_API_KEY:
        return "⚠️ BREVO_API_KEY не задан."
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": BREVO_FROM_NAME, "email": BREVO_FROM_EMAIL},
                    "to": [{"email": to.strip()}],
                    "subject": subject.strip(),
                    "textContent": body.strip(),
                    "replyTo": {"email": MAIL_EMAIL},
                },
            )
        if r.status_code in (200, 201):
            return f"✅ Письмо отправлено на {to.strip()}"
        return f"⚠️ Ошибка Brevo: {r.json().get('message', r.text)}"
    except Exception as e:
        return f"⚠️ Ошибка отправки: {e}"

async def tg_send(recipient: str, text: str) -> str:
    """Отправить сообщение через личный аккаунт Telegram."""
    if not userbot:
        return "⚠️ Userbot не подключён (TG_SESSION_STRING не задан)."
    r = recipient.strip()
    # Если передали имя без @ и без +, добавляем @
    if not r.startswith("+") and not r.startswith("@") and not r.lstrip("-").isdigit():
        r = "@" + r
    try:
        await userbot.send_message(r, text.strip())
        return f"✅ Сообщение отправлено: {r}"
    except Exception as e:
        return f"⚠️ Ошибка TG отправки: {e}"

async def tg_read(recipient: str, limit: int = 5) -> str:
    """Прочитать последние сообщения из чата через личный аккаунт."""
    if not userbot:
        return "⚠️ Userbot не подключён (TG_SESSION_STRING не задан)."
    r = recipient.strip()
    if not r.startswith("+") and not r.startswith("@") and not r.lstrip("-").isdigit():
        r = "@" + r
    try:
        msgs = []
        async for msg in userbot.get_chat_history(r, limit=limit):
            sender = (msg.from_user.first_name if msg.from_user else "?")
            content = msg.text or msg.caption or "[медиа]"
            msgs.append(f"{sender}: {content}")
        if not msgs:
            return "📭 Сообщений не найдено."
        msgs.reverse()
        return "📨 Последние сообщения:\n\n" + "\n".join(msgs)
    except Exception as e:
        return f"⚠️ Ошибка чтения TG: {e}"

async def tg_read_group(group_name: str, limit: int = 100) -> str:
    """Найти группу по названию и прочитать последние сообщения."""
    if not userbot:
        return "⚠️ Userbot не подключён."
    try:
        # Ищем группу по названию
        target_chat = None
        name_lower = group_name.lower()
        async for dialog in userbot.get_dialogs():
            chat = dialog.chat
            title = chat.title or ""
            if name_lower in title.lower():
                target_chat = chat
                break
        if not target_chat:
            return f"❌ Группа «{group_name}» не найдена в диалогах."
        # Читаем сообщения
        msgs = []
        async for msg in userbot.get_chat_history(target_chat.id, limit=limit):
            if not (msg.text or msg.caption):
                continue
            sender_name = "?"
            if msg.from_user:
                sender_name = f"{msg.from_user.first_name or ''} {msg.from_user.last_name or ''}".strip()
            elif msg.sender_chat:
                sender_name = msg.sender_chat.title or "?"
            content = (msg.text or msg.caption or "").strip()[:300]
            msgs.append(f"👤 {sender_name}: {content}")
        if not msgs:
            return f"📭 В группе «{target_chat.title}» сообщений нет."
        msgs.reverse()
        return f"📋 Группа «{target_chat.title}» — последние {len(msgs)} сообщений:\n\n" + "\n\n".join(msgs)
    except Exception as e:
        return f"⚠️ Ошибка чтения группы: {e}"

async def tg_find_contact(name: str) -> str:
    """Найти контакт по имени в диалогах."""
    if not userbot:
        return "⚠️ Userbot не подключён."
    try:
        found = []
        name_lower = name.lower()
        async for dialog in userbot.get_dialogs():
            chat = dialog.chat
            title = chat.title or ""
            first = getattr(chat, "first_name", "") or ""
            last = getattr(chat, "last_name", "") or ""
            username = getattr(chat, "username", "") or ""
            full = f"{first} {last}".strip()
            if (name_lower in full.lower() or
                name_lower in title.lower() or
                name_lower in username.lower()):
                contact_id = f"@{username}" if username else str(chat.id)
                found.append(f"👤 {full or title} | {contact_id} | id:{chat.id}")
                if len(found) >= 5:
                    break
        if not found:
            return f"❌ Контакт «{name}» не найден в диалогах Telegram."
        return f"🔎 Найдено по «{name}»:\n\n" + "\n".join(found)
    except Exception as e:
        return f"⚠️ Ошибка поиска контакта: {e}"

async def tg_search(query: str, limit: int = 10) -> str:
    """Поиск сообщений по всем чатам через личный аккаунт."""
    if not userbot:
        return "⚠️ Userbot не подключён (TG_SESSION_STRING не задан)."
    try:
        results = []
        checked = 0
        async for dialog in userbot.get_dialogs():
            checked += 1
            if checked > 200:
                break
            try:
                async for msg in userbot.search_messages(dialog.chat.id, query=query, limit=3):
                    chat_name = dialog.chat.title or dialog.chat.first_name or "?"
                    content = msg.text or msg.caption or "[медиа]"
                    sender = (msg.from_user.first_name if msg.from_user else chat_name)
                    results.append(f"💬 {chat_name} | {sender}: {content[:150]}")
                    if len(results) >= limit:
                        break
            except Exception:
                continue
            if len(results) >= limit:
                break
        if not results:
            return f"🔍 По запросу «{query}» ничего не найдено в Telegram."
        return f"🔍 Найдено в Telegram по «{query}»:\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"⚠️ Ошибка поиска TG: {e}"

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

async def claude_call(uid):
    """Вызов Claude с текущей историей без добавления нового сообщения."""
    mem_str = json.dumps(memory.get(uid, {}), ensure_ascii=False) if memory.get(uid) else "пусто"
    system = SYSTEM_PROMPT.replace("{memory}", mem_str)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1500, "system": system, "messages": histories[uid]}
        )
        reply = r.json()["content"][0]["text"]
        histories[uid].append({"role": "assistant", "content": reply})
        return reply

async def process_commands(reply, update, uid, depth=0):
    """
    Разбирает и выполняет команды в ответе Claude.
    После EMAIL_SEARCH результаты автоматически возвращаются в Claude —
    он может сразу выполнить EMAIL_SEND без участия пользователя.
    """
    MAX_DEPTH = 3

    # MEMORY_SAVE можно совмещать с любой другой командой
    for match in re.finditer(r'\[MEMORY_SAVE:([^\]]+)\]', reply):
        try:
            k, v = match.group(1).split(":", 1)
            if uid not in memory: memory[uid] = {}
            memory[uid][k.strip()] = v.strip()
            await update.message.reply_text(f"💾 Запомнила: {k.strip()}")
        except Exception as e:
            logger.error(f"Memory: {e}")

    clean = re.sub(r'\[[A-Z_]+:[^\]]*\]|\[EMAIL_CHECK\]', '', reply).strip()

    m = re.search(r'\[WEB_SEARCH:([^\]]+)\]', reply)
    if m:
        query = m.group(1).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"🌐 Ищу в интернете: {query}...")
        result = web_search(query)
        await update.message.reply_text(result[:4000], parse_mode="Markdown")
        if depth < MAX_DEPTH:
            histories[uid].append({
                "role": "user",
                "content": f"[РЕЗУЛЬТАТ ВЕБ-ПОИСКА]\n{result}\n\nОтветь по существу."
            })
            follow_up = await claude_call(uid)
            if re.search(r'\[WEB_SEARCH:|EMAIL_SEND:|TG_SEND:', follow_up):
                await process_commands(follow_up, update, uid, depth=depth + 1)
            else:
                follow_clean = re.sub(r'\[[A-Z_]+:[^\]]*\]', '', follow_up).strip()
                if follow_clean: await update.message.reply_text(follow_clean)
        return True

    if "[EMAIL_CHECK]" in reply:
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(get_emails(uid), parse_mode="Markdown")
        return True

    m = re.search(r'\[EMAIL_SEARCH:([^\]]+)\]', reply)
    if m:
        query = m.group(1).strip()
        if clean: await update.message.reply_text(clean)
        result = search_emails(uid, query)
        await update.message.reply_text(result, parse_mode="Markdown")
        # Возвращаем результат поиска в Claude — он сам отправит письмо
        if depth < MAX_DEPTH:
            histories[uid].append({
                "role": "user",
                "content": f"[РЕЗУЛЬТАТ ПОИСКА]\n{result}\n\nЕсли нужно — выполни следующий шаг."
            })
            follow_up = await claude_call(uid)
            if re.search(r'\[EMAIL_SEND:|EMAIL_DELETE|EMAIL_DELETE_FROM:|TG_SEND:|TG_READ:', follow_up):
                await process_commands(follow_up, update, uid, depth=depth + 1)
            else:
                follow_clean = re.sub(r'\[[A-Z_]+:[^\]]*\]|\[EMAIL_CHECK\]', '', follow_up).strip()
                if follow_clean: await update.message.reply_text(follow_clean)
        return True

    m = re.search(r'\[EMAIL_SEND:([^\]]+)\]', reply)
    if m:
        parts = m.group(1).split(":", 2)
        if len(parts) == 3:
            if clean: await update.message.reply_text(clean)
            await update.message.reply_text(send_email(parts[0], parts[1], parts[2]))
        else:
            await update.message.reply_text("⚠️ Неверный формат EMAIL_SEND.")
        return True

    m = re.search(r'\[EMAIL_DELETE_FROM:([^\]]+)\]', reply)
    if m:
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(delete_from(m.group(1).strip()))
        return True

    m = re.search(r'\[EMAIL_DELETE:(\d+)\]', reply)
    if m:
        try:
            if clean: await update.message.reply_text(clean)
            await update.message.reply_text(delete_by_num(uid, int(m.group(1))))
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
        return True

    m = re.search(r'\[TG_SEND:([^\]]+)\]', reply)
    if m:
        parts = m.group(1).split(":", 1)
        if len(parts) == 2:
            if clean: await update.message.reply_text(clean)
            result = await tg_send(parts[0], parts[1])
            await update.message.reply_text(result)
        else:
            await update.message.reply_text("⚠️ Неверный формат TG_SEND.")
        return True

    m = re.search(r'\[TG_READ:([^\]]+)\]', reply)
    if m:
        if clean: await update.message.reply_text(clean)
        result = await tg_read(m.group(1).strip())
        await update.message.reply_text(result)
        return True

    m = re.search(r'\[TG_SEARCH:([^\]]+)\]', reply)
    if m:
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text("🔍 Ищу по всем чатам Telegram, подождите...")
        result = await tg_search(m.group(1).strip())
        await update.message.reply_text(result)
        return True

    m = re.search(r'\[TG_READ_GROUP:([^\]]+)\]', reply)
    if m:
        group_name = m.group(1).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"📋 Читаю группу «{group_name}»...")
        result = await tg_read_group(group_name)
        # Если сообщений много — отправляем кусками
        for i in range(0, len(result), 4000):
            await update.message.reply_text(result[i:i+4000])
        # Передаём Claude для анализа
        if depth < MAX_DEPTH and "👤" in result:
            histories[uid].append({
                "role": "user",
                "content": f"[СООБЩЕНИЯ ИЗ ГРУППЫ]\n{result}\n\nПроанализируй и ответь по задаче."
            })
            analysis = await claude_call(uid)
            analysis_clean = re.sub(r'\[[A-Z_]+:[^\]]*\]', '', analysis).strip()
            if analysis_clean: await update.message.reply_text(analysis_clean)
        return True

    m = re.search(r'\[TG_FIND_CONTACT:([^\]]+)\]', reply)
    if m:
        if clean: await update.message.reply_text(clean)
        result = await tg_find_contact(m.group(1).strip())
        await update.message.reply_text(result)
        # Передаём результат обратно в Claude — он сам отправит сообщение
        if depth < MAX_DEPTH and "👤" in result:
            histories[uid].append({
                "role": "user",
                "content": f"[РЕЗУЛЬТАТ ПОИСКА КОНТАКТА]\n{result}\n\nВыполни следующий шаг."
            })
            follow_up = await claude_call(uid)
            if re.search(r'\[TG_SEND:', follow_up):
                await process_commands(follow_up, update, uid, depth=depth + 1)
            else:
                follow_clean = re.sub(r'\[[A-Z_]+:[^\]]*\]', '', follow_up).strip()
                if follow_clean: await update.message.reply_text(follow_clean)
        return True

    return False

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
    tg_status = "✅ Telegram юзербот подключён" if userbot else "⚠️ Telegram юзербот не настроен"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔤 Текст", callback_data="mode_text"),
        InlineKeyboardButton("🎤 Голос", callback_data="mode_voice")
    ]])
    await update.message.reply_text(
        f"Здравствуйте, Сергей Сергеевич. Я Lilu, ваш персональный ассистент.\n\n"
        f"{tg_status}\n\n"
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
            await send_reply(update, ctx, reply, uid)
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

async def main_async():
    global userbot

    # Запуск Pyrogram юзербота
    if TG_SESSION_STRING:
        try:
            userbot = PyrogramClient(
                name="lilu_userbot",
                api_id=TG_API_ID,
                api_hash=TG_API_HASH,
                session_string=TG_SESSION_STRING,
            )
            await userbot.start()
            me = await userbot.get_me()
            logger.info(f"Userbot запущен: @{me.username} ({me.first_name})")
        except Exception as e:
            logger.error(f"Ошибка запуска userbot: {e}")
            userbot = None
    else:
        logger.warning("TG_SESSION_STRING не задан — юзербот не активен.")

    # Запуск основного бота
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
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        try:
            await asyncio.Event().wait()  # Работаем вечно
        finally:
            await app.updater.stop()
            await app.stop()
            if userbot:
                await userbot.stop()
                logger.info("Userbot остановлен.")

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
