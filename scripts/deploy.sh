#!/usr/bin/env bash
#
# Деплой монорепо на VPS (Mac → /opt/openhome/app).
#
#   ./scripts/deploy.sh                          # весь репозиторий, только показать план
#   ./scripts/deploy.sh --yes                    # весь репозиторий, применить
#   ./scripts/deploy.sh --only agent_3_director --yes
#   ./scripts/deploy.sh --only agent_3_director --yes --restart chain-watcher
#
# Почему именно так:
#
#   * Права прибиваются на самом деплое (--chmod=D755,F644), а владелец
#     сбрасывается на сервисного пользователя. Локальные 600 и macOS-овские
#     uid 501 / staff больше не доезжают до сервера: раньше из-за них chain
#     падал с EACCES на файлах, которые он не мог прочитать.
#   * rsync НЕ удаляет лишнее без явного --prune. Деревья мака и сервера
#     разошлись (на сервере живут contact_role, tests, «Агент 9/10», которых
#     нет локально), и деплой с --delete 15.08 снёс рабочие файлы: сервис
#     openhome-api несколько дней крутился из удалённого entrypoint.
#   * После выкладки проверяется, что у каждого systemd-юнита файл из
#     ExecStart на месте и сервис поднялся. Именно эта проверка ловит
#     «удалили из-под работающего процесса».
#
set -euo pipefail

VPS_HOST="${OPENHOME_VPS_HOST:-root@72.60.108.152}"
REMOTE_ROOT="${OPENHOME_REMOTE_ROOT:-/opt/openhome/app}"
SERVICE_USER="${OPENHOME_SERVICE_USER:-openhome}"
BACKUP_ROOT="${OPENHOME_BACKUP_ROOT:-/opt/openhome/backups}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXCLUDE_FILE="$REPO_ROOT/scripts/deploy.exclude"

APPLY=0
PRUNE=0
DIFF_ONLY=0
PULL=0
RESTART_ARG=""
SUBPATH=""

usage() {
  cat <<'EOF'
Деплой монорепо на VPS (Mac → /opt/openhome/app).

  ./scripts/deploy.sh                                        сухой прогон по всему репо
  ./scripts/deploy.sh --yes                                  выкатить всё
  ./scripts/deploy.sh --only agent_3_director --yes          выкатить один агент
  ./scripts/deploy.sh --only agent_6_qualifier --yes --restart api,agent6

Флаги:
  --yes              применить (без него — сухой прогон, ничего не меняется)
  --diff             сверить содержимое мак↔сервер и показать, где чья версия
                     новее (ничего не выкатывает)
  --pull             забрать в репозиторий файлы, которые на сервере новее
                     (перед деплоем, чтобы не откатить серверные правки);
                     с --yes применяет, без него — только список
  --only PATH        выкатить только поддерево (путь относительно корня репо)
  --prune            удалять на сервере то, чего нет локально (сначала бэкап)
  --restart LIST     через запятую: chain-watcher,api,agent1,agent6,availability,
                     wazzup-webhook,amo-task или all / none (по умолчанию auto)
  -h, --help         эта справка
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) APPLY=1; shift ;;
    --diff) DIFF_ONLY=1; shift ;;
    --pull) PULL=1; shift ;;
    --prune) PRUNE=1; shift ;;
    --only) SUBPATH="${2:?--only требует путь}"; shift 2 ;;
    --restart) RESTART_ARG="${2:?--restart требует список}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Неизвестный флаг: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -f "$EXCLUDE_FILE" ]] || { echo "Нет $EXCLUDE_FILE" >&2; exit 1; }

if [[ -n "$SUBPATH" ]]; then
  SUBPATH="${SUBPATH%/}"
  [[ -d "$REPO_ROOT/$SUBPATH" ]] || { echo "Нет каталога $SUBPATH в репозитории" >&2; exit 1; }
  SRC="$REPO_ROOT/$SUBPATH/"
  DST="$REMOTE_ROOT/$SUBPATH/"
  SCOPE="$REMOTE_ROOT/$SUBPATH"
else
  SRC="$REPO_ROOT/"
  DST="$REMOTE_ROOT/"
  SCOPE="$REMOTE_ROOT"
fi

# Сознательно без -a и без -p: -a тащит владельца и режим с мака, а openrsync
# из macOS не понимает --chmod. Права и владельца выставляем на сервере ниже —
# получается детерминированно и не зависит от версии rsync.
RSYNC_OPTS=(
  -rltDvz
  --omit-dir-times
  --no-owner
  --no-group
  --exclude-from="$EXCLUDE_FILE"
  --human-readable
)

if [[ $PRUNE -eq 1 ]]; then
  # без --delete-excluded: исключённое (.env, data/, профили браузера)
  # на сервере остаётся нетронутым
  RSYNC_OPTS+=(--delete)
fi
[[ $APPLY -eq 1 ]] || RSYNC_OPTS+=(--dry-run)

