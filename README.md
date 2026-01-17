# Telegram Trade Bot (MVP)

This is a starter scaffold for a trading bot with anonymous in-bot messaging,
ads catalog, and guarantor assignment via admin topic.

## Quick start

1. Copy `.env.example` to `.env` and fill values.
2. Create a virtualenv and install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the bot:

```bash
python -m bot.main
```

## Docker (Postgres)

This setup runs the bot and a Postgres database using Docker Compose.

1. Copy `.env.example` to `.env` and fill values:
   - `BOT_TOKEN`, `BOT_USERNAME`, `ADMIN_CHAT_ID`, `OWNER_IDS`
   - `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
   - Set `DATABASE_URL` to Postgres:
     `postgresql+asyncpg://bot:bot@db:5432/botdb`
2. Build and start:

```bash
docker compose up -d --build
```
