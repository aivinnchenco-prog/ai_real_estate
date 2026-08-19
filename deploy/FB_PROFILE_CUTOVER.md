# Facebook profile architecture & staged cutover

Canonical env file on production: `/opt/openhome/.env` (never commit secrets).

## Profile map

| Consumer | Env key | Default path | Lock file |
|----------|---------|--------------|-----------|
| Agent 1 parser + Availability | `FB_BROWSER_PROFILE` | `/opt/openhome/runtime/browser_profiles/facebook_owner_outreach` | `facebook_owner_outreach.lock` |
| Agent 7 Envoy (FB Messenger outreach) | `AGENT7_FACEBOOK_PROFILE_DIR` | `/opt/openhome/runtime/browser_profiles/facebook_agent7` | `facebook_agent7.lock` |
| Agent 9 Connector | `AGENT9_FACEBOOK_PROFILE_DIR` | `/opt/openhome/runtime/browser_profiles/facebook_agent9` | `facebook_agent9.lock` |

Locks: `openhome_shared.facebook_profile_lock` → one flock per physical profile under `OPENHOME_FB_LOCK_DIR` (default `/opt/openhome/runtime/state/shared/locks`).

Bootstrap dirs (no login):

```bash
sudo bash openhome_shared/deploy/ensure_facebook_profile_dirs.sh
```

## Staged cutover (Agent 7 / Agent 9) — zero Agent 6 / Wazzup downtime

Agent 6 Qualifier and Wazzup webhook **do not use** FB browser profiles. They keep running unchanged during FB cutover.

### Phase 0 — prepare (no live traffic)

1. Deploy code with new profile defaults (this refactor).
2. Run `ensure_facebook_profile_dirs.sh` on VPS.
3. Set in `/opt/openhome/.env` (gates OFF first):

```env
AGENT7_FACEBOOK_MESSENGER_ENABLED=false
AGENT7_LIVE_OUTREACH_ENABLED=true   # WA outreach stays on
AGENT9_ENABLED=false                # or stop openhome-agent9.service
AGENT7_FACEBOOK_PROFILE_DIR=/opt/openhome/runtime/browser_profiles/facebook_agent7
AGENT9_FACEBOOK_PROFILE_DIR=/opt/openhome/runtime/browser_profiles/facebook_agent9
OPENHOME_FB_LOCK_DIR=/opt/openhome/runtime/state/shared/locks
```

Keep `FB_BROWSER_PROFILE` on `facebook_owner_outreach` for Agent 1 + Availability.

### Phase 1 — manual login (Agent 7)

1. `AGENT7_FACEBOOK_MESSENGER_ENABLED=false` (still off).
2. VNC/login script against `facebook_agent7` profile only.
3. Smoke: `python3 scripts/agent7_channels_status.py` / `social_auth_status.py` → FB Messenger READY for agent7 profile.
4. Optional dry-run: `agent8_run.py --chat …` without `--send`.

### Phase 2 — enable Agent 7 FB live

1. `AGENT7_FACEBOOK_MESSENGER_ENABLED=true`
2. Restart only services that use Agent 7 FB transport (not Agent 6 userbot / Wazzup).
3. Monitor owner outreach logs; confirm lock files rotate under `OPENHOME_FB_LOCK_DIR`.

### Phase 3 — Agent 9 (parallel track)

1. Keep `openhome-agent9.service` stopped or `browser_enabled=false` in connector config.
2. Manual login into `facebook_agent9`.
3. Smoke mock/offline tests + one manual listing open.
4. Start `openhome-agent9.service` with profile lock (short browser sessions per poll).

### Rollback

- Point `AGENT7_FACEBOOK_PROFILE_DIR` back to previous profile path (e.g. `facebook_owner_outreach`).
- Set `AGENT7_FACEBOOK_MESSENGER_ENABLED=false`.
- Agent 6 / Wazzup unaffected.

## Code paths (canonical)

- Client qualifier: `agent6_qualifier`
- Owner outreach: `agent7_envoy` (+ `agent6_qualifier.messaging.owner_outbound` for WA)
- Legacy imports: `agent7.*` / `agent8.*` are thin shims only (see `deploy/LEGACY_COMPATIBILITY.md`)
