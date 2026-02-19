import os
import json
import time
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv
from converter import EthiopianDateConverter
import boto3
from botocore.exceptions import ClientError

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Setup ────────────────────────────────────────────────────────────────────

load_dotenv()

BOT_TOKEN        = os.getenv("T_BOT_TOKEN")
ADMIN_USER_ID    = os.getenv("ADMIN_USER_ID")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL")
AWS_ACCESS_KEY   = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY   = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_BUCKET       = os.getenv("AWS_S3_BUCKET_NAME")
AWS_REGION       = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

if not BOT_TOKEN:
    raise RuntimeError("T_BOT_TOKEN not set in environment")

USE_S3 = all([AWS_ENDPOINT_URL, AWS_ACCESS_KEY, AWS_SECRET_KEY, AWS_BUCKET])

s3_client = None
if USE_S3:
    s3_client = boto3.client(
        "s3",
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=AWS_REGION,
    )
    logger.info(f"S3 storage configured: {AWS_BUCKET}")
else:
    logger.warning("S3 not configured — using local file storage (not persistent on Railway!)")

USERS_FILE   = "users.json"
_users_cache = None

# ─── Month data ───────────────────────────────────────────────────────────────

ETH_MONTHS_AM = [
    "መስከረም", "ጥቅምት", "ኅዳር",  "ታህሳስ",
    "ጥር",    "የካቲት", "መጋቢት", "ሚያዝያ",
    "ግንቦት",  "ሰኔ",   "ሐምሌ",  "ነሐሴ", "ጳጉሜ",
]

ETH_MONTHS_EN = [
    "Meskerem", "Tikimt",  "Hidar",   "Tahsas",
    "Tir",      "Yekatit", "Megabit", "Miyazia",
    "Ginbot",   "Sene",    "Hamle",   "Nehase", "Pagume",
]

GREG_MONTHS = [
    "January", "February", "March",     "April",   "May",      "June",
    "July",    "August",   "September", "October", "November", "December",
]

ETH_TO_GREG_MONTH_NAME = {
    1:  "September", 2:  "October",  3:  "November", 4:  "December",
    5:  "January",   6:  "February", 7:  "March",     8:  "April",
    9:  "May",       10: "June",     11: "July",      12: "August",
    13: "Pagume",
}

ETH_WEEKDAYS_AM = ["ሰኞ", "ማክሰኞ", "ረቡዕ", "ሐሙስ", "አርብ", "ቅዳሜ", "እሁድ"]
ETH_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ─── Ethiopian holidays (fixed, month/day in Ethiopian calendar) ──────────────
# Format: (eth_month, eth_day): {"en": "...", "am": "..."}
ETH_HOLIDAYS = {
    (1,  1):  {"en": "🎊 Ethiopian New Year (Enkutatash)",    "am": "🎊 ዕንቁጣጣሽ (የኢትዮጵያ አዲስ ዓመት)"},
    (1,  11): {"en": "✝️ Meskel (Finding of the True Cross)", "am": "✝️ መስቀል"},
    (4,  29): {"en": "🕌 Timkat (Ethiopian Epiphany)",         "am": "🕌 ጥምቀት"},
    (5,  1):  {"en": "❄️ Leddet (Ethiopian Christmas)",       "am": "❄️ ልደት (የኢትዮጵያ ገና)"},
    (6,  29): {"en": "⚔️ Adwa Victory Day",                   "am": "⚔️ የዓድዋ ድል ቀን"},
    (9,  1):  {"en": "🌸 Ethiopian Labour Day",               "am": "🌸 የሠራተኞች ቀን"},
    (10, 11): {"en": "🦁 Patriots Victory Day",              "am": "🦁 የአርበኞች ቀን"},
    (11, 11): {"en": "🌍 Downfall of the Derg",              "am": "🌍 የደርግ ውድቀት ቀን"},
}

# ─── User persistence ─────────────────────────────────────────────────────────

def load_users() -> dict:
    global _users_cache
    if _users_cache is not None:
        return _users_cache

    if USE_S3:
        try:
            response = s3_client.get_object(Bucket=AWS_BUCKET, Key=USERS_FILE)
            _users_cache = json.loads(response["Body"].read().decode()).get("users", {})
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchKey":
                logger.error(f"S3 read error: {e}")
            _users_cache = {}
        except Exception as e:
            logger.error(f"Unexpected S3 error: {e}")
            _users_cache = {}
    else:
        try:
            with open(USERS_FILE) as f:
                _users_cache = json.load(f).get("users", {})
        except (FileNotFoundError, json.JSONDecodeError):
            _users_cache = {}

    return _users_cache


