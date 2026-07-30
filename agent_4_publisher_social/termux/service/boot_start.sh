#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
# Termux:Boot запускает этот файл после перезагрузки телефона.
# Лежит в ~/.termux/boot/ — bootstrap кладёт его туда сам.
#
# Задача: поднять wake-lock, дождаться сети, переподключить loopback ADB
# (порт после ребута ДРУГОЙ) и стартовать раннер.
# =============================================================================
set -uo pipefail

HOME_DIR="${HOME:-/data/data/com.termux/files/home}"
BIN_DIR="$HOME_DIR/bin"
LOG="$HOME_DIR/.publisher/boot.log"
mkdir -p "$(dirname "$LOG")"

echo "$(date '+%F %T') boot: старт" >> "$LOG"

# не дать системе усыпить нас сразу после загрузки
termux-wake-lock 2>/dev/null || true

# дождаться, пока поднимется сеть (ADB-сервер и mdns без неё не работают)
for i in $(seq 1 30); do
  if ip route get 1.1.1.1 >/dev/null 2>&1; then break; fi
  sleep 2
done

# Беспроводная отладка после ребута включается не мгновенно. Ищем её до двух
# минут, но не запускаем интерактивный запрос парринга во время загрузки.
CONNECTED=0
for _ in $(seq 1 12); do
  # `pub connect` берёт тот же crash-safe UI flock, что и live-раннер.
  if NONINTERACTIVE=1 "$BIN_DIR/pub" connect >> "$LOG" 2>&1; then
    CONNECTED=1
    break
  fi
  sleep 10
done

if [ "$CONNECTED" = "1" ]; then
  echo "$(date '+%F %T') boot: ADB подключён" >> "$LOG"
else
  echo "$(date '+%F %T') boot: ADB пока недоступен, раннер продолжит попытки" >> "$LOG"
fi

if "$BIN_DIR/pub" start >> "$LOG" 2>&1; then
  echo "$(date '+%F %T') boot: раннер запущен" >> "$LOG"
else
  echo "$(date '+%F %T') boot: раннер НЕ запустился" >> "$LOG"
fi
