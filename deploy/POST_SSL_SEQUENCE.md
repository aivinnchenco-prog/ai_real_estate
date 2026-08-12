# Post DNS + SSL sequence (do not run live steps early)

1. HTTPS health PASS — `curl -i https://api.open-home.online/health`
2. `python3 scripts/dns_readiness.py` → READY
3. `python3 scripts/production_status.py` → DNS/HTTPS READY
4. `python3 scripts/amo_chat_registration_info.py` PASS
5. Send amoCRM support request (`deploy/amo_chat_registration_request.md`)
6. Receive channel_id / channel_secret / bot_id for FB + Airbnb
7. Place secrets in `/opt/openhome/.env` only (chmod 600)
8. `python3 scripts/amo_chat_apply_channel_config.py`
9. Briefly set `AMO_CHAT_CONNECT_LIVE=true`, run:
   - `python3 scripts/amo_chat_setup.py connect-facebook`
   - `python3 scripts/amo_chat_setup.py connect-airbnb`
   then set `AMO_CHAT_CONNECT_LIVE=false`
10. Confirm scope_ids persisted; webhook URL uses `:scope_id`
11. Enable **webhook only** (`AMO_CHAT_WEBHOOK_ENABLED=true`) — Agent7 still OFF
12. Dry-run inbound/outbound per `deploy/AMO_CHAT_DRY_RUN_PLAN.md`
13. Enable mirror (`AMO_CHAT_MIRROR_LIVE=true`) after webhook OK
14. Only later: Agent7 source-native live (separate written approval)
