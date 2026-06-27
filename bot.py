import os
import json
import time
import re
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
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

# ═══════════════════════════════════════════════════════════════════
#   Setup
# ═══════════════════════════════════════════════════════════════════

load_dotenv()
BOT_TOKEN      = os.getenv("T_BOT_TOKEN")
ADMIN_USER_ID  = os.getenv("ADMIN_USER_ID")

AWS_ENDPOINT_URL      = os.getenv("AWS_ENDPOINT_URL")
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME    = os.getenv("AWS_S3_BUCKET_NAME")
AWS_DEFAULT_REGION    = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

if not BOT_TOKEN:
    raise RuntimeError("T_BOT_TOKEN not set")

USE_S3 = all([AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME])

if USE_S3:
    s3_client = boto3.client(
        's3',
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_DEFAULT_REGION,
    )
    print(f"✅ S3 storage configured: {AWS_S3_BUCKET_NAME}")
else:
    print("⚠️  S3 not configured - using local file storage (not persistent on Railway!)")
    s3_client = None

USERS_FILE = "users.json"
_users_cache: dict | None = None

# ═══════════════════════════════════════════════════════════════════
#   Month data
# ═══════════════════════════════════════════════════════════════════

ETH_MONTHS_AM = [
    "መስከረም", "ጥቅምት", "ኅዳር", "ታህሳስ",
    "ጥር", "የካቲት", "መጋቢት", "ሚያዝያ",
    "ግንቦት", "ሰኔ", "ሐምሌ", "ነሐሴ", "ጳጉሜ",
]
ETH_MONTHS_EN = [
    "Meskerem", "Tikimt", "Hidar", "Tahsas",
    "Tir", "Yekatit", "Megabit", "Miyazya",
    "Ginbot", "Sene", "Hamle", "Nehase", "Pagume",
]

GREG_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Ethiopian month number → Gregorian month name it overlaps with
ETH_TO_GREG_MONTH_NAME = {
    1: "September", 2: "October",  3: "November", 4: "December",
    5: "January",   6: "February", 7: "March",    8: "April",
    9: "May",      10: "June",    11: "July",     12: "August",
    13: "Pagume (Sep)",
}

# Amharic Ge'ez numerals → Arabic
GEEZ_MAP = {
    "፩": 1,  "፪": 2,  "፫": 3,  "፬": 4,  "፭": 5,
    "፮": 6,  "፯": 7,  "፰": 8,  "፱": 9,  "፲": 10,
    "፳": 20, "፴": 30, "፵": 40, "፶": 50,
    "፷": 60, "፸": 70, "፹": 80, "፺": 90,
    "፻": 100,"፼": 10000,
}

EXAMPLE_DATE = {"E2G": "2017/4/27", "G2E": "2025/1/5"}

# ═══════════════════════════════════════════════════════════════════
#   Did-you-know tips  (shown every 5th successful conversion)
# ═══════════════════════════════════════════════════════════════════

TIPS = [
    "💡 *Tip:* The Ethiopian New Year (Enkutatash) falls on September 11th in the Gregorian calendar — or September 12th in a Gregorian leap year.",
    "💡 *Tip:* Ethiopian year numbers are currently 7–8 years behind Gregorian. So Gregorian 2025 = Ethiopian 2017.",
    "💡 *Tip:* You can switch conversion direction anytime using the buttons below — no need to restart.",
    "💡 *Tip:* Month 13 (ጳጉሜ / Pagume) is the short 13th month — 5 days normally, 6 in an Ethiopian leap year.",
    "💡 *Tip:* Ethiopian months 1–12 all have exactly 30 days, making the calendar very regular.",
    "💡 *Tip:* The Ethiopian calendar is based on the ancient Alexandrian calendar, similar to the Coptic calendar.",
]

# ═══════════════════════════════════════════════════════════════════
#   User store
# ═══════════════════════════════════════════════════════════════════

def load_users() -> dict:
    global _users_cache
    if _users_cache is not None:
        return _users_cache
    if USE_S3:
        try:
            response = s3_client.get_object(Bucket=AWS_S3_BUCKET_NAME, Key=USERS_FILE)
            raw = json.loads(response['Body'].read().decode('utf-8'))
            _users_cache = raw.get("users", {})
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchKey':
                print(f"Error loading users from S3: {e}")
            _users_cache = {}
        except Exception as e:
            print(f"Unexpected error loading users from S3: {e}")
            _users_cache = {}
    else:
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r") as f:
                    raw = json.load(f)
                    _users_cache = raw.get("users", {})
            except (json.JSONDecodeError, IOError):
                _users_cache = {}
        else:
            _users_cache = {}
    return _users_cache


def save_users(users_dict: dict):
    global _users_cache
    _users_cache = users_dict
    json_data = json.dumps({"users": users_dict}, separators=(',', ':'))
    if USE_S3:
        try:
            s3_client.put_object(
                Bucket=AWS_S3_BUCKET_NAME, Key=USERS_FILE,
                Body=json_data.encode('utf-8'), ContentType='application/json',
            )
        except Exception as e:
            print(f"Error saving users to S3: {e}")
    else:
        try:
            with open(USERS_FILE, "w") as f:
                f.write(json_data)
        except IOError as e:
            print(f"Error saving users locally: {e}")


def add_user(user_id: int, username: str = None, first_name: str = None) -> bool:
    users = load_users()
    key   = str(user_id)
    if key in users:
        return False
    record: dict = {"t": int(time.time())}
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
    if not ADMIN_USER_ID:
        return False
    return str(user_id) == str(ADMIN_USER_ID)

# ═══════════════════════════════════════════════════════════════════
#   Keyboards
# ═══════════════════════════════════════════════════════════════════

