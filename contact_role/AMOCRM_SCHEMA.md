# Canonical Contact Role — amoCRM schema

## Reconcile (preferred)

```bash
# plan only
python3 scripts/amocrm_reconcile_contact_role_schema.py --dry-run

# schema/tags only (no contact role writes)
python3 scripts/amocrm_reconcile_contact_role_schema.py --apply --write-cache
```

Uses existing `agent6_qualifier.amo.AmoClient` (Bearer `AMO_ACCESS_TOKEN`).

Resolved IDs cache (optional): `contact_role/data/amocrm_contact_role_schema.json`

## Target schema

| Параметр | Значение |
|----------|----------|
| Entity | **Contact** |
| Field name | `Тип контакта` |
| Type | `select` |
| Enums | `Клиент`, `Владелец`, `Агент`, `Не определено` |
| Managed tags | `CLIENT`, `OWNER`, `AGENT` |

Tags may be created by API on apply; field is created/normalized by reconciler.

## Mapping

| Canonical | Тип контакта | Tag | WhatsApp |
|-----------|--------------|-----|----------|
| CLIENT | Клиент | CLIENT | Client |
| OWNER | Владелец | OWNER | Owner |
| AGENT | Агент | AGENT | Owner |
| UNKNOWN | Не определено | (none) | no-op |

## Live role writes

Still disabled by default:

```
CONTACT_ROLE_AMO_SYNC_ENABLED=false
CONTACT_ROLE_AMO_DRY_RUN=true
WHATSAPP_UI_SYNC_ENABLED=false
WHATSAPP_UI_DRY_RUN=true
```

Dual-sync diagnose (no writes):

```bash
python3 scripts/contact_role_dual_sync_diagnose.py \
  --phone +66XXXXXXXXX --role CLIENT --source MANUAL
```
