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
import datetime
import xml.etree.ElementTree as ET
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

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID", "2bbae21a-1833-44d6-ab3a-3e8dd1382074")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID", "906cfc5a-e237-4cde-9136-bcd518e7a45b")
RAILWAY_ENVIRONMENT_ID = os.getenv("RAILWAY_ENVIRONMENT_ID", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "zss5354bali-tech/lilu-agent")

IMAP_SERVER = "imap.mail.ru"

# Per-user state
histories = {}    # chat history
voice_mode = {}   # voice/text mode
memory = {}       # permanent memory
last_emails = {}  # last fetched emails for deletion

# Per-user pending email drafts
pending_drafts = {}  # uid -> {"num": N, "email_data": {...}, "text": "..."}

# Pyrogram userbot client (global)
userbot: PyrogramClient | None = None

MORNING_QUOTES = [
    "Успех — это сумма небольших усилий, повторяемых день за днём.",
    "Риск приходит от незнания того, что ты делаешь. — Уоррен Баффет",
    "Не жди идеального момента. Возьми момент и сделай его идеальным.",
    "Кто хочет — ищет возможности. Кто не хочет — ищет причины.",
    "Делай сегодня то, что другие не хотят — завтра живи так, как другие не могут.",
    "Фокус решает всё. Один приоритет лучше десяти.",
    "Дисциплина — это свобода. Хаос — это тюрьма.",
    "Идеи без действий — просто мечты.",
    "Инвестиции в знания дают лучший процент. — Бенджамин Франклин",
    "Скорость — это стратегия. Кто быстрее — тот выигрывает.",
    "Репутация строится годами, разрушается за секунды.",
    "Простота — высшая степень изощрённости. — Леонардо да Винчи",
    "Главный конкурент — вчерашняя версия тебя самого.",
    "Хочешь изменить результат — измени действия.",
    "Лучшее время начать — сейчас.",
    "Продавай решения, а не продукты.",
    "Сеть контактов — актив номер один в бизнесе.",
    "Тот, кто говорит невозможно, не должен мешать тому, кто делает.",
    "Деньги любят тех, кто к ним серьёзно относится.",
    "Слушай клиента вдвое больше, чем говоришь — два уха, один рот.",
]

SYSTEM_PROMPT = """Ты Lilu — персональный AI-ассистент Сергея Сергеевича Жмакова (Бали, инвестор, AkuMau).

КТО ТЫ:
Ты развёрнута на Railway, работаешь 24/7 как Telegram-бот. Ты сама — и есть этот бот.
Твой код: github.com/zss5354bali-tech/lilu-agent, хостинг: Railway, почта: alfa-sz@mail.ru.
НЕ говори "я не могу создавать ботов/деплоить/работать с GitHub" — ты именно это и делаешь.
НЕ спрашивай токены и конфиг — всё уже настроено и работает.

СТИЛЬ: обращайся "Сергей Сергеевич", коротко и по делу, без воды.

КОМАНДЫ (вставляй в ответ — код выполнит автоматически):

Поиск/страницы:
[WEB_SEARCH:запрос] — поиск в интернете
[FETCH_URL:https://...] — открыть страницу (погода: wttr.in/Bali, курсы: cbr.ru/currency_base/daily/)

Почта (alfa-sz@mail.ru):
[EMAIL_CHECK] — новые письма
[EMAIL_SEARCH:запрос] — найти письма/адрес
[EMAIL_SEND:адрес:Тема:Текст] — отправить
[EMAIL_DELETE_FROM:отправитель] — удалить все от отправителя
[EMAIL_DELETE:номер] — удалить по номеру
[EMAIL_DRAFT:N:текст] — черновик ответа на письмо N (ждёт "да")

Telegram (личный аккаунт +79180408607):
[TG_UNREAD] — непрочитанные + предложить ответы
[TG_SEND_TO:Имя:Текст] — написать по имени
[TG_REPLY_INBOX:имя:Текст] — ответить на входящее
[TG_READ_GROUP:группа] — прочитать группу
[TG_SEARCH:слово] — поиск по чатам
[TG_SEND_GROUP_MEMBER:группа:Имя:Текст] — написать участнику группы

Railway/GitHub:
[RAILWAY_STATUS] — статус деплоев
[RAILWAY_DEPLOY] — перезапустить
[RAILWAY_SET_VAR:KEY:VALUE] — установить переменную
[RAILWAY_GET_VARS] — все переменные
[GITHUB_REPOS] — список репозиториев
[GITHUB_GET:repo:путь] — читать файл
[GITHUB_PUSH:repo:путь:содержимое:commit] — загрузить файл

Память:
[MEMORY_SAVE:ключ:значение] — запомнить навсегда

ПРАВИЛА:
- Поиск контакта → EMAIL_SEARCH, потом EMAIL_SEND. НЕ проси адрес если можешь найти.
- Ответ на письмо → сначала EMAIL_DRAFT, не отправляй сразу.
- НЕ вызывай TG_SEND без явного запроса отправить.
- Если просят написать бота — пиши код, используй ТОЛЬКО токен который дали.
- "статус бота" → RAILWAY_STATUS, "перезапусти" → RAILWAY_DEPLOY.

ПАМЯТЬ: {memory}"""