LANG_KEYBOARD = ReplyKeyboardMarkup(
    [["English 🇬🇧", "አማርኛ 🇪🇹"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

CONVERT_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🇪🇹 Ethiopian → 🌍 Gregorian", "🌍 Gregorian → 🇪🇹 Ethiopian"],
        ["📝 Feedback", "🌐 Change Language"],
    ],
    resize_keyboard=True,
)

# Shown while bot is waiting for date input — adds ❌ Cancel
WAITING_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🇪🇹 Ethiopian → 🌍 Gregorian", "🌍 Gregorian → 🇪🇹 Ethiopian"],
        ["❌ Cancel", "🌐 Change Language"],
    ],
    resize_keyboard=True,
)

STAR_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("⭐ 1", callback_data="rating:1"),
    InlineKeyboardButton("⭐ 2", callback_data="rating:2"),
    InlineKeyboardButton("⭐ 3", callback_data="rating:3"),
    InlineKeyboardButton("⭐ 4", callback_data="rating:4"),
    InlineKeyboardButton("⭐ 5", callback_data="rating:5"),
]])

SKIP_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("⏭ Skip — submit now", callback_data="feedback:skip"),
]])

# Inline "show month guide" buttons — one for each mode
MONTH_GUIDE_ETH_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("📅 Show Ethiopian month guide", callback_data="guide:eth"),
]])
MONTH_GUIDE_GREG_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("📅 Show Gregorian month guide", callback_data="guide:greg"),
]])

# ═══════════════════════════════════════════════════════════════════
#   UI Text
# ═══════════════════════════════════════════════════════════════════

