# amoCRM Chat Dry-Run Plan (no live sends yet)

Prerequisites: DNS+HTTPS green, channels connected, `scope_id` persisted, live gates OFF except temporary flags listed per step.

## Shared rules

- Never run with `AGENT7_LIVE_OUTREACH_ENABLED=true` during dry-run.
- Prefer `AMO_CHAT_MIRROR_LIVE=false` and `AMO_CHAT_WEBHOOK_ENABLED=false` until payload/signature verified offline.
- Use one known OwnerRequest + listing/thread per channel.
- Expect duplicates to no-op (`duplicate_msgid` / loop guard).

## Facebook scenario

1. Pick one real FB owner thread already correlated in OwnerRequestStore (or create dry-run correlation only).
2. `python3 scripts/amo_chat_setup.py dry-run-facebook`
3. Confirm signing + payload shape; no amojo network if connect/mirror live off.
4. With `AMO_CHAT_MIRROR_LIVE=true` **only** after explicit approval: mirror one outbound text once; replay same external_message_id → skipped.
5. Leave `AGENT7_FACEBOOK_MESSENGER_ENABLED=false` so source-native send stays blocked.
6. Manager outbound: enable `AMO_CHAT_WEBHOOK_ENABLED=true` later; with `openhome-api` still without live transport wiring / Agent7 gates OFF → no external send.

## Airbnb scenario

Same as Facebook using:

- `dry-run-airbnb`
- Airbnb OwnerRequest / thread
- `AGENT7_AIRBNB_MESSAGES_ENABLED=false`

## amo manager outbound (future, explicit approval)

1. Confirm webhook HTTPS + signature.
2. Send one controlled manager message from amoCRM UI.
3. Expect log `AMO_CHAT_MANAGER_OUTBOUND` and either `dry_run_or_no_transport` or gated send.
4. Deliver same webhook twice → second ignored (`AMO_CHAT_WEBHOOK_DUPLICATE`).

## Exit criteria

- No owner-visible duplicate messages
- Mirror store persists across restart
- Rollback (`deploy/ROLLBACK.md`) returns all gates OFF in <2 minutes