def save_users(users: dict) -> None:
    payload = json.dumps({"users": users}, separators=(",", ":")).encode()
    if USE_S3:
        try:
            s3_client.put_object(
                Bucket=AWS_BUCKET, Key=USERS_FILE,
                Body=payload, ContentType="application/json",
            )
        except Exception as e:
            logger.error(f"S3 write error: {e}")
    else:
        try:
            with open(USERS_FILE, "w") as f:
                f.write(payload.decode())
        except IOError as e:
            logger.error(f"Local write error: {e}")


def add_user(user_id: int, username: str = None, first_name: str = None) -> bool:
    users = load_users()
    key   = str(user_id)
    if key in users:
        return False
    record = {"t": int(time.time())}
    if username:
        record["u"] = username
    if first_name:
        record["n"] = first_name
    users[key] = record
    save_users(users)
    return True


def get_user_count() -> int:
    return len(load_users())


def get_all_users() -> dict:
    return load_users()


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_USER_ID) and str(user_id) == str(ADMIN_USER_ID)


# ─── Keyboards ────────────────────────────────────────────────────────────────

def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("English 🇬🇧", callback_data="lang:en"),
            InlineKeyboardButton("አማርኛ 🇪🇹",    callback_data="lang:am"),
        ]
    ])


def main_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    if lang == "am":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🇪🇹 ኢትዮ → 🌍 ግሪጎ",  callback_data="mode:E2G"),
                InlineKeyboardButton("🌍 ግሪጎ → 🇪🇹 ኢትዮ",  callback_data="mode:G2E"),
            ],
            [
                InlineKeyboardButton("📅 ዛሬ",              callback_data="action:today"),
                InlineKeyboardButton("🗓 በዓሎች",            callback_data="action:holidays"),
                InlineKeyboardButton("ℹ️ እገዛ",             callback_data="action:help"),
            ],
            [
                InlineKeyboardButton("🌐 ቋንቋ ይቀይሩ",       callback_data="action:changelang"),
            ],
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🇪🇹 Ethiopian → 🌍 Gregorian", callback_data="mode:E2G"),
                InlineKeyboardButton("🌍 Gregorian → 🇪🇹 Ethiopian", callback_data="mode:G2E"),
            ],
            [
                InlineKeyboardButton("📅 Today",            callback_data="action:today"),
                InlineKeyboardButton("🗓 Holidays",         callback_data="action:holidays"),
                InlineKeyboardButton("ℹ️ Help",             callback_data="action:help"),
            ],
            [
                InlineKeyboardButton("🌐 Change Language",  callback_data="action:changelang"),
            ],
        ])


def cancel_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    label = "❌ ሰርዝ" if lang == "am" else "❌ Cancel"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="action:cancel")]
    ])


# ─── UI strings ───────────────────────────────────────────────────────────────

