# Legacy package compatibility (Agent 6–8 refactor)

Production canonical imports:

| Role | Package |
|------|---------|
| Qualifier (client dialog) | `agent6_qualifier` |
| Envoy (owner outreach) | `agent7_envoy` |
| Notary (booking docs) | `agent8_notary` |

## Legacy shims (do not add logic here)

### `agent7/*`

Thin re-exports from `agent6_qualifier` (and `agent7.profile_lock` for FB lock shim).

Launch: `python -m agent7.tg_userbot` → same runtime as `agent6_qualifier.tg_userbot`.

### `agent8/*` and `agent8/envoy/*` and `agent8/notary/*`

Thin re-exports from `agent7_envoy` / `agent8_notary`.

### `agent8/auto.py` chain

`agent8.auto` → `agent8.envoy.auto` → `agent7_envoy.auto` (same objects).

## Deploy safety

- `install_api_server.sh` rsyncs `agent_6_qualifier/src/` including shims.
- **Never** maintain duplicate implementations in `agent7/` or `agent8/` — only `from canonical import …`.
- systemd units use `agent6_qualifier` module paths for Agent 6 and Wazzup.

## Deprecated on VPS (do not delete yet)

- Full-copy `agent7/qualifier.py` on old snapshots — replaced by shims in this refactor.
- `agent_8/fb_messenger.py` legacy Playwright path — production FB uses `agent7_envoy.messaging.facebook_messenger`.