class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    def handle_data(self, d):
        self.text.append(d)
    def get_text(self):
        return ' '.join(self.text).strip()

def safe_text(text: str) -> str:
    """Очистить текст от символов которые ломают Telegram entity parser."""
    if not text:
        return ""
    # Убираем HTML-сущности
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)
    # Убираем управляющие символы
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Сжимаем множественные пробелы
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

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

def web_search(query: str, max_results: int = 5) -> str:
    """Поиск через Tavily API (основной) с резервом на DuckDuckGo."""
    def trim(text: str, n: int = 300) -> str:
        text = re.sub(r'\s+', ' ', text or '').strip()
        return text[:n] + '...' if len(text) > n else text

    if TAVILY_API_KEY:
        try:
            with httpx.Client(timeout=15) as client:
                r = client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": TAVILY_API_KEY, "query": query,
                          "max_results": max_results, "search_depth": "basic"}
                )
            data = r.json()
            results = data.get("results", [])
            if results:
                out = f"Поиск: «{query}»\n\n"
                for item in results[:5]:
                    out += f"{item.get('title','')}\n{trim(item.get('content',''))}\n{item.get('url','')}\n\n"
                return out.strip()
        except Exception as e:
            logger.warning(f"Tavily error: {e}")

    for backend in ["lite", "html", "api"]:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results, backend=backend))
            if not results:
                continue
            out = f"Поиск: «{query}»\n\n"
            for r in results[:5]:
                out += f"{r.get('title','')}\n{trim(r.get('body',''))}\n{r.get('href','')}\n\n"
            return out.strip()
        except Exception:
            continue

    return "⚠️ Поиск недоступен. Добавьте TAVILY_API_KEY в переменные Railway (бесплатно на tavily.com)."

