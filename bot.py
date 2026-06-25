import logging
import os
import re
import sqlite3
from asyncio import gather, to_thread
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
ENABLE_MULTI_CHAIN_REASONING = os.getenv(
    "ENABLE_MULTI_CHAIN_REASONING",
    "true",
).lower() in {"1", "true", "yes", "on"}
REASONING_CHAINS = max(1, min(5, int(os.getenv("REASONING_CHAINS", "3"))))
MULTI_CHAIN_MIN_CHARS = int(os.getenv("MULTI_CHAIN_MIN_CHARS", "80"))
REASONING_TRIGGER_WORDS = {
    "analyze",
    "architecture",
    "calculate",
    "compare",
    "debug",
    "decide",
    "design",
    "diagnose",
    "evaluate",
    "explain",
    "fix",
    "how",
    "plan",
    "problem",
    "reason",
    "solve",
    "strategy",
    "tradeoff",
    "why",
}

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(chat_id, key)
                )
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

    def get_setting(self, chat_id: int, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT value
                FROM chat_settings
                WHERE chat_id = ? AND key = ?
                """,
                (chat_id, key),
            ).fetchone()
        return str(row[0]) if row else None

    def set_setting(self, chat_id: int, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_settings(chat_id, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id, key)
                DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, key, value),
            )


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
        "/reasoning - show or change multi-chain reasoning\n"
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


def multi_chain_enabled(chat_id: int) -> bool:
    setting = memory.get_setting(chat_id, "multi_chain_reasoning")
    if setting is None:
        return ENABLE_MULTI_CHAIN_REASONING
    return setting == "on"


async def reasoning_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    action = context.args[0].lower() if context.args else "status"

    if action in {"on", "enable", "enabled"}:
        memory.set_setting(chat_id, "multi_chain_reasoning", "on")
        await update.message.reply_text("Multi-chain reasoning is on for this chat.")
        return

    if action in {"off", "disable", "disabled"}:
        memory.set_setting(chat_id, "multi_chain_reasoning", "off")
        await update.message.reply_text("Multi-chain reasoning is off for this chat.")
        return

    state = "on" if multi_chain_enabled(chat_id) else "off"
    await update.message.reply_text(
        f"Multi-chain reasoning is {state}. "
        f"When active, complex prompts use {REASONING_CHAINS} independent chains "
        "and a final synthesis answer. Use /reasoning on or /reasoning off."
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


def create_chat_completion(
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 900,
) -> str:
    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return completion.choices[0].message.content.strip()


def should_use_multi_chain(chat_id: int, user_text: str) -> bool:
    if not multi_chain_enabled(chat_id) or REASONING_CHAINS <= 1:
        return False

    normalized = user_text.lower()
    has_trigger = any(word in normalized for word in REASONING_TRIGGER_WORDS)
    return len(user_text) >= MULTI_CHAIN_MIN_CHARS or has_trigger or "?" in user_text


def build_chain_messages(
    base_messages: list[dict[str, str]],
    user_text: str,
    chain_number: int,
) -> list[dict[str, str]]:
    chain_prompt = (
        "Answer the user's latest request using an independent reasoning path. "
        "Think privately and do not reveal chain-of-thought. Return only a concise, "
        "actionable answer with key assumptions and caveats when useful.\n\n"
        f"Reasoning path: {chain_number}\n"
        f"Latest request: {user_text}"
    )
    return [
        *base_messages[:-1],
        {"role": "user", "content": chain_prompt},
    ]


def build_synthesis_messages(
    base_messages: list[dict[str, str]],
    user_text: str,
    chain_answers: list[str],
) -> list[dict[str, str]]:
    chain_text = "\n\n".join(
        f"Attempt {index + 1}:\n{answer}"
        for index, answer in enumerate(chain_answers)
    )
    synthesis_prompt = (
        "Synthesize the independent attempts into the best final response. "
        "Resolve contradictions, keep useful nuance, and do not mention hidden "
        "reasoning or chain-of-thought. Be direct and practical.\n\n"
        f"Latest request:\n{user_text}\n\n"
        f"Independent attempts:\n{chain_text}"
    )
    return [
        *base_messages[:-1],
        {"role": "user", "content": synthesis_prompt},
    ]


async def generate_answer(chat_id: int, user_text: str) -> str:
    base_messages = build_messages(chat_id, user_text)

    if not should_use_multi_chain(chat_id, user_text):
        return await to_thread(create_chat_completion, base_messages)

    chain_tasks = [
        to_thread(
            create_chat_completion,
            build_chain_messages(base_messages, user_text, chain_number),
            0.9,
            700,
        )
        for chain_number in range(1, REASONING_CHAINS + 1)
    ]
    chain_answers = await gather(*chain_tasks)
    return await to_thread(
        create_chat_completion,
        build_synthesis_messages(base_messages, user_text, chain_answers),
        0.45,
        1000,
    )


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
        answer = await generate_answer(chat_id, user_text)
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
    application.add_handler(CommandHandler("reasoning", reasoning_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