if [[ $DIFF_ONLY -eq 1 || $PULL -eq 1 ]]; then
  # Деплой перезаписывает файл, даже если серверная копия новее. Поэтому перед
  # выкладкой сверяем содержимое: правки, сделанные когда-то прямо на сервере
  # и не вернувшиеся в репозиторий, иначе тихо откатятся.
  echo "── сверка $SRC ↔ $VPS_HOST:$DST ─────────"
  list=$(mktemp); local_man=$(mktemp); remote_man=$(mktemp); behind=$(mktemp)
  trap 'rm -f "$list" "$local_man" "$remote_man" "$behind"' EXIT

  # openrsync не умеет --out-format, берём имена из обычного -v.
  rsync -rltDvn --omit-dir-times --no-owner --no-group \
    --exclude-from="$EXCLUDE_FILE" "$SRC" "$VPS_HOST:$DST" 2>/dev/null \
    | grep -v '/$' | grep -vE '^(sent|total|received|Transfer|$)' > "$list"

  while IFS= read -r f; do
    [[ -f "$SRC$f" ]] || continue
    printf '%s\t%s\t%s\n' "$(shasum -a 256 "$SRC$f" | cut -c1-16)" \
      "$(stat -f %m "$SRC$f")" "$f"
  done < "$list" > "$local_man"

  ssh "$VPS_HOST" "cd '$DST' 2>/dev/null && while IFS= read -r f; do
      [ -f \"\$f\" ] || { printf 'ABSENT\t0\t%s\n' \"\$f\"; continue; }
      printf '%s\t%s\t%s\n' \"\$(sha256sum \"\$f\" | cut -c1-16)\" \"\$(stat -c %Y \"\$f\")\" \"\$f\"
    done" < "$list" > "$remote_man"

  awk -F'\t' -v behind="$behind" '
    NR==FNR { rh[$3]=$1; rt[$3]=$2; next }
    {
      f=$3
      if (!(f in rh) || rh[f]=="ABSENT") { new++; newf[new]=f; next }
      if (rh[f]==$1) { same++; next }
      if (rt[f] > $2) { back++; print f > behind } else { fwd++ }
    }
    END {
      printf "\n  идентичны:                            %d\n", same
      printf "  есть только локально, добавятся:       %d\n", new
      printf "  локальная версия свежее (штатно):      %d\n", fwd
      printf "  СЕРВЕРНАЯ свежее — деплой её ОТКАТИТ:  %d\n", back
      if (new) { print "\n  Добавятся:"; for (i=1;i<=new;i++) print "    " newf[i] }
    }' "$remote_man" "$local_man"

  if [[ -s "$behind" ]]; then
    echo
    echo "  Файлы, где сервер впереди репозитория:"
    sed 's/^/    /' "$behind"
  fi

  if [[ $PULL -eq 0 ]]; then
    echo
    [[ -s "$behind" ]] && echo "  Забрать их в репозиторий: --pull --yes"
    exit 0
  fi

  if [[ ! -s "$behind" ]]; then
    echo
    echo "Забирать нечего — репозиторий не отстаёт."
    exit 0
  fi

  if [[ $APPLY -eq 0 ]]; then
    echo
    echo "Сухой прогон. Повторите с --pull --yes, чтобы забрать эти файлы."
    exit 0
  fi

  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  BAK="$REPO_ROOT/.deploy-backups/pre-pull-$STAMP"
  mkdir -p "$BAK"
  while IFS= read -r f; do
    [[ -f "$SRC$f" ]] || continue
    mkdir -p "$BAK/$(dirname "$f")"
    cp -p "$SRC$f" "$BAK/$f"
  done < "$behind"
  echo
  echo "→ бэкап локальных версий: $BAK"

  rsync -rltvz --no-owner --no-group --files-from="$behind" \
    "$VPS_HOST:$DST" "$SRC"
  echo
  echo "Забрано. Проверьте изменения (git diff / сравнение с $BAK) и затем выкатывайте."
  exit 0
fi

echo "── деплой ────────────────────────────────────────────"
echo "  источник : $SRC"
echo "  цель     : $VPS_HOST:$DST"
echo "  режим    : $([[ $APPLY -eq 1 ]] && echo ПРИМЕНИТЬ || echo 'сухой прогон')$([[ $PRUNE -eq 1 ]] && echo ' + удаление лишнего' || echo '')"
echo "──────────────────────────────────────────────────────"

if [[ $PRUNE -eq 1 && $APPLY -eq 1 ]]; then
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  echo "→ бэкап $SCOPE перед удалением…"
  ssh "$VPS_HOST" "mkdir -p '$BACKUP_ROOT' && tar czf '$BACKUP_ROOT/pre-prune-$STAMP.tgz' -C '$(dirname "$SCOPE")' '$(basename "$SCOPE")' 2>/dev/null; ls -lh '$BACKUP_ROOT/pre-prune-$STAMP.tgz'"
fi

rsync "${RSYNC_OPTS[@]}" "$SRC" "$VPS_HOST:$DST"

if [[ $APPLY -eq 0 ]]; then
  echo
  echo "Сухой прогон. Ничего не изменено. Повторите с --yes."
  exit 0
fi

echo
echo "→ права и владелец…"
ssh "$VPS_HOST" bash -s -- "$SCOPE" "$SERVICE_USER" <<'REMOTE'
set -euo pipefail
SCOPE="$1"; SERVICE_USER="$2"

# Не трогаем: чужие бинарники (venv, node_modules) и runtime сервера (data,
# профили браузера) — там свои режимы и свои владельцы.
skip=( -path '*/node_modules' -o -path '*/.venv*' -o -path '*/venv'
       -o -path '*/data' -o -path '*/.fb_profile' -o -name '.fb_*_profile' )
# Секреты не расширяем никогда, даже если они попали в область деплоя.
secret=( -name '.env' -o -name '.env.bak*' -o -name '*.key' -o -name '*.pem' )

find "$SCOPE" \( "${skip[@]}" \) -prune -o -type d -print0 | xargs -0 chmod 755
find "$SCOPE" \( "${skip[@]}" \) -prune -o -type f \( "${secret[@]}" \) -prune -o -type f -print0 | xargs -0 chmod 644
find "$SCOPE" \( "${skip[@]}" \) -prune -o -type f -name '*.sh' -print0 | xargs -0 chmod 755
find "$SCOPE" \( "${skip[@]}" \) -prune -o -print0 | xargs -0 chown "$SERVICE_USER:$SERVICE_USER"

echo "  каталоги 755, файлы 644, sh-скрипты 755, владелец $SERVICE_USER"
REMOTE

echo
echo "→ отметка версии…"
GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_DIRTY="$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
ssh "$VPS_HOST" "cat > /opt/openhome/DEPLOYED_VERSION.json" <<EOF
{
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "git_commit": "$GIT_COMMIT",
  "uncommitted_files": $GIT_DIRTY,
  "scope": "$SCOPE",
  "pruned": $([[ $PRUNE -eq 1 ]] && echo true || echo false)
}
EOF

RESTART="${RESTART_ARG:-auto}"
if [[ "$RESTART" == "auto" ]]; then
  case "$SUBPATH" in
    "") RESTART="all" ;;
    agent_3_director|agent_2_registrar*) RESTART="chain-watcher" ;;
    agent_6_qualifier) RESTART="api,agent6,wazzup-webhook" ;;
    agent_1_parser) RESTART="agent1" ;;
    availability_service) RESTART="availability" ;;
    *) RESTART="none" ;;
  esac