TEXT = {
    "en": {
        "welcome": (
            "👋 *Welcome to the Ethiopian Date Converter!*\n\n"
            "I can convert dates between the Ethiopian and Gregorian calendars, "
            "show today's date in both calendars, and list upcoming Ethiopian holidays.\n\n"
            "Please choose your language:"
        ),
        "choose": (
            "✅ Language set to *English*.\n\n"
            "Use the buttons below to get started:"
        ),
        "ask_e": (
            "📥 *Enter an Ethiopian date:*\n\n"
            "Format: `YYYY/MM/DD`\n"
            "Example: `2017/4/27`\n\n"
            "─────────────────\n"
            "💡 *Ethiopian calendar facts:*\n"
            "• 13 months in total\n"
            "• Months 1–12 have *30 days* each\n"
            "• Month 13 (ጳጉሜ/Pagume) has *5 days* (6 in a leap year)\n"
            "• The Ethiopian year is roughly *7–8 years behind* the Gregorian year\n\n"
            "Type your date or press Cancel to go back."
        ),
        "ask_g": (
            "📥 *Enter a Gregorian date:*\n\n"
            "Format: `YYYY/MM/DD`\n"
            "Example: `2025/1/5`\n\n"
            "Type your date or press Cancel to go back."
        ),
        "unrecognised_lang": "🤔 Please pick your language using the buttons below:",
        "unrecognised_mode": "🤔 Please choose an option from the menu below:",
        "unrecognised_date": (
            "🤔 *That doesn't look like a date.*\n\n"
            "Please enter the date in `YYYY/MM/DD` format.\n"
            "📌 Example: `{}`\n\n"
            "Or press Cancel to go back to the menu."
        ),
        "format_error": (
            "❌ *Wrong format.*\n\n"
            "Use numbers only, separated by `/`\n"
            "📌 Example: `{}`\n\n"
            "Try again, or press Cancel to go back."
        ),
        "conversion_error": (
            "❌ *Invalid date:*\n\n_{}_\n\n"
            "Please correct the date and try again, or press Cancel to go back."
        ),
        "e2g": "✅ *Ethiopian date:*\n{}\n\n➡️ *Gregorian date:*\n{}",
        "g2e": "✅ *Gregorian date:*\n{}\n\n➡️ *Ethiopian date:*\n{}",
        "today": (
            "📅 *Today's Date*\n\n"
            "🌍 *Gregorian:* {}\n"
            "🇪🇹 *Ethiopian:* {}\n"
            "📆 *Day:* {}\n\n"
            "{}"  # holiday notice if any
        ),
        "holiday_notice": "🎉 *Today is a holiday:*\n{}",
        "holidays": (
            "🗓 *Ethiopian Public Holidays*\n\n"
            "{}"
        ),
        "no_holidays": "No holidays found.",
        "help": (
            "ℹ️ *Ethiopian Date Converter — Help*\n\n"
            "*How to use:*\n"
            "1️⃣ Tap *Ethiopian → Gregorian* or *Gregorian → Ethiopian*\n"
            "2️⃣ Type your date as `YYYY/MM/DD`\n"
            "3️⃣ Receive the converted date instantly\n\n"
            "📅 Tap *Today* to see today's date in both calendars.\n"
            "🗓 Tap *Holidays* to see Ethiopian public holidays.\n\n"
            "*Ethiopian calendar facts:*\n"
            "• 13 months — months 1–12 have 30 days each\n"
            "• Month 13 (ጳጉሜ/Pagume) has 5 days (6 in a leap year)\n"
            "• Ethiopian year is ~7–8 years behind the Gregorian year\n\n"
            "*Example conversions:*\n"
            "• Ethiopian `2017/4/27` → Gregorian January 5, 2025\n"
            "• Gregorian `2025/1/5` → Ethiopian 2017/4/27\n\n"
            "*Commands:*\n"
            "/start — restart the bot\n"
            "/help  — show this message\n"
            "/today — today's date in both calendars"
        ),
        "cancelled":        "↩️ Cancelled. Choose an option:",
        "change_language":  "Choose your language:",
        "not_admin":        "⛔ This command is only available to administrators.",
        "stats": (
            "📊 *Bot Statistics*\n\n"
            "👥 Total unique users: *{}*\n"
            "🆔 Your user ID: `{}`\n"
            "💾 Storage: {}"
        ),
        "users_list":       "👥 *Registered Users* ({}) — newest first\n\n{}",
        "users_list_empty": "👥 No users registered yet.",
        "convert_another":  "Convert another date:",
    },
    "am": {
        "welcome": (
            "👋 *እንኳን ደህና መጡ! የኢትዮጵያ ቀን መቀየሪያ!*\n\n"
            "በኢትዮጵያ እና ግሪጎሪያን ካላንደሮች መካከል ቀናትን መቀየር፣ "
            "ዛሬን ማሳየት፣ እና የኢትዮጵያ በዓላትን ማየት ይችላሉ።\n\n"
            "ቋንቋ ይምረጡ:"
        ),
        "choose": (
            "✅ ቋንቋ *አማርኛ* ተመርጧል።\n\n"
            "ከታቹ ያሉ አዝራሮችን ይጠቀሙ:"
        ),
        "ask_e": (
            "📥 *የኢትዮጵያ ቀን ያስገቡ:*\n\n"
            "ቅጽ: `YYYY/MM/DD`\n"
            "ምሳሌ: `2017/4/27`\n\n"
            "─────────────────\n"
            "💡 *የኢትዮጵያ ካላንደር:*\n"
            "• 13 ወሮች አሉ\n"
            "• ወር 1–12 እያንዳንዳቸው *30 ቀናት* አሏቸው\n"
            "• ወር 13 (ጳጉሜ) *5 ቀናት* አሉት (ዘመነ ሉቃስ 6)\n"
            "• የኢትዮጵያ ዓ.ም ከግሪጎሪያን ~*7-8 ዓመት* ወደኋላ ነው\n\n"
            "ቀኑን ያስገቡ ወይም ለቀደም ይምለሱ።"
        ),
        "ask_g": (
            "📥 *የግሪጎሪያን ቀን ያስገቡ:*\n\n"
            "ቅጽ: `YYYY/MM/DD`\n"
            "ምሳሌ: `2025/1/5`\n\n"
            "ቀኑን ያስገቡ ወይም ለቀደም ይምለሱ።"
        ),
        "unrecognised_lang": "🤔 ቋንቋ ይምረጡ:",
        "unrecognised_mode": "🤔 ከታቹ ያሉ አዝራሮችን ይምረጡ:",
        "unrecognised_date": (
            "🤔 *ያስገቡት ቀን አይደለም።*\n\n"
            "ቀኑን `YYYY/MM/DD` ቅጽ ያስገቡ።\n"
            "📌 ምሳሌ: `{}`\n\n"
            "ወይም ለቀደም ይምለሱ።"
        ),
        "format_error": (
            "❌ *ቅጹ ተሳስቷል።*\n\n"
            "ቁጥሮች ብቻ፣ በ `/` ይለዩ\n"
            "📌 ምሳሌ: `{}`\n\n"
            "እንደገና ሞክሩ፣ ወይም ለቀደም ይምለሱ።"
        ),
        "conversion_error": (
            "❌ *ቀኑ ልክ አይደለም:*\n\n_{}_\n\n"
            "ቀኑን አርመው እንደገና ሞክሩ፣ ወይም ለቀደም ይምለሱ።"
        ),
        "e2g": "✅ *የኢትዮጵያ ቀን:*\n{}\n\n➡️ *የግሪጎሪያን ቀን:*\n{}",
        "g2e": "✅ *የግሪጎሪያን ቀን:*\n{}\n\n➡️ *የኢትዮጵያ ቀን:*\n{}",
        "today": (
            "📅 *ዛሬ*\n\n"
            "🌍 *ግሪጎሪያን:* {}\n"
            "🇪🇹 *ኢትዮጵያ:* {}\n"
            "📆 *ቀን:* {}\n\n"
            "{}"
        ),
        "holiday_notice": "🎉 *ዛሬ በዓል ነው:*\n{}",
        "holidays": (
            "🗓 *የኢትዮጵያ ብሔራዊ በዓሎች*\n\n"
            "{}"
        ),
        "no_holidays": "በዓሎች አልተገኙም።",
        "help": (
            "ℹ️ *የኢትዮጵያ ቀን መቀየሪያ — እገዛ*\n\n"
            "*አጠቃቀም:*\n"
            "1️⃣ *ኢትዮ → ግሪጎ* ወይም *ግሪጎ → ኢትዮ* ይምረጡ\n"
            "2️⃣ ቀኑን `YYYY/MM/DD` ቅጽ ያስገቡ\n"
            "3️⃣ የተቀየረውን ቀን ይቀበሉ\n\n"
            "📅 *ዛሬ* — ዛሬን ቀን ይመልከቱ።\n"
            "🗓 *በዓሎች* — የኢትዮጵያ ብሔራዊ በዓሎችን ይመልከቱ።\n\n"
            "*ምሳሌዎች:*\n"
            "• ኢትዮ `2017/4/27` → ጃንዋሪ 5, 2025\n"
            "• ግሪጎ `2025/1/5` → ኢትዮ 2017/4/27\n\n"
            "*ትዕዛዞች:*\n"
            "/start — ዳግም ጀምር\n"
            "/help  — ይህን አሳይ\n"
            "/today — ዛሬ"
        ),
        "cancelled":        "↩️ ተሰርዟል። አማራጭ ይምረጡ:",
        "change_language":  "ቋንቋ ይምረጡ:",
        "not_admin":        "⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።",
        "stats": (
            "📊 *የቦት አኃዛዊ መረጃ*\n\n"
            "👥 ጠቅላላ ልዩ ተጠቃሚዎች: *{}*\n"
            "🆔 የእርስዎ ተጠቃሚ መለያ: `{}`\n"
            "💾 ማከማቻ: {}"
        ),
        "users_list":       "👥 *ምዝገባ ተጠቃሚዎች* ({}) — በምዝገባ ቅደም ተከተል\n\n{}",
        "users_list_empty": "👥 ምንም ተጠቃሚ ገና አልመዘገቡም።",
        "convert_another":  "ሌላ ቀን ቀይሩ:",
    },
}

