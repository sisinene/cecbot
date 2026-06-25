import logging
import os
import re
from asyncio import to_thread
from collections import defaultdict, deque

from dotenv import load_dotenv
from groq import Groq
from telegram import Update
from telegram.constants import ChatAction, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
BOT_SYSTEM_PROMPT = os.getenv(
    "BOT_SYSTEM_PROMPT",
    "You are a helpful, concise AI assistant inside Telegram.",
)
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12"))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram-ai-bot")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
chat_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))


def require_config() -> None:
    missing = [
        name
        for name, value in {
            "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
            "GROQ_API_KEY": GROQ_API_KEY,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi. Send me a message and I will answer with AI. Use /reset to clear this chat's memory."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start - intro\n"
        "/reset - clear this chat's memory\n"
        "/help - show commands\n\n"
        "In groups, mention me or reply to one of my messages."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_history[update.effective_chat.id].clear()
    await update.message.reply_text("Memory cleared for this chat.")


def should_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False

    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return True

    bot_username = context.bot.username
    is_mentioned = bool(bot_username and f"@{bot_username.lower()}" in message.text.lower())
    is_reply_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == context.bot.id
    )
    return is_mentioned or is_reply_to_bot


def clean_group_prompt(text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    bot_username = context.bot.username
    if bot_username:
        return re.sub(f"@{re.escape(bot_username)}", "", text, flags=re.IGNORECASE).strip()
    return text.strip()


def build_messages(chat_id: int, user_text: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": BOT_SYSTEM_PROMPT}]
    messages.extend(chat_history[chat_id])
    messages.append({"role": "user", "content": user_text})
    return messages


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not should_answer(update, context):
        return

    message = update.effective_message
    chat_id = update.effective_chat.id
    user_text = clean_group_prompt(message.text, context)

    if not user_text:
        await message.reply_text("What would you like me to help with?")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        completion = await to_thread(
            client.chat.completions.create,
            model=GROQ_MODEL,
            messages=build_messages(chat_id, user_text),
            temperature=0.7,
            max_tokens=900,
        )
        answer = completion.choices[0].message.content.strip()
    except Exception:
        logger.exception("AI response failed")
        await message.reply_text("I could not get an AI response right now. Please try again.")
        return

    chat_history[chat_id].append({"role": "user", "content": user_text})
    chat_history[chat_id].append({"role": "assistant", "content": answer})
    await message.reply_text(answer[:4096])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram error", exc_info=context.error)


def main() -> None:
    require_config()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