fi

if [[ "$RESTART" != "none" ]]; then
  echo
  echo "→ перезапуск: $RESTART"
  ssh "$VPS_HOST" bash -s -- "$RESTART" <<'REMOTE'
set -uo pipefail
RESTART="$1"
if [[ "$RESTART" == "all" ]]; then
  UNITS="openhome-chain-watcher openhome-api openhome-agent1 openhome-agent6 openhome-availability openhome-wazzup-webhook"
else
  UNITS=""
  IFS=',' read -ra parts <<< "$RESTART"
  for p in "${parts[@]}"; do UNITS="$UNITS openhome-${p// /}"; done
fi
for u in $UNITS; do
  systemctl is-enabled "$u" >/dev/null 2>&1 || { echo "  пропуск $u (не enabled)"; continue; }
  systemctl restart "$u" && echo "  перезапущен $u"
done
REMOTE
fi

echo
echo "→ проверка после деплоя…"
ssh "$VPS_HOST" bash -s <<'REMOTE'
set -uo pipefail
sleep 3
fail=0
for u in $(systemctl list-unit-files --no-pager | grep -o '^openhome-[a-z0-9-]*\.service'); do
  # Файл из ExecStart должен существовать: иначе сервис не переживёт рестарт,
  # даже если прямо сейчас работает из удалённого inode.
  script="$(systemctl cat "$u" 2>/dev/null | grep -m1 '^ExecStart=' | awk '{print $2}')"
  note="OK"
  case "$script" in /*) [[ -e "$script" ]] || { note="НЕТ ФАЙЛА: $script"; fail=1; } ;; esac
  state="$(systemctl is-active "$u")"
  enabled="$(systemctl is-enabled "$u" 2>/dev/null || echo '-')"
  if [[ "$enabled" == "enabled" && "$state" != "active" ]]; then
    # oneshot-юниты по таймеру нормально лежат inactive
    systemctl show -p Type --value "$u" | grep -q oneshot || { note="НЕ ПОДНЯЛСЯ ($state)"; fail=1; }
  fi
  printf '  %-34s %-9s %s\n' "$u" "$state" "$note"
done
world=$(find /opt/openhome/app -type f ! -perm -o=r \
  \( -name '*.py' -o -name '*.mjs' -o -name '*.js' -o -name '*.html' -o -name '*.sh' \) \
  -not -path '*/node_modules/*' -not -path '*/.venv*' -not -path '*/data/*' \
  -not -path '*/.fb_profile/*' 2>/dev/null | wc -l)
echo "  исходников с закрытыми правами: $world"
alien=$(find /opt/openhome/app -nouser -not -path '*/node_modules/*' 2>/dev/null | wc -l)
echo "  файлов с чужим владельцем: $alien"
exit $fail
REMOTE

echo
echo "Готово."
