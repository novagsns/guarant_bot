$ErrorActionPreference = "Stop"

$server = "root@89.169.53.249"
$remotePath = "/opt/gsns-bot"
$expected = @("bot", "db")

$runningRaw = ssh $server "cd $remotePath && docker compose ps --services --filter status=running"
$running = $runningRaw -split "`n" | Where-Object { $_ -and $_.Trim() -ne "" }

$missing = $expected | Where-Object { $_ -notin $running }
if ($missing.Count -gt 0) {
    Write-Host ("Missing or not running: " + ($missing -join ", "))
    exit 1
}

Write-Host "OK: all services running."
