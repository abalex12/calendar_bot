import os
import json
import time
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv
from converter import EthiopianDateConverter
import boto3
from botocore.exceptions import ClientError

#   Setup  

load_dotenv()
BOT_TOKEN = os.getenv("T_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

# S3 Configuration
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

if not BOT_TOKEN:
    raise RuntimeError("T_BOT_TOKEN not set")

USE_S3 = all([AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME])

if USE_S3:
    s3_client = boto3.client(
        's3',
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_DEFAULT_REGION
    )
    print(f"✅ S3 storage configured: {AWS_S3_BUCKET_NAME}")
else:
    print("⚠️  S3 not configured - using local file storage (not persistent on Railway!)")
    s3_client = None

USERS_FILE = "users.json"

# ─── In-memory cache to avoid re-fetching S3 on every message ───────────────
_users_cache: dict | None = None
_cache_dirty: bool = False

#   User Functions  

def load_users() -> dict:
    """Load users from cache, S3, or local file. Returns dict keyed by str(user_id)."""
    global _users_cache

    if _users_cache is not None:
        return _users_cache  # Serve from memory — no S3 call

    if USE_S3:
        try:
            response = s3_client.get_object(Bucket=AWS_S3_BUCKET_NAME, Key=USERS_FILE)
            raw = json.loads(response['Body'].read().decode('utf-8'))
            _users_cache = raw.get("users", {})
        except ClientError as e:
            _users_cache = {} if e.response['Error']['Code'] == 'NoSuchKey' else {}
            if e.response['Error']['Code'] != 'NoSuchKey':
                print(f"Error loading users from S3: {e}")
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
    """Persist users to S3 or local file with minimal payload."""
    # Compact JSON — no indent, saves space at scale
    json_data = json.dumps({"users": users_dict}, separators=(',', ':'))

    if USE_S3:
        try:
            s3_client.put_object(
                Bucket=AWS_S3_BUCKET_NAME,
                Key=USERS_FILE,
                Body=json_data.encode('utf-8'),
                ContentType='application/json'
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
    """
    Add a new user or silently skip existing ones.
    Stores only the minimum needed fields:
      - "u": username (omitted if None)
      - "n": first_name (omitted if None)
      - "t": Unix signup timestamp (set once, never overwritten)

    Returns True if this is a genuinely new user.
    """
    users = load_users()
    key = str(user_id)

    if key in users:
        return False  # Already tracked — no write needed

    # Build the smallest possible record
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


#   Keyboards  

LANG_KEYBOARD = ReplyKeyboardMarkup(
    [["English 🇬🇧", "አማርኛ 🇪🇹"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

CONVERT_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🇪🇹 Ethiopian → 🌍 Gregorian", "🌍 Gregorian → 🇪🇹 Ethiopian"],
        ["🌐 Change Language"],
    ],
    resize_keyboard=True,
)

WAITING_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🇪🇹 Ethiopian → 🌍 Gregorian", "🌍 Gregorian → 🇪🇹 Ethiopian"],
        ["🌐 Change Language"],
    ],
    resize_keyboard=True,
)

#   Month Labels  

ETH_MONTHS = [
    "መስከረም", "ጥቅምት", "ኅዳር", "ታህሳስ",
    "ጥር", "የካቲት", "መጋቢት", "ሚያዝያ",
    "ግንቦት", "ሰኔ", "ሐምሌ", "ነሐሴ", "ጳጉሜ",
]

GREG_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

#   All UI Text  

TEXT = {
    "en": {
        "welcome": (
            "👋 Welcome to the Ethiopian Date Converter!\n\n"
            "I can convert dates between the Ethiopian and Gregorian calendars.\n\n"
            "Please choose your language:"
        ),
        "choose": "✅ Language set to English.\n\nChoose a conversion direction:",
        "ask_e": (
            "📥 Enter an Ethiopian date in this format:\n"
            "YYYY/MM/DD\n\n"
            "📌 Example: 2017/4/27\n\n"
            "💡 The Ethiopian calendar has 13 months.\n"
            "Months 1–12 have 30 days each.\n"
            "Month 13 (ጳጉሜ / Pagume) has 5 days, or 6 in a leap year."
        ),
        "ask_g": (
            "📥 Enter a Gregorian date in this format:\n"
            "YYYY/MM/DD\n\n"
            "📌 Example: 2025/1/5"
        ),
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
            "Please enter the date in YYYY/MM/DD format.\n"
            "📌 Example: {}\n\n"
            "Or pick a different option from the menu below."
        ),
        "format_error": (
            "❌ Wrong format.\n\n"
            "Use YYYY/MM/DD  (numbers only, separated by /)\n"
            "📌 Example: {}\n\n"
            "Please try again, or pick a different option below."
        ),
        "conversion_error": (
            "❌ Invalid date:\n\n"
            "{}\n\n"
            "Please correct the date and try again, or pick a different option below."
        ),
        "e2g": "✅ Ethiopian date:\n{}\n\n➡️ Gregorian date:\n{}\n\nConvert another date:",
        "g2e": "✅ Gregorian date:\n{}\n\n➡️ Ethiopian date:\n{}\n\nConvert another date:",
        "help": (
            "ℹ️ *Ethiopian Date Converter — Help*\n\n"
            "*How to use:*\n"
            "1️⃣ Choose a conversion direction\n"
            "2️⃣ Type your date as YYYY/MM/DD\n"
            "3️⃣ Receive the converted date\n\n"
            "*Ethiopian calendar facts:*\n"
            "• 13 months total\n"
            "• Months 1–12 each have 30 days\n"
            "• Month 13 (ጳጉሜ/Pagume) has 5 days (6 in a leap year)\n"
            "• Ethiopian year is ~7–8 years behind the Gregorian year\n\n"
            "*Examples:*\n"
            "• Ethiopian 2017/4/27  →  Gregorian January 5, 2025\n"
            "• Gregorian 2025/1/5  →  Ethiopian 2017/4/27\n\n"
            "*Commands:*\n"
            "/start — restart the bot\n"
            "/help  — show this message"
        ),
        "change_language": "Choose your language:",
        "not_admin": "⛔ This command is only available to administrators.",
        "stats": (
            "📊 *Bot Statistics*\n\n"
            "👥 Total unique users: *{}*\n"
            "🆔 Your user ID: `{}`\n"
            "💾 Storage: {}"
        ),
        "users_list": "👥 *Registered Users* ({}) — sorted by sign-up date\n\n{}",
        "users_list_empty": "👥 No users registered yet.",
    },
    "am": {
        "welcome": (
            "👋 እንኳን ደህና መጡ! የኢትዮጵያ ቀን መቀየሪያ!\n\n"
            "በኢትዮጵያ እና ግሪጎሪያን ካላንደሮች መካከል ቀናትን መቀየር ይችላሉ።\n\n"
            "ቋንቋ ይምረጡ:"
        ),
        "choose": "✅ ቋንቋ አማርኛ ተመርጧል።\n\nየመቀየሪያ አቅጣጫ ይምረጡ:",
        "ask_e": (
            "📥 የኢትዮጵያ ቀን ያስገቡ:\n"
            "YYYY/MM/DD\n\n"
            "📌 ምሳሌ: 2017/4/27\n\n"
            "💡 የኢትዮጵያ ካላንደር 13 ወሮች አሉት።\n"
            "ወር 1–12 እያንዳንዳቸው 30 ቀናት አሏቸው።\n"
            "ወር 13 (ጳጉሜ) 5 ቀናት አሉት፣ ወይም 6 ቀናት ዘመነ ሉቃስ።"
        ),
        "ask_g": (
            "📥 የግሪጎሪያን ቀን ያስገቡ:\n"
            "YYYY/MM/DD\n\n"
            "📌 ምሳሌ: 2025/1/5"
        ),
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
            "ቀኑን YYYY/MM/DD ቅጽ ያስገቡ።\n"
            "📌 ምሳሌ: {}\n\n"
            "ወይም ከታቹ ሌላ አማራጭ ይምረጡ።"
        ),
        "format_error": (
            "❌ ቅጹ ተሳስቷል።\n\n"
            "YYYY/MM/DD ይጠቀሙ  (ቁጥሮች ብቻ፣ በ / ይለዩ)\n"
            "📌 ምሳሌ: {}\n\n"
            "እባክዎ እንደገና ሞክሩ፣ ወይም ከታቹ ሌላ አማራጭ ይምረጡ።"
        ),
        "conversion_error": (
            "❌ ቀኑ ልክ አይደለም:\n\n"
            "{}\n\n"
            "ቀኑን አርመው እንደገና ሞክሩ፣ ወይም ከታቹ ሌላ አማራጭ ይምረጡ።"
        ),
        "e2g": "✅ የኢትዮጵያ ቀን:\n{}\n\n➡️ የግሪጎሪያን ቀን:\n{}\n\nሌላ ቀን ቀይሩ:",
        "g2e": "✅ የግሪጎሪያን ቀን:\n{}\n\n➡️ የኢትዮጵያ ቀን:\n{}\n\nሌላ ቀን ቀይሩ:",
        "help": (
            "ℹ️ *የኢትዮጵያ ቀን መቀየሪያ — እገዛ*\n\n"
            "*አጠቃቀም:*\n"
            "1️⃣ የመቀየሪያ አቅጣጫ ይምረጡ\n"
            "2️⃣ ቀኑን YYYY/MM/DD ቅጽ ያስገቡ\n"
            "3️⃣ የተቀየረውን ቀን ይቀበሉ\n\n"
            "*የኢትዮጵያ ካላንደር:*\n"
            "• 13 ወሮች አሉ\n"
            "• ወር 1–12 እያንዳንዳቸው 30 ቀናት\n"
            "• ወር 13 (ጳጉሜ) 5 ቀናት (ዘመነ ሉቃስ 6 ቀናት)\n"
            "• የኢትዮጵያ ዓ.ም ከግሪጎሪያን ~7-8 ዓመት ወደኋላ ነው\n\n"
            "*ምሳሌዎች:*\n"
            "• ኢትዮ 2017/4/27  →  ጃንዋሪ 5, 2025\n"
            "• ግሪጎ 2025/1/5  →  ኢትዮ 2017/4/27\n\n"
            "*ትዕዛዞች:*\n"
            "/start — ቦቱን ዳግም ጀምር\n"
            "/help  — ይህን መልዕክት አሳይ"
        ),
        "change_language": "ቋንቋ ይምረጡ:",
        "not_admin": "⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።",
        "stats": (
            "📊 *የቦት አኃዛዊ መረጃ*\n\n"
            "👥 ጠቅላላ ልዩ ተጠቃሚዎች: *{}*\n"
            "🆔 የእርስዎ ተጠቃሚ መለያ: `{}`\n"
            "💾 ማከማቻ: {}"
        ),
        "users_list": "👥 *ምዝገባ ተጠቃሚዎች* ({}) — በምዝገባ ቅደም ተከተል\n\n{}",
        "users_list_empty": "👥 ምንም ተጠቃሚ ገና አልመዘገቡም።",
    },
}