def fetch_url(url: str, max_chars: int = 4000) -> str:
    """Открыть веб-страницу и вернуть текстовое содержимое. Fallback на Jina.ai для JS-страниц."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        text = strip_html(resp.text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) >= 200:
            return text[:max_chars]
    except Exception:
        pass
    # Fallback: Jina.ai читает JS-страницы
    try:
        with httpx.Client(timeout=25) as client:
            resp = client.get(f"https://r.jina.ai/{url}", headers=headers)
        return resp.text[:max_chars]
    except Exception as e:
        return f"⚠️ Не удалось открыть страницу: {e}"

def imap_connect():
    m = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
    m.login(MAIL_EMAIL, MAIL_PASSWORD)
    m.select("INBOX")
    return m

def get_emails(uid, limit=5, unread_only=True):
    try:
        m = imap_connect()
        _, data = m.search(None, "UNSEEN" if unread_only else "ALL")
        ids = data[0].split()
        if not ids:
            m.logout()
            return "📭 Новых писем нет."
        result = f"📬 {'Новых' if unread_only else 'Последних'} писем: {len(ids)}\n\n"
        last_emails[uid] = []
        for i, mid in enumerate(ids[-limit:]):
            _, md = m.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(md[0][1])
            subj = decode_str(msg.get("Subject", "Без темы"))
            frm_raw = decode_str(msg.get("From", ""))
            # Извлекаем имя и адрес
            if "<" in frm_raw:
                frm_name = frm_raw.split("<")[0].strip().strip('"')
                frm_addr = frm_raw.split("<")[1].rstrip(">").strip()
            else:
                frm_name = frm_raw
                frm_addr = frm_raw
            # Парсим дату
            date_raw = msg.get("Date", "")
            try:
                import email.utils as email_utils
                date_parsed = email_utils.parsedate_to_datetime(date_raw)
                date_str = date_parsed.strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = date_raw[:16] if date_raw else "?"
            body = get_body(msg)
            last_emails[uid].append({"id": mid, "subject": subj, "from": frm_raw, "email": frm_addr})
            result += f"{i+1}. {subj}\nОт: {frm_name} <{frm_addr}>\n{date_str}\n{body[:300]}\n\n"
        m.logout()
        return result
    except imaplib.IMAP4.error as e:
        err = str(e)
        logger.error(f"IMAP error: {err}")
        if "AUTHENTICATIONFAILED" in err or "Invalid credentials" in err:
            return ("⚠️ Ошибка авторизации mail.ru.\n\n"
                    "Нужен пароль приложения (не основной пароль):\n"
                    "mail.ru → Настройки → Безопасность → Пароли приложений → Создать")
        return f"⚠️ Ошибка IMAP: {err}"
    except Exception as e:
        logger.error(f"Email read error: {e}")
        return f"⚠️ Ошибка чтения почты: {e}"

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

# ═══════════════════════════════════════════════════
#  RAILWAY API  (GraphQL v2)
# ═══════════════════════════════════════════════════

def railway_gql(query: str, variables: dict = None) -> dict:
    """Выполнить GraphQL-запрос к Railway API."""
    if not RAILWAY_API_TOKEN:
        return {"error": "RAILWAY_API_TOKEN не задан"}
    try:
        with httpx.Client(timeout=20) as client:
            r = client.post(
                "https://backboard.railway.com/graphql/v2",
                headers={"Authorization": f"Bearer {RAILWAY_API_TOKEN}",
                         "Content-Type": "application/json"},
                json={"query": query, "variables": variables or {}}
            )
        data = r.json()
        if "errors" in data:
            return {"error": data["errors"][0].get("message", str(data["errors"]))}
        return data.get("data", {})
    except Exception as e:
        return {"error": str(e)}


def railway_status() -> str:
    """Получить статус деплоя сервиса."""
    q = """
    query($serviceId: String!) {
      deployments(input: { serviceId: $serviceId }, first: 3) {
        edges { node { id status createdAt } }
      }
    }
    """
    data = railway_gql(q, {"serviceId": RAILWAY_SERVICE_ID})
    if "error" in data:
        return f"⚠️ Railway API: {data['error']}"
    edges = data.get("deployments", {}).get("edges", [])
    if not edges:
        return "ℹ️ Деплоев не найдено."
    lines = ["🚂 Последние деплои Railway:"]
    for e in edges:
        n = e["node"]
        status = n.get("status", "?")
        emoji = {"SUCCESS": "✅", "FAILED": "❌", "BUILDING": "🔨", "DEPLOYING": "🚀",
                 "CRASHED": "💥", "REMOVED": "🗑️"}.get(status, "⏳")
        created = n.get("createdAt", "")[:16].replace("T", " ")
        lines.append(f"{emoji} {status} — {created}")
    return "\n".join(lines)


def railway_redeploy() -> str:
    """Перезапустить последний деплой сервиса."""
    # Сначала получим последний deployment id
    q_get = """
    query($serviceId: String!) {
      deployments(input: { serviceId: $serviceId }, first: 1) {
        edges { node { id } }
      }
    }
    """
    data = railway_gql(q_get, {"serviceId": RAILWAY_SERVICE_ID})
    if "error" in data:
        return f"⚠️ Railway: {data['error']}"
    edges = data.get("deployments", {}).get("edges", [])
    if not edges:
        return "⚠️ Нет деплоев для перезапуска."
    dep_id = edges[0]["node"]["id"]
    q_redeploy = "mutation($id: String!) { deploymentRedeploy(id: $id) { id status } }"
    result = railway_gql(q_redeploy, {"id": dep_id})
    if "error" in result:
        return f"⚠️ Ошибка redeploy: {result['error']}"
    node = result.get("deploymentRedeploy", {})
    return f"🚀 Redeploy запущен. ID: {node.get('id','?')}, статус: {node.get('status','?')}"


def railway_set_var(key: str, value: str) -> str:
    """Установить переменную окружения в Railway."""
    # Нужен environmentId — если не задан, получим его
    env_id = RAILWAY_ENVIRONMENT_ID
    if not env_id:
        q_env = """
        query($projectId: String!) {
          project(id: $projectId) {
            environments { edges { node { id name } } }
          }
        }
        """
        data = railway_gql(q_env, {"projectId": RAILWAY_PROJECT_ID})
        envs = data.get("project", {}).get("environments", {}).get("edges", [])
        for e in envs:
            if e["node"]["name"] == "production":
                env_id = e["node"]["id"]
                break
        if not env_id and envs:
            env_id = envs[0]["node"]["id"]
    if not env_id:
        return "⚠️ Не удалось найти environment."

    q = """
    mutation($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "projectId": RAILWAY_PROJECT_ID,
            "serviceId": RAILWAY_SERVICE_ID,
            "environmentId": env_id,
            "variables": {key: value}
        }
    }
    result = railway_gql(q, variables)
    if "error" in result:
        return f"⚠️ Ошибка Railway setVar: {result['error']}"
    return f"✅ Переменная {key} обновлена в Railway."


