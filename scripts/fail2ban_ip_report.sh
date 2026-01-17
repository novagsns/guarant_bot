#!/usr/bin/env bash
set -euo pipefail

JAIL="sshd"
FILE=""
IPS=()

usage() {
  cat <<'EOF'
Usage: fail2ban_ip_report.sh [--jail NAME] [--file PATH] [ip ...]

If no IPs are provided, the script reads the banned IP list from fail2ban.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --jail)
      JAIL="${2:-sshd}"
      shift 2
      ;;
    --file)
      FILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      IPS+=("$1")
      shift
      ;;
  esac
done

if [[ -n "$FILE" ]]; then
  if [[ ! -f "$FILE" ]]; then
    echo "File not found: $FILE"
    exit 1
  fi
  mapfile -t file_ips < <(grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' "$FILE" | sort -u)
  IPS+=("${file_ips[@]}")
fi

if [[ ${#IPS[@]} -eq 0 ]]; then
  if ! command -v fail2ban-client >/dev/null 2>&1; then
    echo "fail2ban-client not found."
    exit 1
  fi
  banned_line=$(fail2ban-client status "$JAIL" 2>/dev/null | awk -F': ' '/Banned IP list/ {print $2}')
  if [[ -z "${banned_line}" ]]; then
    echo "No banned IPs for jail: $JAIL"
    exit 0
  fi
  read -r -a IPS <<< "$banned_line"
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found."
  exit 1
fi

echo "Lookup source for IPs (provider: ipinfo.io)"
for ip in "${IPS[@]}"; do
  json=$(curl -sS "https://ipinfo.io/${ip}/json" || true)
  if [[ -z "$json" ]]; then
    echo "$ip | lookup failed"
    continue
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 - "$ip" <<'PY'
import json
import sys

ip = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    print(f"{ip} | parse failed")
    sys.exit(0)

city = data.get("city") or "-"
region = data.get("region") or "-"
country = data.get("country") or "-"
org = data.get("org") or "-"
loc = data.get("loc") or "-"
tz = data.get("timezone") or "-"
print(f"{ip} | {country} {region} {city} | {org} | loc={loc} | tz={tz}")
PY
  elif command -v python >/dev/null 2>&1; then
    printf '%s' "$json" | python - "$ip" <<'PY'
import json
import sys

ip = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    print(f"{ip} | parse failed")
    sys.exit(0)

city = data.get("city") or "-"
region = data.get("region") or "-"
country = data.get("country") or "-"
org = data.get("org") or "-"
loc = data.get("loc") or "-"
tz = data.get("timezone") or "-"
print(f"{ip} | {country} {region} {city} | {org} | loc={loc} | tz={tz}")
PY
  else
    city=$(printf '%s' "$json" | sed -n 's/.*"city":"\([^"]*\)".*/\1/p')
    region=$(printf '%s' "$json" | sed -n 's/.*"region":"\([^"]*\)".*/\1/p')
    country=$(printf '%s' "$json" | sed -n 's/.*"country":"\([^"]*\)".*/\1/p')
    org=$(printf '%s' "$json" | sed -n 's/.*"org":"\([^"]*\)".*/\1/p')
    city=${city:-"-"}
    region=${region:-"-"}
    country=${country:-"-"}
    org=${org:-"-"}
    echo "$ip | $country $region $city | $org"
  fi
done