EXAMPLE_DATE = {
    "E2G": "2017/4/27",
    "G2E": "2025/1/5",
}

#   Helpers  

def looks_like_date(text: str) -> bool:
    return "/" in text and any(ch.isdigit() for ch in text)


def parse_slash_date(text: str):
    parts = [p.strip() for p in text.split("/")]
    if len(parts) != 3:
        raise ValueError("must have exactly 3 parts")
    try:
        year, month, day = map(int, parts)
        return year, month, day
    except ValueError:
        raise ValueError("must be numbers")


def format_ethiopian(y, m, d) -> str:
    return f"{d} {ETH_MONTHS[m - 1]} {y} ዓ.ም"


def format_gregorian(y, m, d) -> str:
    return f"{GREG_MONTHS[m - 1]} {d}, {y}"


def lang_of(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "en")


def format_user_entry(uid: str, record: dict, index: int) -> str:
    """Format one user line for /users output."""
    username = record.get("u")
    first_name = record.get("n", "N/A")
    ts = record.get("t")

    # Human-readable signup date
    if ts:
        signup = time.strftime("%Y-%m-%d", time.gmtime(ts))
    else:
        signup = "unknown"

    # Clickable profile link
    if username:
        link = f"[🔗 @{username}](https://t.me/{username})"
    else:
        link = f"[🔗 Open Profile](tg://user?id={uid})"

    return f"{index}\\. {first_name} — {link}\n    📅 `{signup}` · ID: `{uid}`"