def railway_get_vars() -> str:
    """Получить список переменных из Railway."""
    env_id = RAILWAY_ENVIRONMENT_ID
    if not env_id:
        q_env = """
        query($projectId: String!) {
          project(id: $projectId) { environments { edges { node { id name } } } }
        }
        """
        data = railway_gql(q_env, {"projectId": RAILWAY_PROJECT_ID})
        envs = data.get("project", {}).get("environments", {}).get("edges", [])
        for e in envs:
            if e["node"]["name"] == "production":
                env_id = e["node"]["id"]
                break
        if not env_id and envs:
            env_id = envs[0]["node"]["id"]
    if not env_id:
        return "⚠️ Не удалось найти environment."

    q = """
    query($projectId: String!, $serviceId: String!, $environmentId: String!) {
      variables(projectId: $projectId, serviceId: $serviceId, environmentId: $environmentId)
    }
    """
    data = railway_gql(q, {
        "projectId": RAILWAY_PROJECT_ID,
        "serviceId": RAILWAY_SERVICE_ID,
        "environmentId": env_id
    })
    if "error" in data:
        return f"⚠️ Railway getVars: {data['error']}"
    vars_dict = data.get("variables", {})
    if not vars_dict:
        return "ℹ️ Переменные не найдены."
    lines = ["🔧 Переменные Railway:"]
    for k in sorted(vars_dict.keys()):
        v = vars_dict[k]
        # Скрываем чувствительные значения
        if any(x in k.upper() for x in ["TOKEN", "KEY", "SECRET", "PASSWORD", "PASS"]):
            v = v[:4] + "***" if len(v) > 4 else "***"
        lines.append(f"  {k}={v}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════
#  GITHUB API
# ═══════════════════════════════════════════════════

def github_push_file(repo: str, path: str, content: str, message: str = "Update via Lilu") -> str:
    """Создать или обновить файл в GitHub репозитории."""
    if not GITHUB_TOKEN:
        return "⚠️ GITHUB_TOKEN не задан."
    if not repo:
        return "⚠️ GITHUB_REPO не задан. Укажи репозиторий в формате user/repo."
    try:
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        # Получаем текущий sha файла (если существует)
        with httpx.Client(timeout=15) as client:
            existing = client.get(url, headers=headers)
        sha = existing.json().get("sha") if existing.status_code == 200 else None

        # Кодируем содержимое в base64
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {"message": message, "content": encoded}
        if sha:
            payload["sha"] = sha

        with httpx.Client(timeout=20) as client:
            r = client.put(url, headers=headers, json=payload)
        if r.status_code in (200, 201):
            action = "обновлён" if sha else "создан"
            return f"✅ Файл {path} {action} в {repo}"
        return f"⚠️ GitHub ошибка {r.status_code}: {r.json().get('message', r.text[:200])}"
    except Exception as e:
        return f"⚠️ GitHub push error: {e}"


def github_get_file(repo: str, path: str) -> str:
    """Прочитать файл из GitHub репозитория."""
    if not GITHUB_TOKEN:
        return "⚠️ GITHUB_TOKEN не задан."
    if not repo:
        return "⚠️ GITHUB_REPO не задан."
    try:
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        with httpx.Client(timeout=15) as client:
            r = client.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return f"📄 {path} из {repo}:\n\n{content[:3000]}"
        return f"⚠️ GitHub {r.status_code}: {r.json().get('message', r.text[:100])}"
    except Exception as e:
        return f"⚠️ GitHub get error: {e}"


def github_list_repos() -> str:
    """Получить список репозиториев пользователя."""
    if not GITHUB_TOKEN:
        return "⚠️ GITHUB_TOKEN не задан."
    try:
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        with httpx.Client(timeout=15) as client:
            r = client.get("https://api.github.com/user/repos?per_page=20&sort=updated", headers=headers)
        if r.status_code == 200:
            repos = r.json()
            lines = ["📦 GitHub репозитории:"]
            for repo in repos:
                private = "🔒" if repo.get("private") else "🌐"
                lines.append(f"  {private} {repo['full_name']}")
            return "\n".join(lines)
        return f"⚠️ GitHub {r.status_code}: {r.json().get('message', r.text[:100])}"
    except Exception as e:
        return f"⚠️ GitHub repos error: {e}"


async def tg_send(recipient: str, text: str) -> str:
    """Отправить сообщение через личный аккаунт Telegram (точный @username или числовой id)."""
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

async def tg_send_to(name: str, text: str) -> str:
    """Найти контакт по имени среди диалогов и отправить сообщение напрямую."""
    if not userbot:
        return "⚠️ Userbot не подключён."
    name = name.strip()
    # Если передали @username — отправляем напрямую
    if name.startswith("@"):
        try:
            await userbot.send_message(name, text.strip())
            return f"✅ Отправлено {name}"
        except Exception as e:
            return f"⚠️ Ошибка отправки {name}: {e}"
    try:
        words = [w.lower() for w in name.split() if len(w) > 1]
        checked = 0
        async for dialog in userbot.get_dialogs():
            checked += 1
            if checked > 500:
                break
            chat = dialog.chat
            first = getattr(chat, "first_name", "") or ""
            last = getattr(chat, "last_name", "") or ""
            title = chat.title or ""
            username = getattr(chat, "username", "") or ""
            full = f"{first} {last}".strip()
            search_str = f"{full} {title} {username}".lower()
            if any(w in search_str for w in words):
                display = full or title or username or str(chat.id)
                try:
                    await userbot.send_message(chat.id, text.strip())
                    return f"✅ Отправлено {display}"
                except Exception as e:
                    return f"⚠️ Найден {display}, но ошибка отправки: {e}"
        return f"❌ Контакт не найден: {name}"
    except Exception as e:
        return f"⚠️ Ошибка tg_send_to: {e}"

async def tg_reply_inbox(description: str, text: str) -> str:
    """Найти последнее входящее сообщение по описанию отправителя и ответить."""
    if not userbot:
        return "⚠️ Userbot не подключён."
    description = description.strip()
    words = [w.lower() for w in description.split() if len(w) > 1]
    try:
        checked = 0
        async for dialog in userbot.get_dialogs():
            checked += 1
            if checked > 50:
                break
            msg = dialog.top_message
            if not msg or msg.outgoing:
                continue
            chat = dialog.chat
            first = getattr(chat, "first_name", "") or ""
            last = getattr(chat, "last_name", "") or ""
            title = chat.title or ""
            username = getattr(chat, "username", "") or ""
            sender_name = ""
            if msg.from_user:
                sender_name = f"{msg.from_user.first_name or ''} {msg.from_user.last_name or ''}".strip()
            full = f"{first} {last} {title} {username} {sender_name}".lower()
            if any(w in full for w in words):
                display = (first + " " + last).strip() or title or username or str(chat.id)
                preview = (text[:50] + "...") if len(text) > 50 else text
                try:
                    await userbot.send_message(chat.id, text.strip())
                    return f"✅ Ответ отправлен {display}: {preview}"
                except Exception as e:
                    return f"⚠️ Найден {display}, но ошибка отправки: {e}"
        return f"❌ Входящее от «{description}» не найдено среди последних {checked} диалогов."
    except Exception as e:
        return f"⚠️ Ошибка tg_reply_inbox: {e}"

async def tg_send_group_member(group_name: str, member_name: str, text: str) -> str:
    """Найти участника группы по имени и отправить ему сообщение напрямую."""
    if not userbot:
        return "⚠️ Userbot не подключён."
    group_name = group_name.strip()
    member_name = member_name.strip()
    try:
        # Ищем группу по словам (любой порядок слов)
        target_chat = None
        words = [w.lower() for w in group_name.split() if len(w) > 1]
        async for dialog in userbot.get_dialogs():
            chat = dialog.chat
            title = (chat.title or "").lower()
            if all(w in title for w in words):
                target_chat = chat
                break
        if not target_chat:
            return f"❌ Группа «{group_name}» не найдена."
        # Ищем участника
        name_words = [w.lower() for w in member_name.split() if len(w) > 1]
        async for member in userbot.get_chat_members(target_chat.id):
            user = member.user
            if user.is_bot or user.is_deleted:
                continue
            first = user.first_name or ""
            last = user.last_name or ""
            full = f"{first} {last}".strip()
            username = user.username or ""
            search_str = f"{full} {username}".lower()
            if any(w in search_str for w in name_words):
                display = full or username or str(user.id)
                try:
                    await userbot.send_message(user.id, text.strip())
                    return f"✅ Отправлено {display} (из группы «{target_chat.title}»)"
                except Exception as e:
                    return f"⚠️ Найден {display}, но ошибка отправки: {e}"
        return f"❌ Участник «{member_name}» не найден в группе «{target_chat.title}»."
    except Exception as e:
        return f"⚠️ Ошибка tg_send_group_member: {e}"

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
        # Ищем группу по словам (любой порядок слов)
        target_chat = None
        words = [w.lower() for w in group_name.split() if len(w) > 1]
        async for dialog in userbot.get_dialogs():
            chat = dialog.chat
            title = (chat.title or "").lower()
            if all(w in title for w in words):
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

async def tg_get_unread(limit: int = 15) -> list:
    """Получить непрочитанные диалоги с контекстом переписки."""
    if not userbot:
        return []
    unread = []
    checked = 0
    async for dialog in userbot.get_dialogs():
        checked += 1
        if checked > 300:
            break
        if (dialog.unread_messages_count or 0) == 0:
            continue
        chat = dialog.chat
        # Берём последние 5 сообщений для контекста
        msgs = []
        try:
            async for msg in userbot.get_chat_history(chat.id, limit=5):
                if not (msg.text or msg.caption):
                    continue
                sender = ""
                if msg.from_user:
                    sender = f"{msg.from_user.first_name or ''} {msg.from_user.last_name or ''}".strip()
                elif msg.sender_chat:
                    sender = msg.sender_chat.title or "?"
                else:
                    sender = "?"
                content = (msg.text or msg.caption or "").strip()[:300]
                msgs.append({"sender": sender, "text": content, "out": msg.outgoing})
        except Exception:
            continue
        if not msgs:
            continue
        msgs.reverse()
        first = getattr(chat, "first_name", "") or ""
        last = getattr(chat, "last_name", "") or ""
        name = f"{first} {last}".strip() or chat.title or str(chat.id)
        unread.append({
            "chat_id": chat.id,
            "name": name,
            "unread": dialog.unread_messages_count,
            "messages": msgs
        })
        if len(unread) >= limit:
            break
    return unread

async def _claude_request(system: str, messages: list) -> str:
    """Базовый вызов Claude API с retry при перегрузке и 500 ошибках (до 4 попыток)."""
    last_err = None
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-sonnet-4-20250514", "max_tokens": 1500, "system": system, "messages": messages}
                )
            # HTTP 5xx — серверная ошибка Anthropic, ретрай
            if r.status_code >= 500:
                wait = (attempt + 1) * 8
                logger.warning(f"Anthropic HTTP {r.status_code} (attempt {attempt+1}), retry in {wait}s...")
                last_err = f"Серверы Anthropic вернули ошибку {r.status_code}, повторяю..."
                await asyncio.sleep(wait)
                continue
            data = r.json()
            if "content" in data:
                return data["content"][0]["text"]
            err_type = data.get("error", {}).get("type", "")
            err_msg = data.get("error", {}).get("message", str(data))
            logger.error(f"Claude error (attempt {attempt+1}): {err_msg}")
            if err_type in ("overloaded_error", "api_error"):
                wait = (attempt + 1) * 8
                logger.info(f"Anthropic {err_type}, retry in {wait}s...")
                await asyncio.sleep(wait)
                last_err = "Серверы Anthropic перегружены, повторяю запрос..."
                continue
            raise Exception(err_msg)
        except httpx.TimeoutException:
            logger.warning(f"Claude timeout (attempt {attempt+1})")
            last_err = "Таймаут запроса к Claude API."
            await asyncio.sleep(3)
    raise Exception(last_err or "Claude недоступен.")

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
    if len(histories[uid]) > 20:
        histories[uid] = histories[uid][-20:]
    reply = await _claude_request(system, histories[uid])
    histories[uid].append({"role": "assistant", "content": reply})
    return reply

