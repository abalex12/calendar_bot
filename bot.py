import os
import json
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
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL")  # e.g., https://s3.amazonaws.com or your provider
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")  # Default region

if not BOT_TOKEN:
    raise RuntimeError("T_BOT_TOKEN not set")

# Check if S3 is configured
USE_S3 = all([AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME])

if USE_S3:
    # Initialize S3 client
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

# User tracking file
USERS_FILE = "users.json"

#   User Counter Functions with S3 Support  

def load_users():
    """Load the set of user IDs from S3 or local file"""
    if USE_S3:
        try:
            response = s3_client.get_object(Bucket=AWS_S3_BUCKET_NAME, Key=USERS_FILE)
            data = json.loads(response['Body'].read().decode('utf-8'))
            return set(data.get("users", []))
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # File doesn't exist yet, return empty set
                return set()
            else:
                print(f"Error loading users from S3: {e}")
                return set()
        except Exception as e:
            print(f"Unexpected error loading users from S3: {e}")
            return set()
    else:
        # Fallback to local file
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r") as f:
                    data = json.load(f)
                    return set(data.get("users", []))
            except (json.JSONDecodeError, IOError):
                return set()
        return set()

def save_users(users_set):
    """Save the set of user IDs to S3 or local file"""
    data = {"users": list(users_set)}
    json_data = json.dumps(data, indent=2)
    
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
        # Fallback to local file
        try:
            with open(USERS_FILE, "w") as f:
                f.write(json_data)
        except IOError as e:
            print(f"Error saving users locally: {e}")

def add_user(user_id):
    """Add a user ID to the tracking set and save"""
    users = load_users()
    is_new = user_id not in users
    users.add(user_id)
    save_users(users)
    return is_new

def get_user_count():
    """Get the total number of unique users"""
    return len(load_users())

def is_admin(user_id):
    """Check if the user is an admin"""
    if not ADMIN_USER_ID:
        return False
    try:
        return str(user_id) == str(ADMIN_USER_ID)
    except:
        return False

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

# Shown while waiting for a date — keeps all options accessible
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
        # Greetings / navigation
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
        # Errors
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
        # Success
        "e2g": "✅ Ethiopian date:\n{}\n\n➡️ Gregorian date:\n{}\n\nConvert another date:",
        "g2e": "✅ Gregorian date:\n{}\n\n➡️ Ethiopian date:\n{}\n\nConvert another date:",
        # Help
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
    },
    "am": {
        # Greetings / navigation
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
        # Errors
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
        # Success
        "e2g": "✅ የኢትዮጵያ ቀን:\n{}\n\n➡️ የግሪጎሪያን ቀን:\n{}\n\nሌላ ቀን ቀይሩ:",
        "g2e": "✅ የግሪጎሪያን ቀን:\n{}\n\n➡️ የኢትዮጵያ ቀን:\n{}\n\nሌላ ቀን ቀይሩ:",
        # Help
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
    },
}

# Example dates shown in error messages, per mode
EXAMPLE_DATE = {
    "E2G": "2017/4/27",
    "G2E": "2025/1/5",
}

#   Helpers  

def looks_like_date(text: str) -> bool:
    """Return True if the text at least resembles a date attempt (contains digits and /)"""
    return "/" in text and any(ch.isdigit() for ch in text)

def parse_slash_date(text: str):
    """Parse YYYY/MM/DD and return (year, month, day) as ints, or raise ValueError"""
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

#   Handlers  

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset state and greet the user"""
    user_id = update.effective_user.id
    
    # Track the user
    is_new_user = add_user(user_id)
    
    # Log new users (optional - for your monitoring)
    if is_new_user:
        storage_type = "S3" if USE_S3 else "local"
        print(f"🆕 New user started the bot: {user_id} (Total: {get_user_count()}) [{storage_type}]")
    
    context.user_data.clear()
    await update.message.reply_text(TEXT["en"]["welcome"], reply_markup=LANG_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a detailed help message, keeping the user's current keyboard"""
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
    """Show user statistics (admin only)"""
    user_id = update.effective_user.id
    lang = lang_of(context)
    
    # Check if user is admin
    if not is_admin(user_id):
        await update.message.reply_text(TEXT[lang]["not_admin"])
        return
    
    # Get statistics
    total_users = get_user_count()
    storage_info = f"S3 ({AWS_S3_BUCKET_NAME})" if USE_S3 else "Local (⚠️ not persistent)"
    
    await update.message.reply_text(
        TEXT[lang]["stats"].format(total_users, user_id, storage_info),
        parse_mode="Markdown"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Single entry point for all text messages.
    Routes by state: no-lang → no-mode → awaiting-date.
    Every branch handles irrelevant input gracefully.
    """
    text = update.message.text.strip()
    lang = lang_of(context)

    # ── "Change Language" is accessible from any state ──────────────────────
    if "🌐" in text or "Change Language" in text or "ቋንቋ" in text:
        context.user_data.clear()
        await update.message.reply_text(
            TEXT["en"]["change_language"], reply_markup=LANG_KEYBOARD
        )
        return

    # ── STATE 1: No language chosen yet 
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

    # ── Switching conversion direction is always allowed from here on 
    if "Ethiopian →" in text:
        context.user_data["mode"] = "E2G"
        await update.message.reply_text(
            TEXT[lang]["ask_e"], reply_markup=WAITING_KEYBOARD
        )
        return
    if "Gregorian →" in text:
        context.user_data["mode"] = "G2E"
        await update.message.reply_text(
            TEXT[lang]["ask_g"], reply_markup=WAITING_KEYBOARD
        )
        return

    # ── STATE 2: Language chosen, no conversion direction yet 
    if "mode" not in context.user_data:
        await update.message.reply_text(
            TEXT[lang]["unrecognised_mode"], reply_markup=CONVERT_KEYBOARD
        )
        return

    # ── STATE 3: Awaiting a date 
    mode = context.user_data["mode"]
    example = EXAMPLE_DATE[mode]

    # Catch completely non-date-looking input before even trying to parse
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

        # Keep lang, clear mode — ready for next conversion
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🤖 Bot is starting… Press Ctrl+C to stop.")
    app.run_polling()