TEXT = {
    "en": {
        # ── Onboarding ────────────────────────────────────────────
        "welcome": (
            "👋 *Welcome to the Ethiopian Date Converter!*\n\n"
            "🗓 This bot converts dates between the *Ethiopian* and *Gregorian* calendars instantly.\n\n"
            "📌 *Quick example:*\n"
            "Ethiopian 2017/4/27 = Gregorian January 5, 2025\n\n"
            "The Ethiopian calendar has *13 months* and is currently *7–8 years behind* the Gregorian calendar.\n\n"
            "Please choose your language to get started:"
        ),
        "choose": (
            "✅ Language set to English.\n\n"
            "Choose a conversion direction below:"
        ),

        # ── Date prompts ─────────────────────────────────────────
        "ask_e": (
            "📥 *Enter an Ethiopian date:*\n"
            "Format: `YYYY/MM/DD`\n\n"
            "📌 Example: `2017/4/27`\n"
            "_(Ethiopian year 2017, month 4 = ታህሳስ/Tahsas = December, day 27)_\n\n"
            "⚠️ *Remember:* Ethiopian month 1 is *September*, not January.\n\n"
            "Tap the button below if you need the full month guide:"
        ),
        "ask_g": (
            "📥 *Enter a Gregorian date:*\n"
            "Format: `YYYY/MM/DD`\n\n"
            "📌 Example: `2025/1/5`\n"
            "_(Gregorian year 2025, month 1 = January, day 5)_\n\n"
            "⚠️ *Remember:* Gregorian month 1 is *January*. The Ethiopian equivalent of month 1 is *September*.\n\n"
            "Tap the button below if you need the full month guide:"
        ),

        # ── Month guides (shown on demand via inline button) ──────
        "guide_eth": (
            "📅 *Ethiopian Month Guide*\n"
            "_(No. → Amharic / English → Gregorian overlap)_\n\n"
            " 1  → መስከረም / Meskerem  ≈ *September*\n"
            " 2  → ጥቅምት  / Tikimt    ≈ *October*\n"
            " 3  → ኅዳር   / Hidar     ≈ *November*\n"
            " 4  → ታህሳስ  / Tahsas    ≈ *December*\n"
            " 5  → ጥር    / Tir       ≈ *January*\n"
            " 6  → የካቲት / Yekatit   ≈ *February*\n"
            " 7  → መጋቢት / Megabit   ≈ *March*\n"
            " 8  → ሚያዝያ / Miyazya   ≈ *April*\n"
            " 9  → ግንቦት / Ginbot    ≈ *May*\n"
            "10  → ሰኔ   / Sene      ≈ *June*\n"
            "11  → ሐምሌ  / Hamle     ≈ *July*\n"
            "12  → ነሐሴ  / Nehase    ≈ *August*\n"
            "13  → ጳጉሜ  / Pagume    ≈ *September* (5–6 days only)\n\n"
            "💡 So if you want month 1, you are entering *September* in Gregorian terms."
        ),
        "guide_greg": (
            "📅 *Gregorian Month Guide*\n"
            "_(No. → Month → Ethiopian equivalent)_\n\n"
            " 1  → *January*    ≈ Ethiopian month 5 (ጥር / Tir)\n"
            " 2  → *February*   ≈ Ethiopian month 6 (የካቲት / Yekatit)\n"
            " 3  → *March*      ≈ Ethiopian month 7 (መጋቢት / Megabit)\n"
            " 4  → *April*      ≈ Ethiopian month 8 (ሚያዝያ / Miyazya)\n"
            " 5  → *May*        ≈ Ethiopian month 9 (ግንቦት / Ginbot)\n"
            " 6  → *June*       ≈ Ethiopian month 10 (ሰኔ / Sene)\n"
            " 7  → *July*       ≈ Ethiopian month 11 (ሐምሌ / Hamle)\n"
            " 8  → *August*     ≈ Ethiopian month 12 (ነሐሴ / Nehase)\n"
            " 9  → *September*  ≈ Ethiopian month 1 (መስከረም / Meskerem)\n"
            "10  → *October*    ≈ Ethiopian month 2 (ጥቅምት / Tikimt)\n"
            "11  → *November*   ≈ Ethiopian month 3 (ኅዳር / Hidar)\n"
            "12  → *December*   ≈ Ethiopian month 4 (ታህሳስ / Tahsas)\n\n"
            "💡 So if you want month 1, you are entering *January* — which is *Tir* (month 5) in Ethiopian."
        ),

        # ── Errors ───────────────────────────────────────────────
        "unrecognised_lang": (
            "🤔 I didn't understand that.\n\n"
            "Please pick your language using the buttons below:"
        ),
        "unrecognised_mode": (
            "🤔 I didn't understand that.\n\n"
            "Please choose a conversion direction using the buttons below:"
        ),
        "unrecognised_date": (
            "🤔 That doesn't look like a date.\n\n"
            "Please enter the date as `YYYY/MM/DD`\n"
            "📌 Example: `{}`\n\n"
            "Or pick a different option from the menu below."
        ),
        "format_error": (
            "❌ *Wrong format.*\n\n"
            "Use `YYYY/MM/DD` — numbers only, separated by `/`\n"
            "📌 Example: `{}`\n\n"
            "Please try again, or pick a different option below."
        ),
        "format_error_autofix": (
            "⚠️ I noticed you used `{sep}` instead of `/` — I've corrected it automatically.\n\n"
        ),
        "reversed_date_hint": (
            "🤔 *Possible reversed date?*\n\n"
            "You entered `{input}` — but month *{m}* doesn't exist "
            "(max is 13 for Ethiopian, 12 for Gregorian).\n\n"
            "Did you mean `{suggestion}`? _(day/month swapped)_\n\n"
            "Please re-enter the correct date as `YYYY/MM/DD`:"
        ),
        "geez_numeral_hint": (
            "🤔 It looks like you used *Ethiopic (Ge'ez) numerals*.\n\n"
            "This bot currently accepts standard Arabic numerals only.\n"
            "📌 Please enter your date like this: `2017/4/27`"
        ),
        "conversion_error": (
            "❌ *Invalid date:*\n\n"
            "{}\n\n"
            "Please correct the date and try again, or pick a different option below."
        ),
        "cancelled": "✅ Cancelled. Choose a conversion direction:",

        # ── Success ───────────────────────────────────────────────
        "e2g": (
            "✅ *Ethiopian date:*\n"
            "{eth}\n\n"
            "➡️ *Gregorian date:*\n"
            "{greg}\n\n"
            "Want to convert another date? Choose below:"
        ),
        "g2e": (
            "✅ *Gregorian date:*\n"
            "{greg}\n\n"
            "➡️ *Ethiopian date:*\n"
            "{eth}\n\n"
            "Want to convert another date? Choose below:"
        ),

        # ── Help ──────────────────────────────────────────────────
        "help": (
            "ℹ️ *Ethiopian Date Converter — Help*\n\n"
            "*How to use:*\n"
            "1️⃣ Choose a conversion direction\n"
            "2️⃣ Type your date as `YYYY/MM/DD`\n"
            "3️⃣ Receive the converted date instantly\n\n"
            "*Ethiopian calendar facts:*\n"
            "• 13 months total\n"
            "• Months 1–12 each have 30 days\n"
            "• Month 13 (ጳጉሜ/Pagume) has 5 days (6 in a leap year)\n"
            "• Ethiopian month *1 = September* (not January!)\n"
            "• Ethiopian year is ~7–8 years behind Gregorian\n\n"
            "*Examples:*\n"
            "• Ethiopian `2017/4/27` → Gregorian January 5, 2025\n"
            "• Gregorian `2025/1/5` → Ethiopian 2017/4/27\n\n"
            "*The bot also accepts:*\n"
            "• Dashes: `2017-4-27` → auto-fixed to `2017/4/27`\n"
            "• Dots: `2017.4.27` → auto-fixed to `2017/4/27`\n\n"
            "*Commands:*\n"
            "/start — restart the bot\n"
            "/help — show this message\n"
            "/cancel — cancel current input\n"
            "/feedback — leave a rating & review"
        ),

        # ── Admin ─────────────────────────────────────────────────
        "change_language": "Choose your language:",
        "not_admin": "⛔ This command is only available to administrators.",
        "stats": (
            "📊 *Bot Statistics*\n\n"
            "👥 Total unique users: *{}*\n"
            "🆔 Your user ID: `{}`\n"
            "💾 Storage: {}"
        ),
        "users_list_header": "👥 *Registered Users* ({} total) — showing {}-{}",
        "users_list_empty": "👥 No users registered yet.",

        # ── Feedback ──────────────────────────────────────────────
        "feedback_nudge": (
            "📝 *Enjoying the bot?* We'd love to hear from you!\n"
            "Tap *📝 Feedback* below to leave a quick star rating."
        ),
        "feedback_ask_rating": (
            "📝 *We'd love your feedback!*\n\n"
            "How would you rate the Ethiopian Date Converter?\n"
            "Tap a star below:"
        ),
        "feedback_ask_text": (
            "✨ You rated us *{} star{}!*\n\n"
            "Would you like to add a comment? Type it below, "
            "or tap *Skip* to submit now."
        ),
        "feedback_thanks": (
            "🙏 *Thank you for your feedback!*\n\n"
            "Your review has been submitted. We really appreciate it! 🎉"
        ),
    },

    "am": {
        # ── Onboarding ────────────────────────────────────────────
        "welcome": (
            "👋 *እንኳን ደህና መጡ! የኢትዮጵያ ቀን መቀየሪያ!*\n\n"
            "🗓 ይህ ቦት በ*ኢትዮጵያ* እና *ግሪጎሪያን* ካላንደሮች መካከል ቀናትን ወዲያውኑ ይቀይራል።\n\n"
            "📌 *ፈጣን ምሳሌ:*\n"
            "ኢትዮ 2017/4/27 = ጃንዋሪ 5, 2025 (ግሪጎ)\n\n"
            "የኢትዮጵያ ካላንደር *13 ወሮች* አሉት እና ከግሪጎሪያን *7–8 ዓመት ወደኋላ* ነው።\n\n"
            "ቋንቋ ይምረጡ:"
        ),
        "choose": (
            "✅ ቋንቋ አማርኛ ተምርጧል።\n\n"
            "ከታቹ የመቀየሪያ አቅጣጫ ይምረጡ:"
        ),

        # ── Date prompts ──────────────────────────────────────────
        "ask_e": (
            "📥 *የኢትዮጵያ ቀን ያስገቡ:*\n"
            "ቅጽ: `YYYY/MM/DD`\n\n"
            "📌 ምሳሌ: `2017/4/27`\n"
            "_(ዓ.ም 2017፣ ወር 4 = ታህሳስ = December፣ ቀን 27)_\n\n"
            "⚠️ *ያስታውሱ:* የኢትዮጵያ ወር 1 = *መስከረም* (September) ነው — January አይደለም።\n\n"
            "የወሮች ዝርዝር ለማየት ከታቹ ያለውን አዝራር ይጫኑ:"
        ),
        "ask_g": (
            "📥 *የግሪጎሪያን ቀን ያስገቡ:*\n"
            "ቅጽ: `YYYY/MM/DD`\n\n"
            "📌 ምሳሌ: `2025/1/5`\n"
            "_(ዓ.ም 2025፣ ወር 1 = January፣ ቀን 5)_\n\n"
            "⚠️ *ያስታውሱ:* ግሪጎሪያን ወር 1 = *January* ነው። በኢትዮጵያ ካላንደር ወር 1 = *መስከረም* (September) ነው።\n\n"
            "የወሮች ዝርዝር ለማየት ከታቹ ያለውን አዝራር ይጫኑ:"
        ),

        # ── Month guides ──────────────────────────────────────────
        "guide_eth": (
            "📅 *የኢትዮጵያ ወሮች ዝርዝር*\n"
            "_(ቁጥር → አማርኛ / እንግሊዝኛ → ከግሪጎሪያን ጋር)_\n\n"
            " 1  → መስከረም / Meskerem  ≈ *September*\n"
            " 2  → ጥቅምት  / Tikimt    ≈ *October*\n"
            " 3  → ኅዳር   / Hidar     ≈ *November*\n"
            " 4  → ታህሳስ  / Tahsas    ≈ *December*\n"
            " 5  → ጥር    / Tir       ≈ *January*\n"
            " 6  → የካቲት / Yekatit   ≈ *February*\n"
            " 7  → መጋቢት / Megabit   ≈ *March*\n"
            " 8  → ሚያዝያ / Miyazya   ≈ *April*\n"
            " 9  → ግንቦት / Ginbot    ≈ *May*\n"
            "10  → ሰኔ   / Sene      ≈ *June*\n"
            "11  → ሐምሌ  / Hamle     ≈ *July*\n"
            "12  → ነሐሴ  / Nehase    ≈ *August*\n"
            "13  → ጳጉሜ  / Pagume    ≈ *September* (5–6 ቀናት)\n\n"
            "💡 ወር 1 ሲያስገቡ *September* (መስከረም) ማለት ነው።"
        ),
        "guide_greg": (
            "📅 *የግሪጎሪያን ወሮች ዝርዝር*\n"
            "_(ቁጥር → ወር → የኢትዮጵያ ዝምድና)_\n\n"
            " 1  → *January*    ≈ የኢትዮ ወር 5 (ጥር / Tir)\n"
            " 2  → *February*   ≈ የኢትዮ ወር 6 (የካቲት / Yekatit)\n"
            " 3  → *March*      ≈ የኢትዮ ወር 7 (መጋቢት / Megabit)\n"
            " 4  → *April*      ≈ የኢትዮ ወር 8 (ሚያዝያ / Miyazya)\n"
            " 5  → *May*        ≈ የኢትዮ ወር 9 (ግንቦት / Ginbot)\n"
            " 6  → *June*       ≈ የኢትዮ ወር 10 (ሰኔ / Sene)\n"
            " 7  → *July*       ≈ የኢትዮ ወር 11 (ሐምሌ / Hamle)\n"
            " 8  → *August*     ≈ የኢትዮ ወር 12 (ነሐሴ / Nehase)\n"
            " 9  → *September*  ≈ የኢትዮ ወር 1 (መስከረም / Meskerem)\n"
            "10  → *October*    ≈ የኢትዮ ወር 2 (ጥቅምት / Tikimt)\n"
            "11  → *November*   ≈ የኢትዮ ወር 3 (ኅዳር / Hidar)\n"
            "12  → *December*   ≈ የኢትዮ ወር 4 (ታህሳስ / Tahsas)\n\n"
            "💡 ወር 1 ሲያስገቡ *January* ማለት ነው — በኢትዮጵያ ወር 5 (ጥር) ነው።"
        ),

        # ── Errors ───────────────────────────────────────────────
        "unrecognised_lang": (
            "🤔 ያስገቡት ጽሑፍ አልተረዳም።\n\n"
            "እባክዎ ከታቹ ያሉ አዝራሮችን ተጠቅመው ቋንቋ ይምረጡ:"
        ),
        "unrecognised_mode": (
            "🤔 ያስገቡት ጽሑፍ አልተረዳም።\n\n"
            "እባክዎ ከታቹ ያሉ አዝራሮችን ተጠቅመው የመቀየሪያ አቅጣጫ ይምረጡ:"
        ),
        "unrecognised_date": (
            "🤔 ያስገቡት ቀን አይደለም።\n\n"
            "ቀኑን `YYYY/MM/DD` ቅጽ ያስገቡ።\n"
            "📌 ምሳሌ: `{}`\n\n"
            "ወይም ከታቹ ሌላ አማራጭ ይምረጡ።"
        ),
        "format_error": (
            "❌ *ቅጹ ተሳስቷል።*\n\n"
            "`YYYY/MM/DD` ይጠቀሙ — ቁጥሮች ብቻ፣ በ `/` ይለዩ\n"
            "📌 ምሳሌ: `{}`\n\n"
            "እባክዎ እንደገና ሞክሩ፣ ወይም ከታቹ ሌላ አማራጭ ይምረጡ።"
        ),
        "format_error_autofix": (
            "⚠️ `{sep}` ፋንታ `/` ተጠቀሙ — ራሱ አስተካክዬዋለሁ።\n\n"
        ),
        "reversed_date_hint": (
            "🤔 *ቀኑ ተቀልብሶ ይሆን?*\n\n"
            "`{input}` አስገብተዋል — ወር *{m}* የለም "
            "(ከፍተኛ 13 ለኢትዮ፣ 12 ለግሪጎ)።\n\n"
            "`{suggestion}` ማለትዎ ነው? _(ቀን/ወር ተቀይሯል)_\n\n"
            "ቀኑን `YYYY/MM/DD` ቅጽ ሞክሩ:"
        ),
        "geez_numeral_hint": (
            "🤔 *የግዕዝ ቁጥሮች* ያስገቡ ይመስላል።\n\n"
            "ቦቱ አሁን የአረቢክ ቁጥሮችን ብቻ ይቀበላል።\n"
            "📌 ቀኑን እንደዚህ ያስገቡ: `2017/4/27`"
        ),
        "conversion_error": (
            "❌ *ቀኑ ልክ አይደለም:*\n\n"
            "{}\n\n"
            "ቀኑን አርመው እንደገና ሞክሩ፣ ወይም ከታቹ ሌላ አማራጭ ይምረጡ።"
        ),
        "cancelled": "✅ ተሰርዟል። የመቀየሪያ አቅጣጫ ይምረጡ:",

        # ── Success ───────────────────────────────────────────────
        "e2g": (
            "✅ *የኢትዮጵያ ቀን:*\n"
            "{eth}\n\n"
            "➡️ *የግሪጎሪያን ቀን:*\n"
            "{greg}\n\n"
            "ሌላ ቀን ለመቀየር ከታቹ ይምረጡ:"
        ),
        "g2e": (
            "✅ *የግሪጎሪያን ቀን:*\n"
            "{greg}\n\n"
            "➡️ *የኢትዮጵያ ቀን:*\n"
            "{eth}\n\n"
            "ሌላ ቀን ለመቀየር ከታቹ ይምረጡ:"
        ),

        # ── Help ──────────────────────────────────────────────────
        "help": (
            "ℹ️ *የኢትዮጵያ ቀን መቀየሪያ — እገዛ*\n\n"
            "*አጠቃቀም:*\n"
            "1️⃣ የመቀየሪያ አቅጣጫ ይምረጡ\n"
            "2️⃣ ቀኑን `YYYY/MM/DD` ቅጽ ያስገቡ\n"
            "3️⃣ የተቀየረውን ቀን ይቀበሉ\n\n"
            "*የኢትዮጵያ ካላንደር:*\n"
            "• 13 ወሮች አሉ\n"
            "• ወር 1–12 እያንዳንዳቸው 30 ቀናት\n"
            "• ወር 13 (ጳጉሜ) 5 ቀናት (ዘመነ ሉቃስ 6)\n"
            "• ወር 1 = *መስከረም* (September) — January አይደለም!\n"
            "• ከግሪጎሪያን ~7-8 ዓመት ወደኋላ ነው\n\n"
            "*ምሳሌዎች:*\n"
            "• ኢትዮ `2017/4/27` → ጃን 5, 2025\n"
            "• ግሪጎ `2025/1/5` → ኢትዮ 2017/4/27\n\n"
            "*ቦቱ እነዚህንም ይቀበላል:*\n"
            "• ሰረዞች: `2017-4-27` → `2017/4/27` ይለውጣል\n"
            "• ነጥቦች: `2017.4.27` → `2017/4/27` ይለውጣል\n\n"
            "*ትዕዛዞች:*\n"
            "/start — ቦቱን ዳግም ጀምር\n"
            "/help — ይህን መልዕክት አሳይ\n"
            "/cancel — ግቤት ሰርዝ\n"
            "/feedback — ግምገማ ስጥ"
        ),

        # ── Admin ─────────────────────────────────────────────────
        "change_language": "ቋንቋ ይምረጡ:",
        "not_admin": "⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።",
        "stats": (
            "📊 *የቦት አኃዛዊ መረጃ*\n\n"
            "👥 ጠቅላላ ልዩ ተጠቃሚዎች: *{}*\n"
            "🆔 የእርስዎ ተጠቃሚ መለያ: `{}`\n"
            "💾 ማከማቻ: {}"
        ),
        "users_list_header": "👥 *ምዝገባ ተጠቃሚዎች* ({} ጠቅላላ) — እያሳየ {}-{}",
        "users_list_empty": "👥 ምንም ተጠቃሚ ገና አልመዘገቡም።",

        # ── Feedback ──────────────────────────────────────────────
        "feedback_nudge": (
            "📝 *ቦቱን እየወደዱት ነው?* አስተያየትዎን ሰጡን!\n"
            "ከታቹ *📝 Feedback* ይጫኑ።"
        ),
        "feedback_ask_rating": (
            "📝 *አስተያየትዎን እንፈልጋለን!*\n\n"
            "የኢትዮጵያ ቀን መቀየሪያን እንዴት ይመዝኑታል?\n"
            "ከታቹ ኮከብ ይምረጡ:"
        ),
        "feedback_ask_text": (
            "✨ *{} ኮከብ* ሰጡን!\n\n"
            "አስተያየት መጨመር ይፈልጋሉ? ከታቹ ይጻፉ፣ "
            "ወይም *Skip* ይጫኑ።"
        ),
        "feedback_thanks": (
            "🙏 *አመሰግናለሁ!*\n\n"
            "ግምገማዎ ተልኳል። በጣም እናደንቃለን! 🎉"
        ),
    },
}

