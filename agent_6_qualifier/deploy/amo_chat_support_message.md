# amoCRM Chat API — support request (Facebook only)

Ready to send after `https://api.open-home.online/health` returns HTTP 200.

Do not attach tokens, channel secrets, API keys, cookies, or passwords.

---

```text
Hello amoCRM Chat API support,

Please register ONE private custom chat channel for our amoCRM account.
We cannot complete channel creation via the public API; we need your
manual registration so we can then call Chat API connect ourselves.

Account ID: 33148394
amojo_id: 41a97e13-a7b6-482c-9312-b7f7724952f0
client_uuid: 831ec8bc-1565-4c9c-a10d-80b4162dadb6
Webhook (Chat API v2): https://api.open-home.online/webhooks/amo-chat/:scope_id

Please restrict the channel to this amoCRM account only.

------------------------------------------------
Channel — Facebook Marketplace owners
------------------------------------------------
Technical code: OpenHomeFacebook
Display name: Open Home | Facebook Marketplace
Purpose: Bidirectional synchronization/mirroring of owner conversations
between Facebook Marketplace and amoCRM.

------------------------------------------------
Capabilities required

- Receive incoming owner messages into amoCRM
- Import outgoing Open Home / Agent7 messages into the same amo conversation
- Allow manager replies from amoCRM UI back to the Facebook owner thread
- Write-first / bot-initiated messages if supported/approved
- Webhook API v2
- Private channel
- Restricted to amoCRM account 33148394

After approval please provide:
- channel_id
- channel_secret
- bot_id

(Place into production env; never paste secrets into tickets after receipt.)

Icon: deploy/icons/openhome_facebook_channel.svg
```

**Note:** Airbnb custom chat is intentionally not requested (Facebook-only integration).