EXAMPLE_DATE = {"E2G": "2017/4/27", "G2E": "2025/1/5"}

# ─── Formatting helpers ───────────────────────────────────────────────────────

def looks_like_date(text: str) -> bool:
    return "/" in text and any(ch.isdigit() for ch in text)


def parse_slash_date(text: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in text.split("/")]
    if len(parts) != 3:
        raise ValueError("must have exactly 3 parts")
    try:
        year, month, day = map(int, parts)
    except ValueError:
        raise ValueError("must be numbers")
    return year, month, day


def format_ethiopian(eth_y: int, eth_m: int, eth_d: int) -> str:
    am_month   = ETH_MONTHS_AM[eth_m - 1]
    en_month   = ETH_MONTHS_EN[eth_m - 1]
    greg_month = ETH_TO_GREG_MONTH_NAME[eth_m]
    return f"{eth_d} {am_month} ({en_month}) ({greg_month}) {eth_y} ዓ.ም"


def format_gregorian(y: int, m: int, d: int) -> str:
    return f"{GREG_MONTHS[m - 1]} {d}, {y}"


def get_today_both_calendars() -> dict:
    """Return today's date in both Gregorian and Ethiopian, with weekday."""
    now  = datetime.now(timezone.utc)
    gy, gm, gd = now.year, now.month, now.day
    ey, em, ed = EthiopianDateConverter.to_ethiopian(gy, gm, gd)
    weekday_idx = now.weekday()  # 0=Monday
    return {
        "greg_str":    format_gregorian(gy, gm, gd),
        "eth_str":     format_ethiopian(ey, em, ed),
        "weekday_en":  ETH_WEEKDAYS_EN[weekday_idx],
        "weekday_am":  ETH_WEEKDAYS_AM[weekday_idx],
        "eth_month":   em,
        "eth_day":     ed,
    }


