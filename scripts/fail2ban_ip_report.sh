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

PYTHON=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
fi

PYINFO_CODE=""
PYAPI_CODE=""
if [[ -n "$PYTHON" ]]; then
  PYINFO_CODE=$(cat <<'PY'
import json
import sys

ip = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

if data.get("error"):
    sys.exit(1)

city = data.get("city") or "-"
region = data.get("region") or "-"
country = data.get("country") or "-"
org = data.get("org") or "-"
loc = data.get("loc") or "-"
tz = data.get("timezone") or "-"
print(f"{ip} | {country} {region} {city} | {org} | loc={loc} | tz={tz}")
PY
  )
  PYAPI_CODE=$(cat <<'PY'
import json
import sys

ip = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

if data.get("status") != "success":
    sys.exit(1)

city = data.get("city") or "-"
region = data.get("regionName") or "-"
country = data.get("country") or "-"
org = data.get("org") or data.get("isp") or "-"
lat = data.get("lat")
lon = data.get("lon")
loc = f"{lat},{lon}" if lat is not None and lon is not None else "-"
tz = data.get("timezone") or "-"
print(f"{ip} | {country} {region} {city} | {org} | loc={loc} | tz={tz}")
PY
  )
fi

echo "Lookup source for IPs (provider: ipinfo.io, fallback: ip-api.com)"
for ip in "${IPS[@]}"; do
  json=$(curl -sS "https://ipinfo.io/${ip}/json" || true)
  output=""
  if [[ -n "$PYTHON" ]]; then
    if output=$(printf '%s' "$json" | "$PYTHON" -c "$PYINFO_CODE" "$ip"); then
      echo "$output"
      continue
    fi
  fi

  json=$(curl -sS "http://ip-api.com/json/${ip}?fields=status,message,country,regionName,city,org,isp,lat,lon,timezone" || true)
  if [[ -n "$PYTHON" ]]; then
    if output=$(printf '%s' "$json" | "$PYTHON" -c "$PYAPI_CODE" "$ip"); then
      echo "$output"
      continue
    fi
  else
    city=$(printf '%s' "$json" | sed -n 's/.*"city":"\([^"]*\)".*/\1/p')
    region=$(printf '%s' "$json" | sed -n 's/.*"regionName":"\([^"]*\)".*/\1/p')
    country=$(printf '%s' "$json" | sed -n 's/.*"country":"\([^"]*\)".*/\1/p')
    org=$(printf '%s' "$json" | sed -n 's/.*"org":"\([^"]*\)".*/\1/p')
    city=${city:-"-"}
    region=${region:-"-"}
    country=${country:-"-"}
    org=${org:-"-"}
    echo "$ip | $country $region $city | $org"
    continue
  fi

  echo "$ip | lookup failed"
done
