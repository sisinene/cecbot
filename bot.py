import logging
import os
import re
import sqlite3
from asyncio import to_thread
from pathlib import Path

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
MAX_STORED_MESSAGES = int(os.getenv("MAX_STORED_MESSAGES", "300"))
MEMORY_DB_PATH = Path(os.getenv("MEMORY_DB_PATH", "bot_memory.sqlite3"))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("telegram-ai-bot")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


class ChatMemory:
    def __init__(self, db_path: Path, context_limit: int, store_limit: int) -> None:
        self.db_path = db_path
        self.context_limit = context_limit
        self.store_limit = store_limit
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_chat_id_id
                ON messages(chat_id, id)
                """
            )

    def recent(self, chat_id: int) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content
                FROM messages
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, self.context_limit),
            ).fetchall()

        return [
            {"role": role, "content": content}
            for role, content in reversed(rows)
        ]

    def append_pair(self, chat_id: int, user_text: str, assistant_text: str) -> None:
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO messages(chat_id, role, content)
                VALUES (?, ?, ?)
                """,
                (
                    (chat_id, "user", user_text),
                    (chat_id, "assistant", assistant_text),
                ),
            )
            connection.execute(
                """
                DELETE FROM messages
                WHERE chat_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM messages
                      WHERE chat_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (chat_id, chat_id, self.store_limit),
            )

    def clear(self, chat_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))

    def count(self, chat_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return int(row[0])


memory = ChatMemory(MEMORY_DB_PATH, MAX_HISTORY_MESSAGES, MAX_STORED_MESSAGES)


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
        "/memory - show this chat's stored memory size\n"
        "/reset - clear this chat's memory\n"
        "/help - show commands\n\n"
        "In groups, mention me or reply to one of my messages."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    memory.clear(update.effective_chat.id)
    await update.message.reply_text("Memory cleared for this chat.")


async def memory_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stored_messages = memory.count(update.effective_chat.id)
    await update.message.reply_text(
        f"This chat has {stored_messages} stored memory messages. "
        f"I use the latest {MAX_HISTORY_MESSAGES} messages as context."
    )


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
    messages.extend(memory.recent(chat_id))
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

    memory.append_pair(chat_id, user_text, answer)
    await message.reply_text(answer[:4096])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram error", exc_info=context.error)


def main() -> None:
    require_config()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("memory", memory_status))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
