$ErrorActionPreference = "Stop"

$server = "root@89.169.53.249"
$remotePath = "/opt/gsns-bot"

ssh $server "cd $remotePath && docker compose restart bot"
