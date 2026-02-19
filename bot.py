import os
import json
import time
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
    print(f"✅ S3 storage configured: {AWS_BUCKET}")
else:
    print("⚠️  S3 not configured — using local file storage.")

USERS_FILE   = "users.json"
_users_cache = None

# ─── Calendar data ────────────────────────────────────────────────────────────

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

ETH_HOLIDAYS = {
    (1,  1):  {"en": "Ethiopian New Year (Enkutatash)",    "am": "ዕንቁጣጣሽ (የኢትዮጵያ አዲስ ዓመት)"},
    (1,  17): {"en": "Meskel (Finding of the True Cross)", "am": "መስቀል"},
    (5,  11): {"en": "Timkat (Ethiopian Epiphany)",         "am": "ጥምቀት"},
    (4,  29):  {"en": "Leddet (Ethiopian Christmas)",       "am": "ልደት (የኢትዮጵያ ገና)"},
    (6,  23): {"en": "Adwa Victory Day",                   "am": "የዓድዋ ድል ቀን"},
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
                print(f"S3 read error: {e}")
            _users_cache = {}
        except Exception as e:
            print(f"S3 error: {e}")
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
            print(f"S3 write error: {e}")
    else:
        try:
            with open(USERS_FILE, "w") as f:
                f.write(payload.decode())
        except IOError as e:
            print(f"Local write error: {e}")


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
#
#  Rules:
#   - Each conversion direction gets its own full-width row so the label never clips
#   - Today + Holidays share one row (short labels)
#   - Help + Language share one row (meta/settings)
#   - After a result, two full-width rows so nothing is cramped
#   - While waiting for input, only a Cancel button is shown

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
            [InlineKeyboardButton("🇪🇹 ኢትዮጵያ  ➜  🌍 ግሪጎሪያን", callback_data="mode:E2G")],
            [InlineKeyboardButton("🌍 ግሪጎሪያን  ➜  🇪🇹 ኢትዮጵያ", callback_data="mode:G2E")],
            [
                InlineKeyboardButton("📅 ዛሬ",      callback_data="action:today"),
                InlineKeyboardButton("🗓 በዓሎች",    callback_data="action:holidays"),
            ],
            [
                InlineKeyboardButton("ℹ️ እገዛ",     callback_data="action:help"),
                InlineKeyboardButton("🌐 ቋንቋ",     callback_data="action:changelang"),
            ],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🇪🇹 Ethiopian  ➜  🌍 Gregorian", callback_data="mode:E2G")],
            [InlineKeyboardButton("🌍 Gregorian  ➜  🇪🇹 Ethiopian", callback_data="mode:G2E")],
            [
                InlineKeyboardButton("📅 Today",    callback_data="action:today"),
                InlineKeyboardButton("🗓 Holidays", callback_data="action:holidays"),
            ],
            [
                InlineKeyboardButton("ℹ️ Help",     callback_data="action:help"),
                InlineKeyboardButton("🌐 Language", callback_data="action:changelang"),
            ],
        ])


def cancel_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    label = "❌  ሰርዝ — ወደ ምናሌ ተመለስ" if lang == "am" else "❌  Cancel — back to menu"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="action:cancel")]
    ])


def after_result_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    if lang == "am":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄  ሌላ ቀን ቀይር",  callback_data="action:convert_again")],
            [InlineKeyboardButton("🏠  ዋና ምናሌ",      callback_data="action:cancel")],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄  Convert Another Date", callback_data="action:convert_again")],
            [InlineKeyboardButton("🏠  Back to Main Menu",    callback_data="action:cancel")],
        ])


# ─── UI text strings ──────────────────────────────────────────────────────────
# All messages use plain Markdown (parse_mode="Markdown") only.
# Allowed: *bold*, _italic_, `code`, [link](url)  — nothing else.
# No parentheses, dots, dashes, or special chars need escaping in plain Markdown.