def get_holiday_for_eth_date(eth_m: int, eth_d: int, lang: str) -> str | None:
    """Return a holiday string if (eth_m, eth_d) is a holiday, else None."""
    h = ETH_HOLIDAYS.get((eth_m, eth_d))
    if h:
        return h.get(lang, h["en"])
    return None


def build_holidays_text(lang: str) -> str:
    """Build a formatted list of all Ethiopian holidays."""
    lines = []
    for (em, ed), names in sorted(ETH_HOLIDAYS.items()):
        eth_month_en = ETH_MONTHS_EN[em - 1]
        eth_month_am = ETH_MONTHS_AM[em - 1]
        name = names.get(lang, names["en"])
        if lang == "am":
            lines.append(f"{name}\n  📌 {ed} {eth_month_am} ({eth_month_en})")
        else:
            lines.append(f"{name}\n  📌 {ed} {eth_month_en}")
    return "\n\n".join(lines) if lines else TEXT[lang]["no_holidays"]


def lang_of(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "en")


def format_user_entry(uid: str, record: dict, index: int) -> str:
    username   = record.get("u")
    first_name = record.get("n", "N/A")
    link = (
        f"[🔗 @{username}](https://t.me/{username})"
        if username
        else f"[🔗 Open Profile](tg://user?id={uid})"
    )
    return f"{index}\\. {first_name} — {link}"


# ─── Shared send helpers ──────────────────────────────────────────────────────

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send a message with the main menu keyboard."""
    lang = lang_of(context)
    if update.callback_query:
        await update.callback_query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_keyboard(lang)
        )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_keyboard(lang)
        )


async def send_awaiting_date(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send a prompt asking for date input with a Cancel button."""
    lang = lang_of(context)
    if update.callback_query:
        await update.callback_query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=cancel_keyboard(lang)
        )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=cancel_keyboard(lang)
        )


