$ErrorActionPreference = "Stop"

$server = "root@89.169.53.249"
$remotePath = "/opt/gsns-bot"
$archive = ".deploy.tgz"

if (Test-Path $archive) {
    Remove-Item -Force $archive
}

tar -czf $archive `
    --exclude=".git" `
    --exclude=".deploy.tgz" `
    --exclude=".env" `
    --exclude="data" `
    --exclude="logs" `
    --exclude="__pycache__" `
    --exclude="*.pyc" `
    --exclude=".venv" `
    --exclude=".backup_before_format.zip" `
    .

scp $archive "${server}:$remotePath/$archive"

ssh $server "cd $remotePath && tar -xzf $archive && rm -f $archive && docker compose up -d --build"

Remove-Item -Force $archive

$gitRoot = git rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -eq 0 -and $gitRoot) {
    $status = git status --porcelain
    if ($status) {
        git add -A
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        git commit -m "Auto deploy: $timestamp"
    }
    $origin = git remote get-url origin 2>$null
    if ($origin) {
        $branch = git rev-parse --abbrev-ref HEAD
        git push origin $branch
    } else {
        Write-Host "No git remote named 'origin'; skipping push."
    }
}