TEXT = {
    "en": {
        "welcome": (
            "👋 *Welcome to the Ethiopian Date Converter!*\n\n"
            "I can help you:\n"
            "🔁  Convert dates between Ethiopian and Gregorian calendars\n"
            "📅  Show today's date in both calendars\n"
            "🗓  List all Ethiopian public holidays\n\n"
            "Please choose your language:"
        ),
        "choose": (
            "✅ *Language set to English.*\n\n"
            "Choose what you would like to do:"
        ),

        # ── Date input prompts ──
        "ask_e": (
            "🇪🇹 *ETHIOPIAN  →  GREGORIAN CONVERSION*\n"
            "────────────────────────────\n\n"
            "📌 *ABOUT THE ETHIOPIAN CALENDAR*\n\n"
            "  • 13 months total\n"
            "  • Months 1 to 12 have 30 days each\n"
            "  • Month 13 (Pagume / ጳጉሜ) has 5 days\n"
            "    (6 days in a leap year)\n"
            "  • Ethiopian year is about 7-8 years\n"
            "    behind the Gregorian year\n\n"
            "────────────────────────────\n\n"
            "⌨️ *USE YOUR KEYBOARD AND TYPE THE DATE BELOW*\n\n"
            "  FORMAT   →   `YEAR/MONTH/DAY`\n\n"
            "  EXAMPLE  →   `2017/4/27`\n\n"
            "────────────────────────────\n"
            "_Tap Cancel below to go back._"
        ),
        "ask_g": (
            "🌍 *GREGORIAN  →  ETHIOPIAN CONVERSION*\n"
            "────────────────────────────\n\n"
            "⌨️ *USE YOUR KEYBOARD AND TYPE THE DATE BELOW*\n\n"
            "  FORMAT   →   `YEAR/MONTH/DAY`\n\n"
            "  EXAMPLE  →   `2025/1/5`\n\n"
            "────────────────────────────\n"
            "_Tap Cancel below to go back._"
        ),

        # ── Errors ──
        "unrecognised_lang": "🤔 Please pick your language using the buttons below:",
        "unrecognised_mode": "🤔 Please choose an option from the menu below:",
        "unrecognised_date": (
            "⚠️ *THAT DOES NOT LOOK LIKE A DATE*\n\n"
            "⌨️ TYPE YOUR DATE LIKE THIS:\n\n"
            "  FORMAT   →   `YEAR/MONTH/DAY`\n"
            "  EXAMPLE  →   `{}`\n\n"
            "_Tap Cancel to return to the menu._"
        ),
        "format_error": (
            "⚠️ *WRONG FORMAT — NUMBERS ONLY, SEPARATED BY /*\n\n"
            "⌨️ TRY AGAIN:\n\n"
            "  FORMAT   →   `YEAR/MONTH/DAY`\n"
            "  EXAMPLE  →   `{}`\n\n"
            "_Tap Cancel to return to the menu._"
        ),
        "conversion_error": (
            "❌ *INVALID DATE*\n\n"
            "_{}_\n\n"
            "⌨️ Please correct the date and try again.\n"
            "_Tap Cancel to return to the menu._"
        ),

        # ── Results ──
        "e2g": (
            "✅ *CONVERSION COMPLETE*\n"
            "────────────────────\n\n"
            "🇪🇹 *ETHIOPIAN DATE* (input)\n\n"
            "  {}\n\n"
            "────────────────────\n\n"
            "🌍 *GREGORIAN DATE* (result)\n\n"
            "  {}\n\n"
            "────────────────────"
        ),
        "g2e": (
            "✅ *CONVERSION COMPLETE*\n"
            "────────────────────\n\n"
            "🌍 *GREGORIAN DATE* (input)\n\n"
            "  {}\n\n"
            "──────────────────\n\n"
            "🇪🇹 *ETHIOPIAN DATE* (result)\n\n"
            "  {}\n\n"
            "──────────────────"
        ),

        # ── Today ──
        "today": (
            "📅 *TODAY'S DATE*\n"
            "─────────────────────\n\n"
            "🌍 *Gregorian*\n\n"
            "  {}\n\n"
            "────────────────────\n\n"
            "🇪🇹 *Ethiopian*\n\n"
            "  {}\n\n"
            "────────────────────\n\n"
            "📆 *Day of the week:*  {}\n\n"
            "────────────────────\n"
            "{}"
        ),
        "holiday_notice": "\n🎉 *TODAY IS A HOLIDAY*\n\n  {}",

        # ── Holidays list ──
        "holidays": (
            "🗓 *ETHIOPIAN PUBLIC HOLIDAYS*\n"
            "────────────────────\n\n"
            "{}\n\n"
            "───────────────────"
        ),
        "no_holidays": "No holidays found.",

        # ── Help ──
        "help": (
            "ℹ️ *ETHIOPIAN DATE CONVERTER — HELP*\n"
            "────────────────────────────\n\n"
            "*HOW TO CONVERT A DATE*\n\n"
            "  1.  Tap a conversion direction button\n"
            "  2.  Use your keyboard to type the date:\n"
            "      `YEAR/MONTH/DAY`\n"
            "  3.  Receive your result instantly\n\n"
            "────────────────────────────\n\n"
            "*QUICK ACTIONS*\n\n"
            "  📅  Today — see today in both calendars\n"
            "  🗓  Holidays — all Ethiopian public holidays\n\n"
            "────────────────────────────\n\n"
            "*ETHIOPIAN CALENDAR FACTS*\n\n"
            "  • 13 months — months 1 to 12 have 30 days each\n"
            "  • Month 13 (Pagume) has 5 days (6 in a leap year)\n"
            "  • Ethiopian year is about 7-8 years behind Gregorian\n\n"
            "────────────────────────────\n\n"
            "*EXAMPLE CONVERSIONS*\n\n"
            "  🇪🇹 `2017/4/27`  →  🌍 January 5, 2025\n"
            "  🌍 `2025/1/5`   →  🇪🇹 27 Miyazia 2017\n\n"
            "────────────────────────────\n\n"
            "*COMMANDS*\n\n"
            "  /start  — restart the bot\n"
            "  /help   — show this message\n"
            "  /today  — today's date in both calendars"
        ),

        "cancelled":       "↩️  Cancelled. What would you like to do?",
        "change_language": "Choose your language:",
        "not_admin":       "⛔ This command is only available to administrators.",
        "stats": (
            "📊 *Bot Statistics*\n\n"
            "👥 Total unique users: *{}*\n"
            "🆔 Your user ID: `{}`\n"
            "💾 Storage: {}"
        ),
        "users_list":       "👥 *Registered Users* ({}) — newest first\n\n{}",
        "users_list_empty": "👥 No users registered yet.",
    },

    "am": {
        "welcome": (
            "👋 *እንኳን ደህና መጡ! የኢትዮጵያ ቀን መቀየሪያ!*\n\n"
            "የሚያደርጉት:\n"
            "🔁  በኢትዮጵያ እና ግሪጎሪያን ካላንደሮች መካከል ቀናትን መቀየር\n"
            "📅  ዛሬን ቀን ማሳየት\n"
            "🗓  የኢትዮጵያ ብሔራዊ በዓሎችን ማሳየት\n\n"
            "ቋንቋ ይምረጡ:"
        ),
        "choose": (
            "✅ *ቋንቋ አማርኛ ተመርጧል።*\n\n"
            "ምን ማድረግ ይፈልጋሉ?"
        ),

        "ask_e": (
            "🇪🇹 *ኢትዮጵያ  →  ግሪጎሪያን ቀን ለወጥ*\n"
            "────────────────────────────\n\n"
            "📌 *ስለ ኢትዮጵያ ካላንደር*\n\n"
            "  • 13 ወሮች አሉ\n"
            "  • ወር 1 እስከ 12 እያንዳንዳቸው 30 ቀናት\n"
            "  • ወር 13 (ጳጉሜ) 5 ቀናት (ዘመነ ሉቃስ 6)\n"
            "  • የኢትዮጵያ ዓ.ም ከግሪጎሪያን ~7-8 ዓመት ወደ ኋላ\n\n"
            "────────────────────────────\n\n"
            "⌨️ *ቁልፍ ሰሌዳዎን ይጠቀሙ — ቀን ያስገቡ*\n\n"
            "  ቅጽ    →   `ዓ.ም/ወር/ቀን`\n\n"
            "  ምሳሌ   →   `2017/4/27`\n\n"
            "────────────────────────────\n"
            "_ለቀደም ለመመለስ ሰርዝ ይጫኑ።_"
        ),
        "ask_g": (
            "🌍 *ግሪጎሪያን  →  ኢትዮጵያ ቀን ለወጥ*\n"
            "────────────────────────────\n\n"
            "⌨️ *ቁልፍ ሰሌዳዎን ይጠቀሙ — ቀን ያስገቡ*\n\n"
            "  ቅጽ    →   `ዓ.ም/ወር/ቀን`\n\n"
            "  ምሳሌ   →   `2025/1/5`\n\n"
            "────────────────────────────\n"
            "_ለቀደም ለመመለስ ሰርዝ ይጫኑ።_"
        ),

        "unrecognised_lang": "🤔 ቋንቋ ይምረጡ:",
        "unrecognised_mode": "🤔 ከታቹ ያሉ አዝራሮችን ይምረጡ:",
        "unrecognised_date": (
            "⚠️ *ያስገቡት ቀን አይደለም*\n\n"
            "⌨️ ቀኑን እንደዚህ ያስገቡ:\n\n"
            "  ቅጽ    →   `ዓ.ም/ወር/ቀን`\n"
            "  ምሳሌ   →   `{}`\n\n"
            "_ለቀደም ለመመለስ ሰርዝ ይጫኑ።_"
        ),
        "format_error": (
            "⚠️ *ቅጹ ተሳስቷል — ቁጥሮች ብቻ፣ በ / ይለዩ*\n\n"
            "⌨️ እንደገና ሞክሩ:\n\n"
            "  ቅጽ    →   `ዓ.ም/ወር/ቀን`\n"
            "  ምሳሌ   →   `{}`\n\n"
            "_ለቀደም ለመመለስ ሰርዝ ይጫኑ።_"
        ),
        "conversion_error": (
            "❌ *ቀኑ ልክ አይደለም*\n\n"
            "_{}_\n\n"
            "⌨️ ቀኑን አርመው እንደገና ሞክሩ።\n"
            "_ለቀደም ለመመለስ ሰርዝ ይጫኑ።_"
        ),

        "e2g": (
            "✅ *ቀን ተቀይሯል*\n"
            "────────────────────────────\n\n"
            "🇪🇹 *የኢትዮጵያ ቀን* (ያስገቡት)\n\n"
            "  {}\n\n"
            "────────────────────────────\n\n"
            "🌍 *የግሪጎሪያን ቀን* (ውጤት)\n\n"
            "  {}\n\n"
            "────────────────────────────"
        ),
        "g2e": (
            "✅ *ቀን ተቀይሯል*\n"
            "────────────────────────────\n\n"
            "🌍 *የግሪጎሪያን ቀን* (ያስገቡት)\n\n"
            "  {}\n\n"
            "────────────────────────────\n\n"
            "🇪🇹 *የኢትዮጵያ ቀን* (ውጤት)\n\n"
            "  {}\n\n"
            "────────────────────────────"
        ),

        "today": (
            "📅 *ዛሬ*\n"
            "────────────────────────────\n\n"
            "🌍 *ግሪጎሪያን*\n\n"
            "  {}\n\n"
            "────────────────────────────\n\n"
            "🇪🇹 *ኢትዮጵያ*\n\n"
            "  {}\n\n"
            "────────────────────────────\n\n"
            "📆 *የሳምንቱ ቀን:*  {}\n\n"
            "────────────────────────────\n"
            "{}"
        ),
        "holiday_notice": "\n🎉 *ዛሬ በዓል ነው*\n\n  {}",

        "holidays": (
            "🗓 *የኢትዮጵያ ብሔራዊ በዓሎች*\n"
            "────────────────────────────\n\n"
            "{}\n\n"
            "────────────────────────────"
        ),
        "no_holidays": "በዓሎች አልተገኙም።",

        "help": (
            "ℹ️ *የኢትዮጵያ ቀን መቀየሪያ — እገዛ*\n"
            "────────────────────────────\n\n"
            "*አጠቃቀም*\n\n"
            "  1.  የቀን ለወጥ አቅጣጫ ይምረጡ\n"
            "  2.  ቁልፍ ሰሌዳዎን ተጠቅመው ቀን ያስገቡ:\n"
            "      `ዓ.ም/ወር/ቀን`\n"
            "  3.  የተቀየረ ቀን ይቀበሉ\n\n"
            "────────────────────────────\n\n"
            "*ፈጣን አማራጮች*\n\n"
            "  📅  ዛሬ — ዛሬን ቀን ይመልከቱ\n"
            "  🗓  በዓሎች — ሁሉም ብሔራዊ በዓሎች\n\n"
            "────────────────────────────\n\n"
            "*ምሳሌዎች*\n\n"
            "  🇪🇹 `2017/4/27`  →  🌍 January 5, 2025\n"
            "  🌍 `2025/1/5`   →  🇪🇹 27 ሚያዝያ 2017\n\n"
            "────────────────────────────\n\n"
            "*ትዕዛዞች*\n\n"
            "  /start  — ዳግም ጀምር\n"
            "  /help   — ይህን አሳይ\n"
            "  /today  — ዛሬ"
        ),

        "cancelled":       "↩️  ተሰርዟል። ምን ማድረግ ይፈልጋሉ?",
        "change_language": "ቋንቋ ይምረጡ:",
        "not_admin":       "⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።",
        "stats": (
            "📊 *የቦት አኃዛዊ መረጃ*\n\n"
            "👥 ጠቅላላ ልዩ ተጠቃሚዎች: *{}*\n"
            "🆔 የእርስዎ ተጠቃሚ መለያ: `{}`\n"
            "💾 ማከማቻ: {}"
        ),
        "users_list":       "👥 *ምዝገባ ተጠቃሚዎች* ({}) — በምዝገባ ቅደም ተከተል\n\n{}",
        "users_list_empty": "👥 ምንም ተጠቃሚ ገና አልመዘገቡም።",
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
    now = datetime.now(timezone.utc)
    gy, gm, gd = now.year, now.month, now.day
    ey, em, ed = EthiopianDateConverter.to_ethiopian(gy, gm, gd)
    weekday_idx = now.weekday()
    return {
        "greg_str":   format_gregorian(gy, gm, gd),
        "eth_str":    format_ethiopian(ey, em, ed),
        "weekday_en": ETH_WEEKDAYS_EN[weekday_idx],
        "weekday_am": ETH_WEEKDAYS_AM[weekday_idx],
        "eth_month":  em,
        "eth_day":    ed,
    }


def get_holiday_for_eth_date(eth_m: int, eth_d: int, lang: str) -> str | None:
    h = ETH_HOLIDAYS.get((eth_m, eth_d))
    if h:
        return h.get(lang, h["en"])
    return None


def build_holidays_text(lang: str) -> str:
    lines = []
    for (em, ed), names in sorted(ETH_HOLIDAYS.items()):
        eth_month_en = ETH_MONTHS_EN[em - 1]
        eth_month_am = ETH_MONTHS_AM[em - 1]
        name = names.get(lang, names["en"])
        if lang == "am":
            lines.append(f"🎉 *{name}*\n  📌 {ed} {eth_month_am} ({eth_month_en})")
        else:
            lines.append(f"🎉 *{name}*\n  📌 {ed} {eth_month_en}")
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
    return f"{index}. {first_name} — {link}"


# ─── Shared reply helper ──────────────────────────────────────────────────────

async def reply(update: Update, text: str, keyboard: InlineKeyboardMarkup):
    """Send a plain-Markdown message. Works from both command and callback contexts."""
    msg = update.message if update.message else update.callback_query.message
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ─── Command handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    is_new = add_user(user.id, username=user.username, first_name=user.first_name)
    if is_new:
        print(f"New user: {user.id} (@{user.username}) — Total: {get_user_count()}")
    context.user_data.clear()
    await reply(update, TEXT["en"]["welcome"], lang_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(context)
    kb   = main_keyboard(lang) if "lang" in context.user_data else lang_keyboard()
    await reply(update, TEXT[lang]["help"], kb)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang    = lang_of(context)
    today   = get_today_both_calendars()
    weekday = today["weekday_am"] if lang == "am" else today["weekday_en"]
    holiday = get_holiday_for_eth_date(today["eth_month"], today["eth_day"], lang)
    holiday_line = TEXT[lang]["holiday_notice"].format(holiday) if holiday else ""
    text = TEXT[lang]["today"].format(
        today["greg_str"], today["eth_str"], weekday, holiday_line
    )
    kb = main_keyboard(lang) if "lang" in context.user_data else lang_keyboard()
    await reply(update, text, kb)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang    = lang_of(context)
    if not is_admin(user_id):
        await reply(update, TEXT[lang]["not_admin"], main_keyboard(lang))
        return
    storage = f"S3 ({AWS_BUCKET})" if USE_S3 else "Local (not persistent)"
    await reply(update, TEXT[lang]["stats"].format(get_user_count(), user_id, storage), main_keyboard(lang))


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang    = lang_of(context)
    if not is_admin(user_id):
        await reply(update, TEXT[lang]["not_admin"], main_keyboard(lang))
        return

    all_users = get_all_users()
    if not all_users:
        await reply(update, TEXT[lang]["users_list_empty"], main_keyboard(lang))
        return

    sorted_users = sorted(
        all_users.items(), key=lambda item: item[1].get("t", 0), reverse=True
    )

    MAX_CHARS, pages, current_lines, current_len = 4000, [], [], 0
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
            header = header.rstrip() + f" (page {i+1}/{len(pages)})\n\n"
        await update.message.reply_text(
            header + page, parse_mode="Markdown", disable_web_page_preview=True
        )


# ─── Callback query handler ───────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    lang = lang_of(context)

    if data.startswith("lang:"):
        chosen = data.split(":")[1]
        context.user_data["lang"] = chosen
        lang = chosen
        await query.message.reply_text(
            TEXT[lang]["choose"], parse_mode="Markdown", reply_markup=main_keyboard(lang)
        )
        return

    if data.startswith("mode:"):
        mode = data.split(":")[1]
        context.user_data["mode"] = mode
        prompt = TEXT[lang]["ask_e"] if mode == "E2G" else TEXT[lang]["ask_g"]
        await query.message.reply_text(
            prompt, parse_mode="Markdown", reply_markup=cancel_keyboard(lang)
        )
        return

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
        await query.message.reply_text(
            TEXT[lang]["holidays"].format(build_holidays_text(lang)),
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

    if data == "action:convert_again":
        mode = context.user_data.get("last_mode", "E2G")
        context.user_data["mode"] = mode
        prompt = TEXT[lang]["ask_e"] if mode == "E2G" else TEXT[lang]["ask_g"]
        await query.message.reply_text(
            prompt, parse_mode="Markdown", reply_markup=cancel_keyboard(lang)
        )
        return


# ─── Text message handler ─────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lang = lang_of(context)

    if "lang" not in context.user_data:
        await reply(update, TEXT["en"]["unrecognised_lang"], lang_keyboard())
        return

    if "mode" not in context.user_data:
        await reply(update, TEXT[lang]["unrecognised_mode"], main_keyboard(lang))
        return

    mode    = context.user_data["mode"]
    example = EXAMPLE_DATE[mode]

    if not looks_like_date(text):
        await reply(update, TEXT[lang]["unrecognised_date"].format(example), cancel_keyboard(lang))
        return

    try:
        y, m, d = parse_slash_date(text)

        if mode == "E2G":
            g        = EthiopianDateConverter.to_gregorian(y, m, d)
            eth_str  = format_ethiopian(y, m, d)
            greg_str = format_gregorian(g.year, g.month, g.day)
            result   = TEXT[lang]["e2g"].format(eth_str, greg_str)
            holiday  = get_holiday_for_eth_date(m, d, lang)
        else:
            ey, em, ed = EthiopianDateConverter.to_ethiopian(y, m, d)
            greg_str   = format_gregorian(y, m, d)
            eth_str    = format_ethiopian(ey, em, ed)
            result     = TEXT[lang]["g2e"].format(greg_str, eth_str)
            holiday    = get_holiday_for_eth_date(em, ed, lang)

        if holiday:
            result += TEXT[lang]["holiday_notice"].format(holiday)

        context.user_data["last_mode"] = mode
        context.user_data.pop("mode", None)

        await reply(update, result, after_result_keyboard(lang))

    except ValueError as e:
        msg = str(e)
        error = (
            TEXT[lang]["format_error"].format(example)
            if "3 parts" in msg or "must be numbers" in msg
            else TEXT[lang]["conversion_error"].format(msg)
        )
        await reply(update, error, cancel_keyboard(lang))

    except Exception as e:
        await reply(
            update,
            TEXT[lang]["conversion_error"].format(f"Unexpected error: {e}"),
            cancel_keyboard(lang),
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

    print("🤖 Bot is running… Press Ctrl+C to stop.")
    app.run_polling()