# ─── Command Handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    is_new = add_user(user.id, username=user.username, first_name=user.first_name)
    if is_new:
        storage = "S3" if USE_S3 else "local"
        logger.info(f"New user: {user.id} (@{user.username}) — Total: {get_user_count()} [{storage}]")
    context.user_data.clear()
    await update.message.reply_text(
        TEXT["en"]["welcome"], parse_mode="Markdown", reply_markup=lang_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(context)
    kb   = main_keyboard(lang) if "lang" in context.user_data else lang_keyboard()
    await update.message.reply_text(TEXT[lang]["help"], parse_mode="Markdown", reply_markup=kb)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang   = lang_of(context)
    today  = get_today_both_calendars()
    weekday = today["weekday_am"] if lang == "am" else today["weekday_en"]
    holiday = get_holiday_for_eth_date(today["eth_month"], today["eth_day"], lang)
    holiday_line = TEXT[lang]["holiday_notice"].format(holiday) if holiday else ""
    text = TEXT[lang]["today"].format(
        today["greg_str"], today["eth_str"], weekday, holiday_line
    )
    kb = main_keyboard(lang) if "lang" in context.user_data else lang_keyboard()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang    = lang_of(context)
    if not is_admin(user_id):
        await update.message.reply_text(TEXT[lang]["not_admin"])
        return
    storage = f"S3 ({AWS_BUCKET})" if USE_S3 else "Local (⚠️ not persistent)"
    await update.message.reply_text(
        TEXT[lang]["stats"].format(get_user_count(), user_id, storage),
        parse_mode="Markdown",
    )


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang    = lang_of(context)
    if not is_admin(user_id):
        await update.message.reply_text(TEXT[lang]["not_admin"])
        return

    all_users = get_all_users()
    if not all_users:
        await update.message.reply_text(TEXT[lang]["users_list_empty"])
        return

    sorted_users = sorted(
        all_users.items(),
        key=lambda item: item[1].get("t", 0),
        reverse=True,
    )

    MAX_CHARS     = 4000
    pages         = []
    current_lines = []
    current_len   = 0

    for idx, (uid, record) in enumerate(sorted_users, start=1):
        line = format_user_entry(uid, record, idx)
        if current_len + len(line) > MAX_CHARS and current_lines:
            pages.append("\n\n".join(current_lines))
            current_lines, current_len = [], 0
        current_lines.append(line)
        current_len += len(line)
    if current_lines:
        pages.append("\n\n".join(current_lines))

    total = len(all_users)
    for i, page in enumerate(pages):
        header = TEXT[lang]["users_list"].format(total, "")
        if len(pages) > 1:
            header = header.rstrip() + f" _(page {i+1}/{len(pages)})_\n\n"
        await update.message.reply_text(
            header + page,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )


# ─── Callback Query Handler ───────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data
    lang  = lang_of(context)

    # ── Language selection ──
    if data.startswith("lang:"):
        chosen = data.split(":")[1]
        context.user_data["lang"] = chosen
        lang = chosen
        await query.message.reply_text(
            TEXT[lang]["choose"], parse_mode="Markdown", reply_markup=main_keyboard(lang)
        )
        return

    # ── Mode selection ──
    if data.startswith("mode:"):
        mode = data.split(":")[1]
        context.user_data["mode"] = mode
        prompt = TEXT[lang]["ask_e"] if mode == "E2G" else TEXT[lang]["ask_g"]
        await query.message.reply_text(
            prompt, parse_mode="Markdown", reply_markup=cancel_keyboard(lang)
        )
        return

    # ── Actions ──
    if data == "action:today":
        today   = get_today_both_calendars()
        weekday = today["weekday_am"] if lang == "am" else today["weekday_en"]
        holiday = get_holiday_for_eth_date(today["eth_month"], today["eth_day"], lang)
        holiday_line = TEXT[lang]["holiday_notice"].format(holiday) if holiday else ""
        text = TEXT[lang]["today"].format(
            today["greg_str"], today["eth_str"], weekday, holiday_line
        )
        await query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_keyboard(lang)
        )
        return

    if data == "action:holidays":
        holidays_text = build_holidays_text(lang)
        await query.message.reply_text(
            TEXT[lang]["holidays"].format(holidays_text),
            parse_mode="Markdown",
            reply_markup=main_keyboard(lang),
        )
        return

    if data == "action:help":
        await query.message.reply_text(
            TEXT[lang]["help"], parse_mode="Markdown", reply_markup=main_keyboard(lang)
        )
        return

    if data == "action:changelang":
        context.user_data.clear()
        await query.message.reply_text(
            TEXT["en"]["change_language"], reply_markup=lang_keyboard()
        )
        return

    if data == "action:cancel":
        context.user_data.pop("mode", None)
        await query.message.reply_text(
            TEXT[lang]["cancelled"], parse_mode="Markdown", reply_markup=main_keyboard(lang)
        )
        return

    # ── Convert another ──
    if data == "action:convert_again":
        mode = context.user_data.get("last_mode", "E2G")
        context.user_data["mode"] = mode
        prompt = TEXT[lang]["ask_e"] if mode == "E2G" else TEXT[lang]["ask_g"]
        await query.message.reply_text(
            prompt, parse_mode="Markdown", reply_markup=cancel_keyboard(lang)
        )
        return


