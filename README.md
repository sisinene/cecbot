# Telegram AI Bot

A small Python Telegram bot that answers messages with Groq-hosted AI.

## Features

- Private chat replies
- Group chat replies when mentioned or replied to
- Short per-chat memory
- `/start`, `/help`, and `/reset` commands
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

Because bot and API keys are secrets, do not commit `.env`. If a key was shared in chat or exposed publicly, rotate it in the relevant provider dashboard.
