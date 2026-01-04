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
CREATE TABLE IF NOT EXISTS deal_messages (
    id SERIAL PRIMARY KEY,
    deal_id INTEGER NOT NULL REFERENCES deals(id),
    sender_id BIGINT NOT NULL,
    sender_role VARCHAR(16) NOT NULL,
    message_type VARCHAR(16) NOT NULL,
    text TEXT,
    file_id VARCHAR(256),
    created_at TIMESTAMPTZ DEFAULT now()
);
SQL
"@

ssh $server $remoteCmd