async def claude_call(uid):
    """Вызов Claude с текущей историей без добавления нового сообщения."""
    mem_str = json.dumps(memory.get(uid, {}), ensure_ascii=False) if memory.get(uid) else "пусто"
    system = SYSTEM_PROMPT.replace("{memory}", mem_str)
    reply = await _claude_request(system, histories[uid])
    histories[uid].append({"role": "assistant", "content": reply})
    return reply

async def process_commands(reply, update, uid, depth=0):
    """
    Разбирает и выполняет команды в ответе Claude.
    Атомарные TG команды (TG_SEND_TO, TG_REPLY_INBOX, TG_SEND_GROUP_MEMBER) —
    код сам делает всё, без промежуточных вызовов Claude.
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
        await update.message.reply_text(f"🌐 Ищу: {query}...")
        result = web_search(query)
        # Не показываем сырые результаты — Claude обрабатывает и отвечает кратко
        if depth < MAX_DEPTH:
            histories[uid].append({
                "role": "user",
                "content": (
                    f"[РЕЗУЛЬТАТ ПОИСКА по запросу «{query}»]\n{result[:2000]}\n\n"
                    "Дай краткий конкретный ответ на вопрос пользователя. "
                    "Без лишних деталей. Если нужна конкретная цифра/адрес/контакт — дай только её."
                )
            })
            follow_up = await claude_call(uid)
            if re.search(r'\[WEB_SEARCH:|FETCH_URL:|EMAIL_SEND:|TG_SEND:', follow_up):
                await process_commands(follow_up, update, uid, depth=depth + 1)
            else:
                follow_clean = re.sub(r'\[[A-Z_]+:[^\]]*\]', '', follow_up).strip()
                if follow_clean: await update.message.reply_text(follow_clean)
        else:
            await update.message.reply_text(result[:1500])
        return True

    m = re.search(r'\[FETCH_URL:([^\]]+)\]', reply)
    if m:
        url = m.group(1).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"🌐 Открываю страницу...")
        content = fetch_url(url, max_chars=2000)
        if depth < MAX_DEPTH:
            histories[uid].append({
                "role": "user",
                "content": (
                    f"[СТРАНИЦА {url}]\n{content[:2000]}\n\n"
                    "Ответь кратко и по существу задачи. Только нужная информация."
                )
            })
            follow_up = await claude_call(uid)
            follow_clean = re.sub(r'\[[A-Z_]+:[^\]]*\]', '', follow_up).strip()
            if follow_clean: await update.message.reply_text(follow_clean)
        else:
            await update.message.reply_text(content[:2000])
        return True

    m = re.search(r'\[EMAIL_DRAFT:(\d+):(.+)\]', reply, re.DOTALL)
    if m:
        try:
            num = int(m.group(1))
            draft_text = m.group(2).strip()
            if clean: await update.message.reply_text(clean)
            email_data = last_emails.get(uid, [])
            if email_data and 1 <= num <= len(email_data):
                em = email_data[num - 1]
                pending_drafts[uid] = {"num": num, "email_data": em, "text": draft_text}
                await update.message.reply_text(
                    f"✉️ Черновик ответа на письмо {num} ({em.get('from','?')}):\n\n"
                    f"{'─'*30}\n{draft_text}\n{'─'*30}\n\n"
                    f"Напишите «да» чтобы отправить, или дайте свой вариант текста."
                )
            else:
                await update.message.reply_text(f"✉️ Черновик:\n\n{draft_text}\n\nНапишите «да» для отправки.")
                pending_drafts[uid] = {"num": num, "email_data": None, "text": draft_text}
        except Exception as e:
            await update.message.reply_text(f"⚠️ Ошибка черновика: {e}")
        return True

    if "[EMAIL_CHECK]" in reply:
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(safe_text(get_emails(uid)))
        return True

    m = re.search(r'\[EMAIL_SEARCH:([^\]]+)\]', reply)
    if m:
        query = m.group(1).strip()
        if clean: await update.message.reply_text(clean)
        result = search_emails(uid, query)
        await update.message.reply_text(safe_text(result))
        # Возвращаем результат поиска в Claude — он сам отправит письмо
        if depth < MAX_DEPTH:
            histories[uid].append({
                "role": "user",
                "content": f"[РЕЗУЛЬТАТ ПОИСКА]\n{result}\n\nЕсли нужно — выполни следующий шаг."
            })
            follow_up = await claude_call(uid)
            if re.search(r'\[EMAIL_SEND:|EMAIL_DELETE|EMAIL_DELETE_FROM:|TG_SEND:', follow_up):
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

    # --- TG_UNREAD: непрочитанные + предложение ответов ---

    if "[TG_UNREAD]" in reply:
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text("📬 Читаю непрочитанные сообщения...")
        unread_list = await tg_get_unread()
        if not unread_list:
            await update.message.reply_text("📭 Непрочитанных сообщений нет.")
            return True
        # Формируем сводку для Claude
        summary = f"Непрочитанных диалогов: {len(unread_list)}\n\n"
        for i, item in enumerate(unread_list, 1):
            summary += f"--- {i}. {item['name']} (непрочитанных: {item['unread']}) ---\n"
            for msg in item["messages"]:
                direction = "→ Вы" if msg["out"] else f"← {msg['sender']}"
                summary += f"{direction}: {msg['text']}\n"
            summary += "\n"
        await update.message.reply_text(f"📋 Найдено {len(unread_list)} непрочитанных диалогов. Анализирую...")
        if depth < MAX_DEPTH:
            histories[uid].append({
                "role": "user",
                "content": (
                    f"[НЕПРОЧИТАННЫЕ TELEGRAM СООБЩЕНИЯ]\n{summary}\n\n"
                    "Для каждого диалога:\n"
                    "1. Кратко что пишут\n"
                    "2. Предложи готовый ответ в стиле Сергея Сергеевича — коротко, по делу, без лишних слов\n"
                    "Формат: **Имя**: [суть] → Предлагаю ответить: «текст ответа»\n"
                    "Если сообщение не требует ответа — так и скажи."
                )
            })
            analysis = await claude_call(uid)
            analysis_clean = re.sub(r'\[[A-Z_]+:[^\]]*\]', '', analysis).strip()
            if analysis_clean:
                for i in range(0, len(analysis_clean), 4000):
                    await update.message.reply_text(analysis_clean[i:i+4000])
        return True

    # --- Атомарные TG команды (код делает всё сам, без Claude в середине) ---

    m = re.search(r'\[TG_SEND_TO:([^:]+):(.+)\]', reply, re.DOTALL)
    if m:
        name = m.group(1).strip()
        text = m.group(2).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"📤 Ищу контакт и отправляю...")
        result = await tg_send_to(name, text)
        await update.message.reply_text(result)
        return True

    m = re.search(r'\[TG_REPLY_INBOX:([^:]+):(.+)\]', reply, re.DOTALL)
    if m:
        description = m.group(1).strip()
        text = m.group(2).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"📤 Ищу входящее от «{description}» и отвечаю...")
        result = await tg_reply_inbox(description, text)
        await update.message.reply_text(result)
        return True

    m = re.search(r'\[TG_SEND_GROUP_MEMBER:([^:]+):([^:]+):(.+)\]', reply, re.DOTALL)
    if m:
        group_name = m.group(1).strip()
        member_name = m.group(2).strip()
        text = m.group(3).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"📤 Ищу «{member_name}» в группе «{group_name}»...")
        result = await tg_send_group_member(group_name, member_name, text)
        await update.message.reply_text(result)
        return True

    # --- Стандартные TG команды ---

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

    # ─── Railway команды ───────────────────────────────

    if "[RAILWAY_STATUS]" in reply:
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text("🚂 Проверяю статус Railway...")
        result = railway_status()
        await update.message.reply_text(result)
        return True

    if "[RAILWAY_DEPLOY]" in reply:
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text("🚀 Запускаю redeploy Railway...")
        result = railway_redeploy()
        await update.message.reply_text(result)
        return True

    m = re.search(r'\[RAILWAY_SET_VAR:([^:]+):([^\]]+)\]', reply)
    if m:
        key = m.group(1).strip()
        value = m.group(2).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"🔧 Устанавливаю переменную {key}...")
        result = railway_set_var(key, value)
        await update.message.reply_text(result)
        return True

    if "[RAILWAY_GET_VARS]" in reply:
        if clean: await update.message.reply_text(clean)
        result = railway_get_vars()
        await update.message.reply_text(result[:4000])
        return True

    # ─── GitHub команды ───────────────────────────────

    if "[GITHUB_REPOS]" in reply:
        if clean: await update.message.reply_text(clean)
        result = github_list_repos()
        await update.message.reply_text(result)
        return True

    m = re.search(r'\[GITHUB_GET:([^:]+):([^\]]+)\]', reply)
    if m:
        repo = m.group(1).strip() or GITHUB_REPO
        path = m.group(2).strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"📄 Читаю файл из GitHub...")
        result = github_get_file(repo, path)
        for i in range(0, len(result), 4000):
            await update.message.reply_text(result[i:i+4000])
        return True

    m = re.search(r'\[GITHUB_PUSH:([^:]+):([^:]+):(.+?)(?::([^\]]+))?\]', reply, re.DOTALL)
    if m:
        repo = m.group(1).strip() or GITHUB_REPO
        path = m.group(2).strip()
        content = m.group(3).strip()
        commit_msg = (m.group(4) or "Update via Lilu").strip()
        if clean: await update.message.reply_text(clean)
        await update.message.reply_text(f"📤 Загружаю файл {path} на GitHub...")
        result = github_push_file(repo, path, content, commit_msg)
        await update.message.reply_text(result)
        return True

    return False

async def build_digest() -> str:
    """Собрать текст утреннего дайджеста."""
    today = datetime.date.today()
    quote = MORNING_QUOTES[today.timetuple().tm_yday % len(MORNING_QUOTES)]
    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day_str = today.strftime("%d.%m.%Y") + f" ({days_ru[today.weekday()]})"

    # Погода на Бали
    weather = ""
    try:
        with httpx.Client(timeout=8) as client:
            r = client.get("https://wttr.in/Bali?format=%c+%C,+%t+%w&lang=ru")
        if r.status_code == 200:
            weather = r.text.strip()
    except Exception:
        pass

    # Курсы ЦБ (USD, EUR, IDR если есть)
    rates = ""
    try:
        with httpx.Client(timeout=8) as client:
            r = client.get("https://www.cbr.ru/scripts/XML_daily.asp")
        root = ET.fromstring(r.content)
        usd = eur = idr = ""
        for v in root.findall("Valute"):
            code = v.find("CharCode").text
            val = float(v.find("Value").text.replace(",", "."))
            nom = int(v.find("Nominal").text)
            rate = val / nom
            if code == "USD": usd = f"💵 USD {rate:.2f}₽"
            elif code == "EUR": eur = f"💶 EUR {rate:.2f}₽"
            elif code == "IDR":
                rate_k = val / nom * 1000
                idr = f"🇮🇩 IDR {rate_k:.4f}₽/1000"
        rates = "  ".join(filter(None, [usd, eur, idr]))
    except Exception:
        pass

    # Топ новости (Tavily)
    news_lines = []
    if TAVILY_API_KEY:
        news_queries = [
            ("🌍 Мир", "world news today top headlines"),
            ("🇮🇩 Бали", "Bali Indonesia news today"),
            ("📈 Бизнес", "business investment news today"),
        ]
        for emoji_label, q in news_queries:
            try:
                with httpx.Client(timeout=10) as client:
                    r = client.post(
                        "https://api.tavily.com/search",
                        json={"api_key": TAVILY_API_KEY, "query": q,
                              "max_results": 3, "search_depth": "basic",
                              "topic": "news", "days": 3}
                    )
                data = r.json()
                items = data.get("results", [])[:3]
                if items:
                    news_lines.append(f"\n{emoji_label}:")
                    for it in items:
                        title = re.sub(r'\s+', ' ', it.get('title', '')).strip()[:120]
                        news_lines.append(f"• {title}")
                else:
                    logger.warning(f"Tavily digest '{q}': no results. Response: {str(data)[:200]}")
            except Exception as e:
                logger.warning(f"Tavily digest '{q}' error: {e}")

    # Новые письма
    email_part = ""
    try:
        emails_text = get_emails(OWNER_ID, limit=3)
        if "📭" not in emails_text and "⚠️" not in emails_text:
            email_part = emails_text[:500]
    except Exception:
        pass

    lines = [f"☀️ Доброе утро, Сергей Сергеевич!\n📅 {day_str}"]
    if weather: lines.append(f"🌤 {weather}")
    if rates: lines.append(rates)
    if news_lines: lines.append("\n".join(news_lines))
    lines.append(f"\n💬 «{quote}»")
    if email_part: lines.append(f"\n📬 Почта:\n{email_part}")
    else: lines.append("\n📭 Новых писем нет.")
    return "\n".join(lines)


async def morning_digest(context):
    """Утренний дайджест — запускается по расписанию."""
    try:
        text = await build_digest()
        await context.bot.send_message(chat_id=OWNER_ID, text=text)
        logger.info("Утренний дайджест отправлен.")
    except Exception as e:
        logger.error(f"Morning digest error: {e}")

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

async def digest_cmd(update, ctx):
    """Ручной запуск утреннего дайджеста — /digest"""
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text("⏳ Собираю дайджест...")
    try:
        text = await build_digest()
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка дайджеста: {e}")

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
    await update.message.reply_text(safe_text(get_emails(update.effective_user.id)))

async def handle_text(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid): return

    # Проверка подтверждения черновика письма
    msg = update.message.text.strip().lower()
    if uid in pending_drafts and re.match(r'^(да|ок|окей|отправляй|отправить|send|yes)[\.\!]?$', msg):
        draft = pending_drafts.pop(uid)
        em = draft.get("email_data")
        if em and em.get("email"):
            result = send_email(em["email"], f"Re: {em.get('subject','')}", draft["text"])
            await update.message.reply_text(result)
        else:
            await update.message.reply_text("⚠️ Нет данных получателя для отправки.")
        return

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
    app.add_handler(CommandHandler("digest", digest_cmd))
    app.add_handler(CallbackQueryHandler(set_mode, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Утренний дайджест — 08:00 по Бали (UTC+8 = 00:00 UTC)
    if OWNER_ID:
        app.job_queue.run_daily(
            morning_digest,
            time=datetime.time(0, 0, 0, tzinfo=datetime.timezone.utc),
            chat_id=OWNER_ID
        )
        logger.info("Утренний дайджест запланирован на 08:00 Бали.")

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