# ═══════════════════════════════════════════════════════════════════
#   Helpers
# ═══════════════════════════════════════════════════════════════════

def lang_of(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "en")


def contains_geez(text: str) -> bool:
    """Return True if text contains Ge'ez / Ethiopic numerals."""
    return bool(re.search(r"[፩-፼]", text))


def normalize_separators(text: str) -> tuple[str, str | None]:
    """
    If the user typed dashes or dots instead of slashes, fix it silently.
    Returns (normalized_text, separator_used_or_None).
    """
    for sep in ("-", "."):
        if sep in text and "/" not in text:
            return text.replace(sep, "/"), sep
    return text, None


def looks_like_date(text: str) -> bool:
    normalized, _ = normalize_separators(text)
    return "/" in normalized and any(ch.isdigit() for ch in normalized)


def parse_slash_date(text: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in text.split("/")]
    if len(parts) != 3:
        raise ValueError("must have exactly 3 parts")
    try:
        year, month, day = map(int, parts)
        return year, month, day
    except ValueError:
        raise ValueError("must be numbers")


def detect_reversed(year: int, month: int, day: int, mode: str) -> str | None:
    """
    If month is out of range but day could be a valid month, suggest a swap.
    Returns a suggestion string like '2017/27/4' or None.
    """
    max_month = 13 if mode == "E2G" else 12
    if month > max_month and day <= max_month:
        return f"{year}/{day}/{month}"
    return None


