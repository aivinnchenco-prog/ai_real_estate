# amoCRM Chat Registration Checklist

## Pre-flight

- [ ] DNS resolves: `api.open-home.online` → `72.60.108.152`
- [ ] HTTPS valid (`certbot` + browser/curl)
- [ ] `GET https://api.open-home.online/health` = 200
- [ ] webhook path reachable (`POST .../webhooks/amo-chat/unknown` → safe 404)
- [ ] ACCOUNT_ID confirmed: `33148394`
- [ ] AMOJO_ID confirmed: `41a97e13-a7b6-482c-9312-b7f7724952f0`
- [ ] CLIENT_UUID confirmed: `831ec8bc-1565-4c9c-a10d-80b4162dadb6`
- [ ] Facebook registration data ready (`OpenHomeFacebook`)
- [ ] Airbnb registration data ready (`OpenHomeAirbnb`)
- [ ] icons ready (`deploy/icons/*.svg`) — confirm amo size/format with support if required
- [ ] live gates OFF (`python3 scripts/live_gate_audit.py`)

## Registration

- [ ] request sent to amoCRM (`deploy/amo_chat_registration_request.md`)
- [ ] channel_id received (FB + Airbnb)
- [ ] channel_secret received (FB + Airbnb)
- [ ] bot_id received (FB + Airbnb)
- [ ] secrets added to production `/opt/openhome/.env` (chmod 600)
- [ ] `python3 scripts/amo_chat_apply_channel_config.py` → both channels present
- [ ] connect-facebook (with `AMO_CHAT_CONNECT_LIVE=true` briefly)
- [ ] connect-airbnb
- [ ] scope_id received + persisted (idempotent re-connect OK)
- [ ] webhook mapping verified (`:scope_id` routes to FB/Airbnb)

## Dry-run / go-live (later)

- [ ] dry-run plan executed (`deploy/AMO_CHAT_DRY_RUN_PLAN.md`)
- [ ] enable `AMO_CHAT_WEBHOOK_ENABLED` only (still no Agent7 live)
- [ ] enable `AMO_CHAT_MIRROR_LIVE` only after webhook OK
- [ ] live approval for Agent7 source-native (separate decision)
- [ ] rollback plan reviewed (`deploy/ROLLBACK.md`)
