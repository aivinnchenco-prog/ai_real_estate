#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env.real-estate" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env.real-estate"
  set +a
elif [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
fi

NOTION_API_URL="https://api.notion.com/v1"

# ============================================
# Утилиты
# ============================================

notion_get_properties() {
    # Получить схему базы
    curl -s "$NOTION_API_URL/databases/$NOTION_DB_ID" \
        -H "Authorization: Bearer $NOTION_API_KEY" \
        -H "Notion-Version: $NOTION_API_VERSION" | jq '.properties'
}

notion_query() {
    # Выполнить query к базе
    # Использование: notion_query '{"filter":{"property":"Статус","status":{"equals":"ready_for_video"}}}'
    local filter="$1"
    local limit="${2:-10}"
    
    curl -s "$NOTION_API_URL/databases/$NOTION_DB_ID/query" \
        -X POST \
        -H "Authorization: Bearer $NOTION_API_KEY" \
        -H "Notion-Version: $NOTION_API_VERSION" \
        -H "Content-Type: application/json" \
        -d "{
            \"filter\": $filter,
            \"page_size\": $limit
        }" | jq '.results'
}

notion_get_page() {
    # Получить страницу по ID
    local page_id="$1"
    
    curl -s "$NOTION_API_URL/pages/$page_id" \
        -H "Authorization: Bearer $NOTION_API_KEY" \
        -H "Notion-Version: $NOTION_API_VERSION" | jq '.'
}

notion_update_page() {
    # Обновить страницу
    # Использование: notion_update_page '<page_id>' '{"properties":{"Статус":{"status":{"name":"ready_to_post"}}}}'
    local page_id="$1"
    local updates="$2"
    
    curl -s "$NOTION_API_URL/pages/$page_id" \
        -X PATCH \
        -H "Authorization: Bearer $NOTION_API_KEY" \
        -H "Notion-Version: $NOTION_API_VERSION" \
        -H "Content-Type: application/json" \
        -d "$updates" | jq '.'
}

generate_object_id() {
    # YYYYMMDD_NNN — порядковый номер за день из Notion
    python3 "$ROOT/real_estate_handler.py" --next-id
}

# ============================================
# Команды CLI
# ============================================

case "${1:-help}" in
    
    properties)
        # Вывести все поля в базе
        echo "📋 Свойства CRM Notion:"
        notion_get_properties | jq 'keys | .[]' -r
        ;;
    
    list)
        # Список объектов с статусом (по умолчанию ready_for_video)
        local status="${2:-ready_for_video}"
        echo "🔄 Объекты со статусом: $status"
        notion_query "{\"property\":\"Статус\",\"status\":{\"equals\":\"$status\"}}" 20 | \
            jq '.[] | {id: .id, title: .properties["Название объекта"].title[0].text.content, status: .properties.Статус.status.name}'
        ;;
    
    get)
        # Получить объект по ID
        if [ -z "$2" ]; then
            echo "❌ Использование: $0 get <page_id>"
            exit 1
        fi
        echo "📄 Получаю объект: $2"
        notion_get_page "$2" | jq '.properties'
        ;;
    
    generate-id)
        echo "🆔 Следующий Object ID (YYYYMMDD_NNN):"
        generate_object_id
        ;;
    
    help)
        cat << 'HELP'
Real Estate CRM CLI

КОМАНДЫ:
  properties              Вывести все поля CRM
  list [статус]           Список объектов со статусом (по умолч. ready_for_video)
  get <page_id>           Получить объект по ID
  generate-id               Следующий Object ID (YYYYMMDD_NNN)
  
ПРИМЕРЫ:
  ./real_estate_cli.sh properties
  ./real_estate_cli.sh list ready_for_video
  ./real_estate_cli.sh get e817ce50e788...
  ./real_estate_cli.sh generate-id

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:
  NOTION_API_KEY         API ключ Notion
  NOTION_DB_ID           ID базы Notion
  CONTACT_PHONE          Контактный телефон (по умолч. +66625124001)

HELP
        ;;
    
    *)
        echo "❌ Неизвестная команда: $1"
        echo "Используйте: $0 help"
        exit 1
        ;;
esac