#   Handlers  

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = add_user(user.id, username=user.username, first_name=user.first_name)

    if is_new:
        storage_type = "S3" if USE_S3 else "local"
        print(f"🆕 New user: {user.id} (@{user.username}) — Total: {get_user_count()} [{storage_type}]")

    context.user_data.clear()
    await update.message.reply_text(TEXT["en"]["welcome"], reply_markup=LANG_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = lang_of(context)

    if "mode" in context.user_data:
        keyboard = WAITING_KEYBOARD
    elif "lang" in context.user_data:
        keyboard = CONVERT_KEYBOARD
    else:
        keyboard = LANG_KEYBOARD

    await update.message.reply_text(
        TEXT[lang]["help"],
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = lang_of(context)

    if not is_admin(user_id):
        await update.message.reply_text(TEXT[lang]["not_admin"])
        return

    total_users = get_user_count()
    storage_info = f"S3 ({AWS_S3_BUCKET_NAME})" if USE_S3 else "Local (⚠️ not persistent)"

    await update.message.reply_text(
        TEXT[lang]["stats"].format(total_users, user_id, storage_info),
        parse_mode="Markdown"
    )


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all registered users sorted by signup date (oldest first)."""
    user_id = update.effective_user.id
    lang = lang_of(context)

    if not is_admin(user_id):
        await update.message.reply_text(TEXT[lang]["not_admin"])
        return

    all_users = get_all_users()

    if not all_users:
        await update.message.reply_text(TEXT[lang]["users_list_empty"])
        return

    # Sort by signup timestamp ascending (oldest first); missing timestamp goes last
    sorted_users = sorted(
        all_users.items(),
        key=lambda item: item[1].get("t", float("inf")),
        reverse=True
    )

    # Telegram message limit is 4096 chars — paginate if needed
    MAX_CHARS = 4000
    pages = []
    current_lines = []
    current_len = 0

    for idx, (uid, record) in enumerate(sorted_users, start=1):
        line = format_user_entry(uid, record, idx)
        if current_len + len(line) > MAX_CHARS and current_lines:
            pages.append("\n\n".join(current_lines))
            current_lines = []
            current_len = 0
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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lang = lang_of(context)

    if "🌐" in text or "Change Language" in text or "ቋንቋ" in text:
        context.user_data.clear()
        await update.message.reply_text(
            TEXT["en"]["change_language"], reply_markup=LANG_KEYBOARD
        )
        return

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
        await update.message.reply_text(
            TEXT[new_lang]["choose"], reply_markup=CONVERT_KEYBOARD
        )
        return

    if "Ethiopian →" in text:
        context.user_data["mode"] = "E2G"
        await update.message.reply_text(TEXT[lang]["ask_e"], reply_markup=WAITING_KEYBOARD)
        return
    if "Gregorian →" in text:
        context.user_data["mode"] = "G2E"
        await update.message.reply_text(TEXT[lang]["ask_g"], reply_markup=WAITING_KEYBOARD)
        return

    if "mode" not in context.user_data:
        await update.message.reply_text(
            TEXT[lang]["unrecognised_mode"], reply_markup=CONVERT_KEYBOARD
        )
        return

    mode = context.user_data["mode"]
    example = EXAMPLE_DATE[mode]

    if not looks_like_date(text):
        await update.message.reply_text(
            TEXT[lang]["unrecognised_date"].format(example),
            reply_markup=WAITING_KEYBOARD,
        )
        return

    try:
        y, m, d = parse_slash_date(text)

        if mode == "E2G":
            g = EthiopianDateConverter.to_gregorian(y, m, d)
            await update.message.reply_text(
                TEXT[lang]["e2g"].format(
                    format_ethiopian(y, m, d),
                    format_gregorian(g.year, g.month, g.day),
                ),
                reply_markup=CONVERT_KEYBOARD,
            )
        else:
            ey, em, ed = EthiopianDateConverter.to_ethiopian(y, m, d)
            await update.message.reply_text(
                TEXT[lang]["g2e"].format(
                    format_gregorian(y, m, d),
                    format_ethiopian(ey, em, ed),
                ),
                reply_markup=CONVERT_KEYBOARD,
            )

        context.user_data.pop("mode", None)

    except ValueError as e:
        error_message = str(e)
        if "must have exactly 3 parts" in error_message or "must be numbers" in error_message:
            reply = TEXT[lang]["format_error"].format(example)
        else:
            reply = TEXT[lang]["conversion_error"].format(error_message)
        await update.message.reply_text(reply, reply_markup=WAITING_KEYBOARD)

    except Exception as e:
        await update.message.reply_text(
            TEXT[lang]["conversion_error"].format(f"Unexpected error: {e}"),
            reply_markup=WAITING_KEYBOARD,
        )


#   App  

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🤖 Bot is starting… Press Ctrl+C to stop.")
    app.run_polling()
