# contact_role — canonical contact role + dual sync

Source of truth: **canonical role** (`CLIENT` / `OWNER` / `AGENT` / `UNKNOWN`).

Mirrors (independent, fail-isolated):

1. amoCRM — field `Тип контакта` + tags CLIENT/OWNER/AGENT  
2. WhatsApp native lists — existing Playwright worker (`whatsapp_ui_sync`)

Notion «Агент/Владелец (тип)»: Владелец→OWNER, Агент→AGENT, **empty→UNKNOWN**.

See `AMOCRM_SCHEMA.md` for the manual field requirement.

Dry-run:

```bash
python3 scripts/contact_role_dual_sync_diagnose.py \
  --phone +66XXXXXXXXX --role CLIENT --source MANUAL
```
