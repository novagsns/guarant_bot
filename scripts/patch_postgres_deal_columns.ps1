$ErrorActionPreference = "Stop"

$server = "root@89.169.53.249"
$remotePath = "/opt/gsns-bot"

$envFile = Join-Path $PSScriptRoot "..\\.env"
if (-not (Test-Path $envFile)) {
    throw ".env not found at $envFile"
}

$envMap = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$') {
        $envMap[$matches[1]] = $matches[2]
    }
}

$pgUser = $envMap["POSTGRES_USER"]
$pgDb = $envMap["POSTGRES_DB"]
$pgPassword = $envMap["POSTGRES_PASSWORD"]
if (-not $pgUser -or -not $pgDb -or -not $pgPassword) {
    throw "Missing POSTGRES_USER/POSTGRES_DB/POSTGRES_PASSWORD in .env"
}

$remoteCmd = @"
cd $remotePath
docker compose exec -T -e PGPASSWORD=$pgPassword db psql -U $pgUser -d $pgDb <<'SQL'
ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_chat_id BIGINT;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_invite_link TEXT;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS room_ready BOOLEAN DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS deal_rooms (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT UNIQUE NOT NULL,
    title VARCHAR(255),
    invite_link TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_by BIGINT REFERENCES users(id),
    assigned_deal_id INTEGER REFERENCES deals(id),
    created_at TIMESTAMPTZ DEFAULT now()
);
SQL
"@

ssh $server $remoteCmd
