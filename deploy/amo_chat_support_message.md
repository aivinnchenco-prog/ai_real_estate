# amoCRM Chat API — support request

Ready to send after `https://api.open-home.online/health` returns HTTP 200.

Do not attach tokens, channel secrets, API keys, cookies, or passwords.

---

```text
Hello amoCRM Chat API support,

Please register two private custom chat channels for our amoCRM account.
We cannot complete channel creation via the public API; we need your
manual registration so we can then call Chat API connect ourselves.

Account ID: 33148394
amojo_id: 41a97e13-a7b6-482c-9312-b7f7724952f0
client_uuid: 831ec8bc-1565-4c9c-a10d-80b4162dadb6
Webhook (Chat API v2): https://api.open-home.online/webhooks/amo-chat/:scope_id

Please restrict both channels to this amoCRM account only.

------------------------------------------------
Channel 1 — Facebook Marketplace owners
------------------------------------------------
Technical code: OpenHomeFacebook
Display name: Open Home | Facebook Marketplace
Purpose: Bidirectional synchronization/mirroring of owner conversations
between Facebook Marketplace and amoCRM.

------------------------------------------------
Channel 2 — Airbnb host/owner messages
------------------------------------------------
Technical code: OpenHomeAirbnb
Display name: Open Home | Airbnb
Purpose: Bidirectional synchronization/mirroring of Airbnb host/owner
conversations and amoCRM.

------------------------------------------------
Capabilities required for both channels
------------------------------------------------
- incoming messages into amoCRM
- import of our outgoing messages into the same amo conversation
- manager replies from amoCRM back to the original Facebook/Airbnb thread
- write-first / bot-initiated conversations, if this is supported
- webhook API v2
- private custom chat channel
- allowed only for account 33148394

Icons: we will attach two original Open Home SVG icons (128×128 viewBox).
They are not official Facebook or Airbnb logos.

TO CONFIRM WITH AMO SUPPORT:
- exact required icon size / format if SVG 128×128 is not accepted
- whether write-first needs a separate checkbox/approval
- any additional OAuth redirect or review questionnaire fields

Please send for each channel:
- channel_id
- channel_secret
- bot_id

Thank you,
Open Home
```