# ─── Message Handler ──────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lang = lang_of(context)

    # ── If language not yet set, prompt ──
    if "lang" not in context.user_data:
        await update.message.reply_text(
            TEXT["en"]["unrecognised_lang"],
            parse_mode="Markdown",
            reply_markup=lang_keyboard(),
        )
        return

    # ── If no mode set, prompt menu ──
    if "mode" not in context.user_data:
        await update.message.reply_text(
            TEXT[lang]["unrecognised_mode"],
            parse_mode="Markdown",
            reply_markup=main_keyboard(lang),
        )
        return

    # ── Date input & conversion ──
    mode    = context.user_data["mode"]
    example = EXAMPLE_DATE[mode]

    if not looks_like_date(text):
        await update.message.reply_text(
            TEXT[lang]["unrecognised_date"].format(example),
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(lang),
        )
        return

    try:
        y, m, d = parse_slash_date(text)

        if mode == "E2G":
            g        = EthiopianDateConverter.to_gregorian(y, m, d)
            eth_str  = format_ethiopian(y, m, d)
            greg_str = format_gregorian(g.year, g.month, g.day)
            reply    = TEXT[lang]["e2g"].format(eth_str, greg_str)
        else:
            ey, em, ed = EthiopianDateConverter.to_ethiopian(y, m, d)
            greg_str   = format_gregorian(y, m, d)
            eth_str    = format_ethiopian(ey, em, ed)
            reply      = TEXT[lang]["g2e"].format(greg_str, eth_str)

        # Check if converted Ethiopian date is a holiday
        if mode == "G2E":
            holiday = get_holiday_for_eth_date(em, ed, lang)
        else:
            holiday = get_holiday_for_eth_date(m, d, lang)

        if holiday:
            reply += f"\n\n{TEXT[lang]['holiday_notice'].format(holiday)}"

        context.user_data["last_mode"] = mode
        context.user_data.pop("mode", None)

        # Offer to convert another date or go back to menu
        convert_label = "🔄 ሌላ ቀን" if lang == "am" else "🔄 Convert Another"
        menu_label    = "📋 menu"    if lang == "am" else "📋 Menu"
        post_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(convert_label, callback_data="action:convert_again"),
                InlineKeyboardButton(menu_label,    callback_data="action:cancel"),
            ]
        ])
        await update.message.reply_text(
            reply, parse_mode="Markdown", reply_markup=post_keyboard
        )

    except ValueError as e:
        msg   = str(e)
        error = (
            TEXT[lang]["format_error"].format(example)
            if "3 parts" in msg or "must be numbers" in msg
            else TEXT[lang]["conversion_error"].format(msg)
        )
        await update.message.reply_text(
            error, parse_mode="Markdown", reply_markup=cancel_keyboard(lang)
        )

    except Exception as e:
        logger.exception("Unexpected conversion error")
        await update.message.reply_text(
            TEXT[lang]["conversion_error"].format(f"Unexpected error: {e}"),
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(lang),
        )


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_command))
    app.add_handler(CommandHandler("today",  today_command))
    app.add_handler(CommandHandler("stats",  stats_command))
    app.add_handler(CommandHandler("users",  users_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Bot is running… Press Ctrl+C to stop.")
    app.run_polling()
