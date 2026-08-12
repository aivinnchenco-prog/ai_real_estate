"""WhatsAppNativeListSyncWorker — secondary visual list sync only."""

from __future__ import annotations

from typing import Any

from .browser_session import BrowserSessionStatus, WhatsAppBrowserSession
from .config import WhatsAppUiSyncConfig
from .lists_ui import FakeListsUi, ListsUiPort, PlaywrightListsUi
from .phone import normalize_phone_e164
from .results import SyncCode, SyncResult
from .roles import CanonicalRole, map_role_to_list, plan_list_membership
from .safety import expose_public_api


class WhatsAppNativeListSyncWorker:
    """Assign/remove WhatsApp Business native lists Owner / Client.

    Does not send messages, does not create lists, does not touch Wazzup.
    """

    def __init__(
        self,
        config: WhatsAppUiSyncConfig | None = None,
        *,
        session: WhatsAppBrowserSession | None = None,
        lists_ui: ListsUiPort | None = None,
    ):
        self.config = config or WhatsAppUiSyncConfig.from_env()
        self.session = session or WhatsAppBrowserSession(self.config)
        self._lists_ui = lists_ui
        self._owns_session = session is None and lists_ui is None

    def browser_session_status(self) -> str:
        return self.session.browser_session_status().value

    def healthcheck(self) -> dict[str, Any]:
        started_here = False
        try:
            if self.session.page is None and self._lists_ui is None:
                self.session.start(headless=self.config.headless)
                self.session.open_whatsapp()
                started_here = True
            if self._lists_ui is not None and self.session.page is None:
                # Offline / injected UI — report synthetic READY for unit tests
                return {
                    "browser": "CONNECTED",
                    "whatsapp_web": "READY",
                    "auth": "OK",
                    "message": "injected lists UI",
                }
            probe = self.session.probe_auth()
            browser = (
                "CONNECTED"
                if probe.status
                in {
                    BrowserSessionStatus.READY,
                    BrowserSessionStatus.AUTH_REQUIRED,
                    BrowserSessionStatus.LINKED_DEVICE_CONFLICT,
                }
                else ("FAILED" if probe.status == BrowserSessionStatus.FAILED else "DISCONNECTED")
            )
            wa = "READY" if probe.status == BrowserSessionStatus.READY else "FAILED"
            if probe.status == BrowserSessionStatus.AUTH_REQUIRED:
                wa = "AUTH_REQUIRED"
            if probe.status == BrowserSessionStatus.LINKED_DEVICE_CONFLICT:
                wa = "LINKED_DEVICE_CONFLICT"
            return {
                "browser": browser,
                "whatsapp_web": wa,
                "auth": probe.status.value,
                "message": probe.message,
                "url": probe.url,
                "profile_dir": str(self.config.profile_dir),
            }
        except Exception as exc:
            return {
                "browser": "FAILED",
                "whatsapp_web": "FAILED",
                "auth": BrowserSessionStatus.FAILED.value,
                "message": str(exc),
                "profile_dir": str(self.config.profile_dir),
            }
        finally:
            if started_here and self._owns_session:
                # leave session open only if caller keeps worker; for healthcheck close
                pass

    def _managed_lists(self) -> tuple[str, ...]:
        names = [
            self.config.client_list_name,
            self.config.owner_list_name,
            self.config.agent_list_name,
        ]
        # unique preserve order
        out: list[str] = []
        for n in names:
            if n not in out:
                out.append(n)
        return tuple(out)

    def _resolve_ui(self) -> ListsUiPort | SyncResult:
        if self._lists_ui is not None:
            return self._lists_ui
        if self.session.page is None:
            try:
                self.session.start(headless=self.config.headless)
                self.session.open_whatsapp()
            except Exception as exc:
                return SyncResult(
                    code=SyncCode.BROWSER_UNAVAILABLE,
                    message=str(exc),
                    dry_run=True,
                )
        probe = self.session.probe_auth()
        if probe.status == BrowserSessionStatus.AUTH_REQUIRED:
            return SyncResult(
                code=SyncCode.WHATSAPP_UI_AUTH_REQUIRED,
                message=probe.message,
                dry_run=self.config.dry_run,
            )
        if probe.status == BrowserSessionStatus.LINKED_DEVICE_CONFLICT:
            return SyncResult(
                code=SyncCode.LINKED_DEVICE_CONFLICT,
                message=probe.message,
                dry_run=self.config.dry_run,
            )
        if probe.status != BrowserSessionStatus.READY:
            return SyncResult(
                code=SyncCode.UI_CONTRACT_UNCONFIRMED,
                message=probe.message or "WhatsApp Web not READY",
                dry_run=self.config.dry_run,
            )
        return PlaywrightListsUi(
            self.session.page,
            action_delay_ms=self.config.action_delay_ms,
        )

    def sync_contact_role(
        self,
        phone: str,
        role: CanonicalRole | str,
        *,
        confirm_write: bool = False,
        dry_run: bool | None = None,
    ) -> SyncResult:
        phone_n = normalize_phone_e164(phone)
        canonical = CanonicalRole.parse(role)
        use_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        want_write = bool(confirm_write) and not use_dry
        writes = want_write and self.config.enabled and not self.config.dry_run
        # If caller forces dry_run=False but config.dry_run still true, block writes
        if want_write and self.config.dry_run and dry_run is False:
            # CLI rebuilt config.dry_run=False — allow gated by enabled+confirm only
            writes = want_write and self.config.enabled

        base = SyncResult(
            phone=phone_n or str(phone or ""),
            canonical_role=canonical.value,
            dry_run=not writes,
        )

        if not phone_n:
            base.code = SyncCode.FAILED
            base.message = "invalid phone"
            return base

        if canonical is CanonicalRole.UNKNOWN:
            base.code = SyncCode.SKIPPED_UNKNOWN
            base.message = "UNKNOWN role — no WhatsApp list action"
            return base

        target = map_role_to_list(
            canonical,
            client_list=self.config.client_list_name,
            owner_list=self.config.owner_list_name,
            agent_list=self.config.agent_list_name,
        )
        base.target_list = target

        if want_write and not self.config.enabled:
            base.code = SyncCode.WRITE_BLOCKED
            base.message = "WHATSAPP_UI_SYNC_ENABLED=false — writes blocked"
            return base

        ui_or_err = self._resolve_ui()
        if isinstance(ui_or_err, SyncResult):
            ui_or_err.phone = phone_n
            ui_or_err.canonical_role = canonical.value
            ui_or_err.target_list = target
            return ui_or_err
        ui = ui_or_err

        if isinstance(ui, FakeListsUi):
            ui.bind_phone(phone_n)
            ui._bound_phone = phone_n  # noqa: SLF001 — test helper

        lookup = ui.find_contact_by_phone(phone_n)
        if lookup.ambiguous:
            base.code = SyncCode.CONTACT_AMBIGUOUS
            base.message = lookup.message
            return base
        if not lookup.found:
            base.code = SyncCode.CONTACT_NOT_FOUND
            base.message = lookup.message or "contact not found by phone"
            return base

        if not ui.open_contact_lists_panel():
            base.code = SyncCode.UI_CONTRACT_UNCONFIRMED
            base.message = "contact lists panel not opened"
            return base

        managed = self._managed_lists()
        state = ui.read_lists_state(managed)
        if not state.ui_confirmed:
            base.code = SyncCode.UI_CONTRACT_UNCONFIRMED
            base.message = state.message
            return base

        # Exact list presence — never auto-create
        for required in managed:
            if required not in state.available:
                base.code = SyncCode.WHATSAPP_LIST_NOT_FOUND
                base.message = f"list not found: {required}"
                base.current_lists = list(state.assigned)
                base.details["available"] = list(state.available)
                return base

        plan = plan_list_membership(
            canonical,
            current_lists=state.assigned,
            client_list=self.config.client_list_name,
            owner_list=self.config.owner_list_name,
            agent_list=self.config.agent_list_name,
        )
        base.current_lists = list(state.assigned)
        base.would_add = list(plan.must_assign)
        base.would_remove = list(plan.must_remove)
        base.target_list = plan.target_list

        if plan.no_op:
            base.code = SyncCode.ALREADY_SYNCED
            base.message = "already in target list; no conflicting lists"
            return base

        if not writes:
            if want_write and not confirm_write:
                base.code = SyncCode.WRITE_BLOCKED
                base.message = "pass --confirm-write to apply"
            elif want_write and self.config.dry_run:
                base.code = SyncCode.WRITE_BLOCKED
                base.message = "WHATSAPP_UI_DRY_RUN=true — writes blocked"
            else:
                base.code = SyncCode.DRY_RUN_PLAN
                base.message = "dry-run: no checkbox/Save clicks"
            # Plan path: never mutate
            for name in plan.must_assign:
                ui.set_list_membership(name, assigned=True, dry_run=True)
            for name in plan.must_remove:
                ui.set_list_membership(name, assigned=False, dry_run=True)
            ui.save_lists_if_needed(dry_run=True)
            return base

        try:
            for name in plan.must_remove:
                ui.set_list_membership(name, assigned=False, dry_run=False)
            for name in plan.must_assign:
                ui.set_list_membership(name, assigned=True, dry_run=False)
            ui.save_lists_if_needed(dry_run=False)
        except PermissionError as exc:
            base.code = SyncCode.WRITE_BLOCKED
            base.message = str(exc)
            return base
        except Exception as exc:
            msg = str(exc).lower()
            if "timeout" in msg:
                base.code = SyncCode.RETRYABLE_UI_TIMEOUT
            else:
                base.code = SyncCode.FAILED
            base.message = str(exc)
            return base

        base.code = SyncCode.SYNCED
        base.message = "list membership updated"
        base.dry_run = False
        return base

    def assign_to_list(self, phone: str, list_name: str, *, confirm_write: bool = False) -> SyncResult:
        """Assign phone to an existing list (Client / Owner only via config names)."""
        managed = set(self._managed_lists())
        if list_name not in managed:
            return SyncResult(
                code=SyncCode.WHATSAPP_LIST_NOT_FOUND,
                phone=normalize_phone_e164(phone) or phone,
                message=f"refusing non-managed or unknown list: {list_name}",
                dry_run=True,
            )
        # Map list back to a role for conflict resolution
        if list_name == self.config.client_list_name:
            role = CanonicalRole.CLIENT
        else:
            role = CanonicalRole.OWNER
        return self.sync_contact_role(phone, role, confirm_write=confirm_write)

    def remove_from_conflicting_list(
        self,
        phone: str,
        role: CanonicalRole | str,
        *,
        confirm_write: bool = False,
    ) -> SyncResult:
        """Ensure conflicting managed list is removed as part of role sync."""
        return self.sync_contact_role(phone, role, confirm_write=confirm_write)

    def close(self) -> None:
        if self._owns_session:
            self.session.stop()

    @staticmethod
    def capability_surface() -> dict[str, tuple[str, ...]]:
        return expose_public_api()
