# Rollback (keep business state)

Goal: stop all outbound / mirror / webhook live behaviour **without deleting** OwnerRequest or amo chat mappings.

## Immediate kill switches (production `/opt/openhome/.env`)

```env
AGENT7_LIVE_OUTREACH_ENABLED=false
AGENT7_FACEBOOK_MESSENGER_ENABLED=false
AGENT7_AIRBNB_MESSAGES_ENABLED=false
AMO_CHAT_WEBHOOK_ENABLED=false
AMO_CHAT_MIRROR_LIVE=false
AMO_CHAT_CONNECT_LIVE=false
WAZZUP_SEND_ENABLED=false
WAZZUP_AUTO_REPLY_ENABLED=false
```

Then:

```bash
sudo systemctl restart openhome-api
curl -fsS http://127.0.0.1:8000/health
python3 /opt/openhome/app/scripts/live_gate_audit.py
```

## What stays

- `amo_chat_mirrors.json` mappings
- OwnerRequest records / UNKNOWN_SEND_STATE reconciliation data
- CRM business events (BUSINESS_EVENTS_ONLY path)
- HTTPS / nginx / systemd health endpoint

## What stops

- source-native FB/Airbnb/WA/TG sends
- amojo live import
- manager→source webhook handling (flag off → ignored)
- channel connect calls

## Verify safe mode

```bash
python3 /opt/openhome/app/scripts/production_status.py
# WEBHOOK / MIRROR / AGENT7 SOURCE_NATIVE must show OFF
```