def format_ethiopian(eth_y: int, eth_m: int, eth_d: int) -> str:
    am_month   = ETH_MONTHS_AM[eth_m - 1]
    en_month   = ETH_MONTHS_EN[eth_m - 1]
    greg_month = ETH_TO_GREG_MONTH_NAME[eth_m]
    return (
        f"Ethiopian year {eth_y}, month {eth_m} "
        f"({am_month} / {en_month} ≈ {greg_month}), day {eth_d}"
    )


def format_gregorian(y: int, m: int, d: int) -> str:
    return f"{GREG_MONTHS[m - 1]} {d}, {y}"


def format_user_entry(uid: str, record: dict, index: int) -> str:
    username   = record.get("u")
    first_name = record.get("n", "N/A")
    ts         = record.get("t")
    signup     = time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "unknown"
    link       = (
        f"[🔗 @{username}](https://t.me/{username})"
        if username
        else f"[🔗 Open Profile](tg://user?id={uid})"
    )
    return f"{index}. {first_name} — {link} `{signup}`"


async def send_feedback_to_admin(bot, user, rating: int, comment: str | None):
    if not ADMIN_USER_ID:
        return
    stars        = "⭐" * rating + "☆" * (5 - rating)
    username     = f"@{user.username}" if user.username else "no username"
    comment_line = f"\n💬 *Comment:* {comment}" if comment else "\n💬 _No comment_"
    msg = (
        f"📝 *New Feedback*\n\n"
        f"👤 {user.first_name or 'N/A'} ({username})\n"
        f"🆔 `{user.id}`\n"
        f"⭐ {stars} ({rating}/5)"
        f"{comment_line}\n\n"
        f"🕐 {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
    )
    try:
        await bot.send_message(chat_id=int(ADMIN_USER_ID), text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Failed to forward feedback to admin: {e}")


def maybe_get_tip(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Return a tip string every 5th successful conversion, else None."""
    count = context.user_data.get("conversion_count", 0) + 1
    context.user_data["conversion_count"] = count
    if count % 5 == 0:
        return TIPS[(count // 5 - 1) % len(TIPS)]
    return None


def maybe_get_nudge(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True (once per session) on the 5th conversion to nudge for feedback."""
    return context.user_data.get("conversion_count", 0) == 5 \
        and not context.user_data.get("nudge_sent")


# ═══════════════════════════════════════════════════════════════════
#   Handlers
# ═══════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    is_new = add_user(user.id, username=user.username, first_name=user.first_name)
    if is_new:
        print(f"🆕 New user: {user.id} (@{user.username}) — Total: {get_user_count()}")
    context.user_data.clear()
    await update.message.reply_text(
        TEXT["en"]["welcome"], parse_mode="Markdown", reply_markup=LANG_KEYBOARD
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(context)
    kb   = WAITING_KEYBOARD if "mode" in context.user_data else (
           CONVERT_KEYBOARD if "lang" in context.user_data else LANG_KEYBOARD)
    await update.message.reply_text(TEXT[lang]["help"], parse_mode="Markdown", reply_markup=kb)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(context)
    context.user_data.pop("mode", None)
    context.user_data.pop("feedback_step", None)
    context.user_data.pop("feedback_rating", None)
    await update.message.reply_text(TEXT[lang]["cancelled"], reply_markup=CONVERT_KEYBOARD)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang    = lang_of(context)
    if not is_admin(user_id):
        await update.message.reply_text(TEXT[lang]["not_admin"])
        return
    storage = f"S3 ({AWS_S3_BUCKET_NAME})" if USE_S3 else "Local (⚠️ not persistent)"
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

    args = context.args or []
    if len(args) != 2:
        total = get_user_count()
        await update.message.reply_text(
            f"❌ Please specify a range.\n\nUsage: `/users <from> <to>`\n"
            f"Example: `/users 1 50`\n\n👥 Total users: *{total}*",
            parse_mode="Markdown",
        )
        return

    try:
        range_start, range_end = int(args[0]), int(args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ Both arguments must be numbers.\n\nExample: `/users 1 50`",
            parse_mode="Markdown",
        )
        return

    all_users = get_all_users()
    total     = len(all_users)

    if not all_users:
        await update.message.reply_text(TEXT[lang]["users_list_empty"])
        return

    if range_start < 1 or range_end < range_start:
        await update.message.reply_text(
            f"❌ Invalid range.\n\n👥 Total users: *{total}*", parse_mode="Markdown"
        )
        return
    if range_start > total:
        await update.message.reply_text(
            f"❌ Range starts beyond total users.\n\n👥 Total users: *{total}*",
            parse_mode="Markdown",
        )
        return

    range_end    = min(range_end, total)
    sorted_users = sorted(
        all_users.items(),
        key=lambda item: item[1].get("t", float("-inf")),
        reverse=True,
    )
    slice_idx = [
        (gi, uid, rec)
        for gi, (uid, rec) in enumerate(sorted_users, start=1)
        if range_start <= gi <= range_end
    ]

    MAX_CHARS, pages, current_lines, current_len = 4000, [], [], 0
    for gi, uid, rec in slice_idx:
        line = format_user_entry(uid, rec, gi)
        if current_len + len(line) + 1 > MAX_CHARS and current_lines:
            pages.append(current_lines)
            current_lines, current_len = [], 0
        current_lines.append(line)
        current_len += len(line) + 1
    if current_lines:
        pages.append(current_lines)

    for i, lines in enumerate(pages):
        header = TEXT[lang]["users_list_header"].format(total, range_start, range_end)
        if len(pages) > 1:
            header += f" _(part {i + 1}/{len(pages)})_"
        try:
            await update.message.reply_text(
                f"{header}\n\n" + "\n\n".join(lines),
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send page {i + 1}: {e}")
            break


# ── Feedback ──────────────────────────────────────────────────────

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(context)
    context.user_data.pop("mode", None)
    context.user_data["feedback_step"] = "awaiting_rating"
    await update.message.reply_text(
        TEXT[lang]["feedback_ask_rating"], parse_mode="Markdown", reply_markup=STAR_KEYBOARD
    )


async def handle_rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = lang_of(context)
    data = query.data

    # ── Star rating tapped ─────────────────────────────────────────
    if data.startswith("rating:"):
        rating = int(data.split(":")[1])
        context.user_data["feedback_rating"] = rating
        context.user_data["feedback_step"]   = "awaiting_comment"
        plural = "s" if rating != 1 else ""
        await query.edit_message_text(
            TEXT[lang]["feedback_ask_text"].format(rating, plural),
            parse_mode="Markdown",
            reply_markup=SKIP_KEYBOARD,
        )

    # ── Skip tapped ───────────────────────────────────────────────
    elif data == "feedback:skip":
        rating = context.user_data.pop("feedback_rating", 0)
        context.user_data.pop("feedback_step", None)
        await query.edit_message_text(TEXT[lang]["feedback_thanks"], parse_mode="Markdown")
        await send_feedback_to_admin(context.bot, update.effective_user, rating, None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Choose a conversion direction:",
            reply_markup=CONVERT_KEYBOARD,
        )

    # ── Month guide requested ─────────────────────────────────────
    elif data == "guide:eth":
        await query.answer()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=TEXT[lang]["guide_eth"],
            parse_mode="Markdown",
        )

    elif data == "guide:greg":
        await query.answer()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=TEXT[lang]["guide_greg"],
            parse_mode="Markdown",
        )


# ── Main text handler ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lang = lang_of(context)

    # ── Cancel button / command ────────────────────────────────────
    if "❌ Cancel" in text:
        context.user_data.pop("mode", None)
        context.user_data.pop("feedback_step", None)
        context.user_data.pop("feedback_rating", None)
        await update.message.reply_text(TEXT[lang]["cancelled"], reply_markup=CONVERT_KEYBOARD)
        return

    # ── Feedback button ────────────────────────────────────────────
    if "📝 Feedback" in text:
        context.user_data.pop("mode", None)
        context.user_data["feedback_step"] = "awaiting_rating"
        await update.message.reply_text(
            TEXT[lang]["feedback_ask_rating"], parse_mode="Markdown", reply_markup=STAR_KEYBOARD
        )
        return

    # ── Feedback comment step ──────────────────────────────────────
    if context.user_data.get("feedback_step") == "awaiting_comment":
        rating = context.user_data.pop("feedback_rating", 0)
        context.user_data.pop("feedback_step", None)
        await update.message.reply_text(
            TEXT[lang]["feedback_thanks"], parse_mode="Markdown", reply_markup=CONVERT_KEYBOARD
        )
        await send_feedback_to_admin(context.bot, update.effective_user, rating, text)
        return

    # ── Change language ────────────────────────────────────────────
    if "🌐" in text or "Change Language" in text or "ቋንቋ" in text:
        context.user_data.clear()
        await update.message.reply_text(TEXT["en"]["change_language"], reply_markup=LANG_KEYBOARD)
        return

    # ── Language selection ─────────────────────────────────────────
    if "lang" not in context.user_data:
        if "English" in text:
            context.user_data["lang"] = "en"
        elif "አማርኛ" in text:
            context.user_data["lang"] = "am"
        else:
            await update.message.reply_text(
                TEXT["en"]["unrecognised_lang"], reply_markup=LANG_KEYBOARD
            )
            return
        new_lang = context.user_data["lang"]
        await update.message.reply_text(TEXT[new_lang]["choose"], reply_markup=CONVERT_KEYBOARD)
        return

    # ── Conversion direction ───────────────────────────────────────
    if "Ethiopian →" in text:
        context.user_data["mode"] = "E2G"
        await update.message.reply_text(
            TEXT[lang]["ask_e"], parse_mode="Markdown",
            reply_markup=WAITING_KEYBOARD,
        )
        # Send the inline month guide button as a separate message so it's always tappable
        await update.message.reply_text(
            "Tap below for the full Ethiopian month guide:",
            reply_markup=MONTH_GUIDE_ETH_KB,
        )
        return

    if "Gregorian →" in text:
        context.user_data["mode"] = "G2E"
        await update.message.reply_text(
            TEXT[lang]["ask_g"], parse_mode="Markdown",
            reply_markup=WAITING_KEYBOARD,
        )
        await update.message.reply_text(
            "Tap below for the full Gregorian month guide:",
            reply_markup=MONTH_GUIDE_GREG_KB,
        )
        return

    # ── No mode set ────────────────────────────────────────────────
    if "mode" not in context.user_data:
        await update.message.reply_text(
            TEXT[lang]["unrecognised_mode"], reply_markup=CONVERT_KEYBOARD
        )
        return

    # ══════════════════════════════════════════════════════════════
    #   DATE CONVERSION
    # ══════════════════════════════════════════════════════════════
    mode    = context.user_data["mode"]
    example = EXAMPLE_DATE[mode]

    # ── Ge'ez numeral detection ────────────────────────────────────
    if contains_geez(text):
        await update.message.reply_text(
            TEXT[lang]["geez_numeral_hint"], parse_mode="Markdown", reply_markup=WAITING_KEYBOARD
        )
        return

    # ── Auto-fix separators ────────────────────────────────────────
    fixed_text, sep_used = normalize_separators(text)
    prefix = ""
    if sep_used:
        prefix = TEXT[lang]["format_error_autofix"].format(sep=sep_used)

    # ── Not a date at all ──────────────────────────────────────────
    if not looks_like_date(fixed_text):
        await update.message.reply_text(
            TEXT[lang]["unrecognised_date"].format(example),
            parse_mode="Markdown",
            reply_markup=WAITING_KEYBOARD,
        )
        return

    # ── Parse ──────────────────────────────────────────────────────
    try:
        y, m, d = parse_slash_date(fixed_text)
    except ValueError as e:
        error_message = str(e)
        if "must have exactly 3 parts" in error_message or "must be numbers" in error_message:
            reply = prefix + TEXT[lang]["format_error"].format(example)
        else:
            reply = prefix + TEXT[lang]["conversion_error"].format(error_message)
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=WAITING_KEYBOARD)
        return

    # ── Reversed-date detection ────────────────────────────────────
    suggestion = detect_reversed(y, m, d, mode)
    if suggestion:
        await update.message.reply_text(
            TEXT[lang]["reversed_date_hint"].format(
                input=fixed_text, m=m, suggestion=suggestion
            ),
            parse_mode="Markdown",
            reply_markup=WAITING_KEYBOARD,
        )
        return

    # ── Convert ────────────────────────────────────────────────────
    try:
        if mode == "E2G":
            g   = EthiopianDateConverter.to_gregorian(y, m, d)
            msg = prefix + TEXT[lang]["e2g"].format(
                eth=format_ethiopian(y, m, d),
                greg=format_gregorian(g.year, g.month, g.day),
            )
        else:
            ey, em, ed = EthiopianDateConverter.to_ethiopian(y, m, d)
            msg = prefix + TEXT[lang]["g2e"].format(
                greg=format_gregorian(y, m, d),
                eth=format_ethiopian(ey, em, ed),
            )

        context.user_data.pop("mode", None)

        # Did-you-know tip
        tip = maybe_get_tip(context)
        if tip:
            msg += f"\n\n{tip}"

        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=CONVERT_KEYBOARD)

        # Feedback nudge — once at 5th conversion
        if maybe_get_nudge(context):
            context.user_data["nudge_sent"] = True
            await update.message.reply_text(
                TEXT[lang]["feedback_nudge"],
                parse_mode="Markdown",
                reply_markup=CONVERT_KEYBOARD,
            )

    except ValueError as e:
        await update.message.reply_text(
            prefix + TEXT[lang]["conversion_error"].format(str(e)),
            parse_mode="Markdown",
            reply_markup=WAITING_KEYBOARD,
        )
    except Exception as e:
        await update.message.reply_text(
            prefix + TEXT[lang]["conversion_error"].format(f"Unexpected error: {e}"),
            parse_mode="Markdown",
            reply_markup=WAITING_KEYBOARD,
        )


# ═══════════════════════════════════════════════════════════════════
#   App
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("help",     help_command))
    app.add_handler(CommandHandler("cancel",   cancel_command))
    app.add_handler(CommandHandler("stats",    stats_command))
    app.add_handler(CommandHandler("users",    users_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CallbackQueryHandler(handle_rating_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🤖 Bot is starting… Press Ctrl+C to stop.")
    app.run_polling()