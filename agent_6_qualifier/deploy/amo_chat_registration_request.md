# amoCRM Custom Chat — Facebook only

**Status:** READY TO SEND after `https://api.open-home.online/health` returns 200.

Airbnb custom chat is **not** part of this integration (`AMO_CHAT_AIRBNB_ENABLED=false`).

Use the copy-paste text in `amo_chat_support_message.md` when contacting amoCRM support.

## Channel

| Field | Value |
|-------|-------|
| Technical code | `OpenHomeFacebook` |
| Display name | `Open Home | Facebook Marketplace` |
| account_id | `33148394` |
| amojo_id | `41a97e13-a7b6-482c-9312-b7f7724952f0` |
| client_uuid | `831ec8bc-1565-4c9c-a10d-80b4162dadb6` |
| Webhook | `https://api.open-home.online/webhooks/amo-chat/:scope_id` |

## After amo approval

1. Set in `/opt/openhome/.env`: `AMO_CHAT_FB_CHANNEL_ID`, `AMO_CHAT_FB_CHANNEL_SECRET`, `AMO_CHAT_FB_BOT_ID`, `AMO_CHAT_ACCOUNT_ID`
2. `python scripts/amo_chat_setup.py connect-facebook` (with `AMO_CHAT_CONNECT_LIVE=true`)
3. Set `AMO_CHAT_FB_SCOPE_ID` from connect result
4. Enable live: `AMO_CHAT_MIRROR_LIVE=true` when ready
