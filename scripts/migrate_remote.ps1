$ErrorActionPreference = "Stop"

$server = "root@89.169.53.249"
$remotePath = "/opt/gsns-bot"
$localDb = "data/bot.db"
$remoteDb = "$remotePath/data/bot.db"

if (-not (Test-Path $localDb)) {
    throw "Local SQLite DB not found at $localDb"
}

scp $localDb "${server}:$remoteDb"

ssh $server "cd $remotePath && docker compose up -d db"

ssh $server ("cd $remotePath && docker compose run --rm -w /app -e PYTHONPATH=/app " +
    "-v /opt/gsns-bot/data:/data bot " +
    "python scripts/migrate_sqlite_to_postgres.py " +
    "--sqlite-url sqlite+aiosqlite:////data/bot.db --truncate")

ssh $server "cd $remotePath && docker compose up -d --build"
