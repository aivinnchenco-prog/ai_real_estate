# amoCRM Custom Chat Channel Registration Request

**Status:** READY TO SEND after `https://api.open-home.online/health` returns 200.  
**Do not enable Agent7 / amo chat live until credentials + scope_id are verified in dry-run.**

---

## Account / integration (shared)

| Field | Value |
|---|---|
| amoCRM account ID | `33148394` |
| subdomain | `aivinnchenco` |
| amojo_id | `41a97e13-a7b6-482c-9312-b7f7724952f0` |
| client_uuid (integration) | `831ec8bc-1565-4c9c-a10d-80b4162dadb6` |
| Webhook URL template | `https://api.open-home.online/webhooks/amo-chat/:scope_id` |
| Hook API version | `v2` |
| Channel type | Private custom chat channel |
| Account restriction | Restricted to account `33148394` only |

`TO CONFIRM WITH AMO SUPPORT` (if their form requires them):

- Preferred time zone / language for support replies
- Exact icon pixel size / file format beyond SVG
- Whether “write first” / bot-initiated conversations require a separate checkbox
- Any additional OAuth redirect / review questionnaire fields not listed below

---

## Channel 1 — Facebook Marketplace owners

**Technical / channel code:** `OpenHomeFacebook`  
**Display name:** `Open Home | Facebook Marketplace`

### Purpose

Bidirectional owner communication mirror between **Facebook Marketplace owner conversations** and amoCRM for the Open Home operations team.

### Capabilities requested

- Receive incoming owner messages into amoCRM
- Import outgoing Open Home / Agent7 messages into the same amo conversation
- Allow manager replies from amoCRM UI back to the Facebook owner thread
- Write-first / bot-initiated messages **if supported/approved**
- Webhook API v2
- Private channel
- Restricted to amoCRM account `33148394`

### Technical fields

| Field | Value |
|---|---|
| channel code | `OpenHomeFacebook` |
| display name | `Open Home | Facebook Marketplace` |
| client_uuid | `831ec8bc-1565-4c9c-a10d-80b4162dadb6` |
| account_id | `33148394` |
| amojo_id | `41a97e13-a7b6-482c-9312-b7f7724952f0` |
| webhook | `https://api.open-home.online/webhooks/amo-chat/:scope_id` |
| icon | See `deploy/icons/openhome_facebook_channel.svg` (neutral Open Home artwork — not Meta trademark) |

### After approval we need from amoCRM

- `channel_id`
- `channel_secret`
- `bot_id`

(Place into production env; never paste secrets into chat/tickets after receipt.)

---

## Channel 2 — Airbnb Messages

**Technical / channel code:** `OpenHomeAirbnb`  
**Display name:** `Open Home | Airbnb`

### Purpose

Bidirectional owner/host communication mirror between **Airbnb Messages** and amoCRM for the Open Home operations team.

### Capabilities requested

Same as Facebook channel:

- inbound host/owner messages
- import of Open Home outbound messages
- manager replies from amoCRM
- write-first if supported/approved
- Webhook API v2
- private channel
- restricted to account `33148394`

### Technical fields

| Field | Value |
|---|---|
| channel code | `OpenHomeAirbnb` |
| display name | `Open Home | Airbnb` |
| client_uuid | `831ec8bc-1565-4c9c-a10d-80b4162dadb6` |
| account_id | `33148394` |
| amojo_id | `41a97e13-a7b6-482c-9312-b7f7724952f0` |
| webhook | `https://api.open-home.online/webhooks/amo-chat/:scope_id` |
| icon | See `deploy/icons/openhome_airbnb_channel.svg` (neutral Open Home artwork — not Airbnb trademark) |

### After approval we need from amoCRM

- `channel_id`
- `channel_secret`
- `bot_id`

---

## Suggested support email body (copy/paste)

```text
Hello amoCRM Chat API support,

Please register TWO private custom chat channels for our amoCRM account.

Account ID: 33148394
amojo_id: 41a97e13-a7b6-482c-9312-b7f7724952f0
client_uuid: 831ec8bc-1565-4c9c-a10d-80b4162dadb6
Webhook (API v2): https://api.open-home.online/webhooks/amo-chat/:scope_id
Channels must be restricted to this account only.

1) Code: OpenHomeFacebook
   Name: Open Home | Facebook Marketplace
   Purpose: Bidirectional mirror of Facebook Marketplace owner chats into amoCRM.

2) Code: OpenHomeAirbnb
   Name: Open Home | Airbnb
   Purpose: Bidirectional mirror of Airbnb Messages owner/host chats into amoCRM.

Capabilities needed for both:
- inbound messages
- import of our outbound messages
- manager replies from amoCRM
- write first if available
- private channel
- webhook API v2

Please send channel_id, channel_secret, and bot_id for each channel.

Thank you,
Open Home
```

---

## After credentials arrive

1. Put secrets into `/opt/openhome/.env` (server) — do not pass on CLI.
2. `python3 scripts/amo_chat_apply_channel_config.py`
3. `AMO_CHAT_CONNECT_LIVE=true` only for connect step, then:
   - `python3 scripts/amo_chat_setup.py connect-facebook`
   - `python3 scripts/amo_chat_setup.py connect-airbnb`
4. Persist `scope_id` (script does this idempotently).
5. Follow `deploy/AMO_CHAT_DRY_RUN_PLAN.md` before any live sends.
