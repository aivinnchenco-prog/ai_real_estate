#!/bin/bash
# Запуск Agent 7 (юзербот) с автоперезапуском при сбое.
# Использование: bash scripts/start_userbot.sh
cd "$(dirname "$0")/.."

# не плодим дубли
pkill -f "agent6_qualifier.tg_userbot" 2>/dev/null
sleep 1

while true; do
  PYTHONUNBUFFERED=1 PYTHONPATH=src python3 -m agent6_qualifier.tg_userbot
  code=$?
  if [ $code -eq 0 ] || [ $code -eq 130 ]; then
    echo "userbot остановлен штатно (код $code)"
    break
  fi
  echo "[restart] userbot упал (код $code), перезапуск через 5 секунд..."
  sleep 5
done
