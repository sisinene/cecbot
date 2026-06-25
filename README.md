# Telegram AI Bot

A small Python Telegram bot that answers messages with Groq-hosted AI.

## Features

- Private chat replies
- Group chat replies when mentioned or replied to
- Persistent per-chat SQLite memory
- Multi-chain reasoning for complex prompts
- `/start`, `/help`, `/memory`, `/reasoning`, and `/reset` commands
- Credentials loaded from environment variables

## Setup

1. Install Python 3.10 or newer.
2. Create a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

4. Create a `.env` file from `.env.example` and fill in:

   ```env
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   GROQ_API_KEY=your_groq_api_key
   GROQ_MODEL=llama-3.3-70b-versatile
   BOT_SYSTEM_PROMPT=You are a helpful, concise AI assistant inside Telegram.
   MEMORY_DB_PATH=bot_memory.sqlite3
   MAX_HISTORY_MESSAGES=20
   MAX_STORED_MESSAGES=300
   ENABLE_MULTI_CHAIN_REASONING=true
   REASONING_CHAINS=3
   MULTI_CHAIN_MIN_CHARS=80
   ```

5. Run the bot:

   ```powershell
   python bot.py
   ```

## Deploy Notes

This bot uses Telegram long polling, so it can run on a VPS, local machine, or worker process without setting up a public webhook URL.

For production, set these environment variables in your host instead of committing a `.env` file:

- `TELEGRAM_BOT_TOKEN`
- `GROQ_API_KEY`
- `GROQ_MODEL`
- `BOT_SYSTEM_PROMPT`
- `MEMORY_DB_PATH`
- `MAX_HISTORY_MESSAGES`
- `MAX_STORED_MESSAGES`
- `ENABLE_MULTI_CHAIN_REASONING`
- `REASONING_CHAINS`
- `MULTI_CHAIN_MIN_CHARS`

## Memory

Chat memory is stored in a local SQLite database. By default, the bot keeps up to 300 messages per chat and sends the latest 20 memory messages to the AI model as context.

Use `/memory` in Telegram to see how many memory messages are stored for the current chat. Use `/reset` to clear memory for that chat.

## Multi-Chain Reasoning

When multi-chain reasoning is enabled, complex prompts generate several independent private answer attempts and then synthesize them into one final response. The bot does not reveal hidden chain-of-thought.

Use `/reasoning` to check the current chat's setting. Use `/reasoning on` or `/reasoning off` to change it for that chat.

Tune depth and latency with:

- `ENABLE_MULTI_CHAIN_REASONING`: default on/off behavior
- `REASONING_CHAINS`: number of independent attempts, clamped from 1 to 5
- `MULTI_CHAIN_MIN_CHARS`: prompt length threshold for automatic multi-chain mode

Because bot and API keys are secrets, do not commit `.env`. If a key was shared in chat or exposed publicly, rotate it in the relevant provider dashboard.
