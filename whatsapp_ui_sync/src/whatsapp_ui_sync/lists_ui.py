"""WhatsApp Web list membership UI operations (semantic selectors).

Write capability is limited to ASSIGN / REMOVE list membership.
Never creates lists. Never sends messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .phone import phone_search_variants
from .safety import assert_action_allowed
from .selectors import SELECTORS, exact_list_name_match


@dataclass
class ContactLookup:
    found: bool = False
    ambiguous: bool = False
    match_count: int = 0
    message: str = ""


@dataclass
class ListsState:
    available: list[str] = field(default_factory=list)
    assigned: list[str] = field(default_factory=list)
    ui_confirmed: bool = False
    message: str = ""


class ListsUiPort(Protocol):
    def find_contact_by_phone(self, phone_e164: str) -> ContactLookup: ...

    def open_contact_lists_panel(self) -> bool: ...

    def read_lists_state(self, managed_names: tuple[str, ...]) -> ListsState: ...

    def set_list_membership(
        self,
        list_name: str,
        *,
        assigned: bool,
        dry_run: bool,
    ) -> None: ...

    def save_lists_if_needed(self, *, dry_run: bool) -> None: ...


class PlaywrightListsUi:
    """Live Playwright implementation — fail-safe when UI contract unclear."""

    def __init__(self, page: Any, *, action_delay_ms: int = 2000):
        self.page = page
        self.action_delay_ms = max(0, action_delay_ms)

    def _pause(self, multiplier: float = 1.0) -> None:
        ms = int(self.action_delay_ms * multiplier)
        if ms:
            self.page.wait_for_timeout(ms)

    def find_contact_by_phone(self, phone_e164: str) -> ContactLookup:
        from .phone import normalize_phone_digits

        variants = phone_search_variants(phone_e164)
        target_digits = normalize_phone_digits(phone_e164)
        if not variants or not target_digits:
            return ContactLookup(message="empty phone")

        self._dismiss_lists_migration_modal()
        self._pause(1.0)

        visible_hit = self._click_phone_in_chat_list(target_digits)
        if visible_hit is not None and visible_hit.found:
            return visible_hit

        search = self._find_search_box()
        if search is None:
            return ContactLookup(
                message="search box not found — UI contract unconfirmed",
            )

        ordered: list[str] = []
        for preferred in (f"+{target_digits}", target_digits):
            if preferred not in ordered:
                ordered.append(preferred)
        for q in variants:
            if q not in ordered:
                ordered.append(q)

        last = ContactLookup(message="not found")
        for query in ordered:
            try:
                search.click()
                self._pause(0.5)
                search.fill("")
                search.fill(query)
                self._pause(2.5)
                hit = self._click_single_chat_search_result()
                if hit is not None and (hit.found or hit.ambiguous):
                    return hit
                hit = self._click_phone_in_chat_list(target_digits)
                if hit is not None and hit.found:
                    return hit
                last = ContactLookup(message=f"no chat result for {query}")
            except Exception as exc:
                last = ContactLookup(message=f"search error: {exc}")
        return last

    def _dismiss_lists_migration_modal(self) -> None:
        for name in SELECTORS.continue_labels:
            try:
                btn = self.page.get_by_role("button", name=name, exact=True)
                if btn.count() > 0:
                    btn.first.click()
                    self._pause(1.5)
                    return
                txt = self.page.get_by_text(name, exact=True)
                if txt.count() > 0:
                    txt.first.click()
                    self._pause(1.5)
                    return
            except Exception:
                continue

    def _find_search_box(self) -> Any | None:
        for role in ("searchbox", "textbox"):
            try:
                loc = self.page.get_by_role(role)
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        for substr in SELECTORS.main_search_placeholder_substrings:
            try:
                loc = self.page.get_by_placeholder(substr)
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return None

    def _click_single_chat_search_result(self) -> ContactLookup | None:
        """After phone search: click the only chat under Chats/Бесіди."""
        self._pause(1.0)
        viewport = self.page.viewport_size or {"width": 1400, "height": 900}
        left_max_x = float(viewport["width"]) * 0.55

        rows: list[tuple[float, Any]] = []
        try:
            items = self.page.locator('[role="listitem"], [role="row"]')
            total = min(items.count(), 40)
            for i in range(total):
                node = items.nth(i)
                try:
                    if not node.is_visible():
                        continue
                    box = node.bounding_box()
                    text = (node.inner_text(timeout=300) or "").strip()
                except Exception:
                    continue
                if not box or box["x"] > left_max_x:
                    continue
                if not text or len(text) < 2:
                    continue
                first_line = text.split("\n", 1)[0].strip()
                if first_line in {
                    "Чаты", "Chats", "Бесіди", "Контакты", "Контакти",
                    "Contacts", "Сообщения", "Messages",
                }:
                    continue
                # Skip "Contacts" section self-row if clearly the business line
                if box["height"] < 36:
                    continue
                rows.append((box["y"], node))
        except Exception:
            rows = []

        unique: list[tuple[float, Any]] = []
        for y, node in sorted(rows, key=lambda t: t[0]):
            if any(abs(y - uy) <= 10 for uy, _ in unique):
                continue
            unique.append((y, node))

        if len(unique) == 1:
            try:
                unique[0][1].click(timeout=10_000)
            except Exception:
                unique[0][1].click(force=True, timeout=10_000)
            self._pause(2.0)
            return ContactLookup(
                found=True,
                match_count=1,
                message="clicked single Chats search result after phone query",
            )
        if len(unique) > 1:
            # Prefer topmost chat (first under Бесіди) when phone query was exact
            try:
                unique[0][1].click(timeout=10_000)
                self._pause(2.0)
                return ContactLookup(
                    found=True,
                    match_count=1,
                    message="clicked topmost chat after phone search",
                )
            except Exception:
                return ContactLookup(
                    found=False,
                    ambiguous=True,
                    match_count=len(unique),
                    message="ambiguous chat search results after phone query",
                )
        return None

    def _click_phone_in_chat_list(self, target_digits: str) -> ContactLookup | None:
        """Click topmost chat-list row whose title digits match the phone.

        Nested duplicate nodes for the same chat are NOT treated as ambiguous —
        we pick the topmost visible label in the left pane (first in list).
        """
        from .phone import digits_only, normalize_phone_digits, phone_search_variants

        e164 = f"+{target_digits}"
        viewport = self.page.viewport_size or {"width": 1280, "height": 900}
        left_max_x = float(viewport["width"]) * 0.55

        candidates: list[tuple[float, float, Any]] = []  # (y, area, node)

        for query in phone_search_variants(e164):
            try:
                exact = self.page.get_by_text(query, exact=True)
                count = exact.count()
                for i in range(min(count, 30)):
                    node = exact.nth(i)
                    try:
                        if not node.is_visible():
                            continue
                        box = node.bounding_box()
                    except Exception:
                        continue
                    if not box or box["width"] <= 0 or box["height"] <= 0:
                        continue
                    # Prefer left chat list, ignore huge wrappers
                    if box["x"] > left_max_x:
                        continue
                    if box["width"] * box["height"] > 80_000:
                        continue
                    candidates.append((box["y"], box["width"] * box["height"], node))
            except Exception:
                continue

        if not candidates:
            # Fallback: scan short single-line labels by digit equality
            try:
                nodes = self.page.locator("span, div")
                total = min(nodes.count(), 300)
                for i in range(total):
                    node = nodes.nth(i)
                    try:
                        if not node.is_visible():
                            continue
                        text = (node.inner_text(timeout=100) or "").strip()
                    except Exception:
                        continue
                    if not text or "\n" in text or len(text) > 32:
                        continue
                    norm = normalize_phone_digits(text) or digits_only(text)
                    if norm != target_digits:
                        continue
                    compact = text.replace(" ", "").replace("-", "").replace("+", "")
                    if not compact.isdigit():
                        continue
                    box = node.bounding_box()
                    if not box or box["x"] > left_max_x:
                        continue
                    area = box["width"] * box["height"]
                    if area <= 0 or area > 80_000:
                        continue
                    candidates.append((box["y"], area, node))
            except Exception:
                pass

        if not candidates:
            return None

        # Topmost row in chat list (= first visible). Among same Y, smallest label.
        candidates.sort(key=lambda t: (round(t[0] / 4) * 4, t[1]))
        top_y = candidates[0][0]
        same_row = [c for c in candidates if abs(c[0] - top_y) <= 12]
        same_row.sort(key=lambda t: t[1])
        target = same_row[0][2]

        try:
            target.click(timeout=10_000)
        except Exception:
            try:
                target.click(force=True, timeout=10_000)
            except Exception as exc:
                return ContactLookup(
                    found=False,
                    message=f"visible phone found but click failed: {exc}",
                )
        self._pause(2.0)
        return ContactLookup(
            found=True,
            match_count=1,
            message="clicked topmost chat-list phone match",
        )

    def open_contact_lists_panel(self) -> bool:
        """Open native list picker: chat menu → Add to list → Вибрати список."""
        self._dismiss_lists_migration_modal()
        self._pause(1.0)

        if self._list_picker_visible():
            return True

        if not self._open_chat_overflow_menu():
            return False

        clicked = False
        for name in SELECTORS.add_to_list_menu:
            try:
                mi = self.page.get_by_role("menuitem", name=name)
                if mi.count() > 0:
                    mi.first.click()
                    clicked = True
                    break
                txt = self.page.get_by_text(name, exact=True)
                if txt.count() > 0:
                    txt.first.click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return False

        self._pause(2.0)
        self._dismiss_lists_migration_modal()
        self._pause(1.0)
        return self._list_picker_visible()

    def _list_picker_visible(self) -> bool:
        for title in SELECTORS.select_list_titles:
            try:
                if self.page.get_by_text(title, exact=True).count() > 0:
                    return True
            except Exception:
                continue
        try:
            if (
                self.page.get_by_text("Owner", exact=True).count() > 0
                and self.page.get_by_text("Client", exact=True).count() > 0
                and self.page.get_by_role("checkbox").count() > 0
            ):
                return True
        except Exception:
            pass
        return False

    def _open_chat_overflow_menu(self) -> bool:
        try:
            menus = self.page.get_by_role("button", name="Меню")
            if menus.count() == 0:
                menus = self.page.get_by_role("button", name="Menu")
            best = None
            best_x = -1.0
            for i in range(menus.count()):
                box = menus.nth(i).bounding_box()
                if box and box["x"] > best_x:
                    best_x = box["x"]
                    best = menus.nth(i)
            if best is None:
                return False
            best.click()
            self._pause(1.5)
            return True
        except Exception:
            return False

    def _checkbox_for_list(self, list_name: str) -> Any | None:
        """Find checkbox on the SAME ROW as exact list title (Owner/Client).

        Never return the first checkbox in a shared ancestor — that wrongly
        hits «Избранное» / Favorites above Client.
        """
        # Never treat favorites / recommendations as managed lists
        banned = {
            "избранное",
            "обране",
            "favorites",
            "favourite",
            "новий клієнт",
            "новый клиент",
            "new client",
        }
        if list_name.strip().lower() in banned:
            return None

        for role in ("checkbox", "switch"):
            try:
                loc = self.page.get_by_role(role, name=list_name, exact=True)
                if loc.count() == 1:
                    return loc.first
            except Exception:
                continue

        label_box = None
        try:
            label = self.page.get_by_text(list_name, exact=True)
            for i in range(min(label.count(), 8)):
                node = label.nth(i)
                try:
                    t = (node.inner_text() or "").strip()
                except Exception:
                    continue
                if not exact_list_name_match(t, list_name):
                    continue
                box = node.bounding_box()
                if not box or box["width"] <= 0 or box["height"] <= 0:
                    continue
                # Skip huge wrappers
                if box["width"] * box["height"] > 30_000:
                    continue
                label_box = box
                break
        except Exception:
            return None
        if label_box is None:
            return None

        label_cy = label_box["y"] + label_box["height"] / 2.0
        best = None
        best_score: float | None = None
        try:
            cbs = self.page.get_by_role("checkbox")
            if cbs.count() == 0:
                cbs = self.page.get_by_role("switch")
            for i in range(min(cbs.count(), 30)):
                cb = cbs.nth(i)
                try:
                    if not cb.is_visible():
                        continue
                    cbox = cb.bounding_box()
                except Exception:
                    continue
                if not cbox:
                    continue
                cb_cy = cbox["y"] + cbox["height"] / 2.0
                # Must be same visual row
                if abs(cb_cy - label_cy) > 14:
                    continue
                # Prefer checkbox to the right of the label (WA layout)
                dx = cbox["x"] - label_box["x"]
                score = abs(cb_cy - label_cy) * 10 + (0 if dx >= -20 else 1000) + abs(dx) * 0.01
                if best_score is None or score < best_score:
                    best_score = score
                    best = cb
        except Exception:
            return None
        return best

    def read_lists_state(self, managed_names: tuple[str, ...]) -> ListsState:
        available: list[str] = []
        assigned: list[str] = []
        for name in managed_names:
            cb = self._checkbox_for_list(name)
            if cb is None:
                try:
                    if self.page.get_by_text(name, exact=True).count() > 0:
                        available.append(name)
                except Exception:
                    pass
                continue
            available.append(name)
            try:
                is_on = bool(cb.is_checked())
            except Exception:
                is_on = cb.get_attribute("aria-checked") == "true"
            if is_on:
                assigned.append(name)

        if not available:
            return ListsState(
                ui_confirmed=False,
                message="managed lists not visible — UI contract unconfirmed or lists missing",
            )
        return ListsState(
            available=available,
            assigned=assigned,
            ui_confirmed=True,
            message="ok",
        )

    def set_list_membership(
        self,
        list_name: str,
        *,
        assigned: bool,
        dry_run: bool,
    ) -> None:
        for forbidden in SELECTORS.create_list_forbidden:
            if list_name.strip() == forbidden.strip() or list_name.startswith("+"):
                raise PermissionError(f"refusing create-list action: {list_name}")

        action = "assign_list" if assigned else "remove_list"
        assert_action_allowed(action)
        if dry_run:
            return
        cb = self._checkbox_for_list(list_name)
        if cb is None:
            raise RuntimeError(
                f"cannot toggle list {list_name!r} — checkbox not uniquely confirmed"
            )
        try:
            currently = bool(cb.is_checked())
        except Exception:
            currently = cb.get_attribute("aria-checked") == "true"
        if currently == assigned:
            return
        cb.click()
        self._pause(1.0)
        # Verify we toggled the intended row, not Favorites
        try:
            now = bool(cb.is_checked())
        except Exception:
            now = cb.get_attribute("aria-checked") == "true"
        if now != assigned:
            raise RuntimeError(
                f"toggle verify failed for {list_name!r}: expected assigned={assigned}"
            )

    def save_lists_if_needed(self, *, dry_run: bool) -> None:
        if dry_run:
            return
        for name in SELECTORS.done_labels:
            try:
                btn = self.page.get_by_role("button", name=name, exact=True)
                if btn.count() >= 1 and btn.first.is_enabled():
                    btn.first.click()
                    self._pause(1.5)
                    return
            except Exception:
                continue


@dataclass
class FakeListsUi:
    """In-memory UI for offline tests."""

    contacts: dict[str, set[str]] = field(default_factory=dict)
    available_lists: set[str] = field(default_factory=lambda: {"Owner", "Client"})
    clicks: list[tuple[str, bool]] = field(default_factory=list)
    save_clicks: int = 0
    ambiguous_phones: set[str] = field(default_factory=set)
    fail_open_panel: bool = False

    def find_contact_by_phone(self, phone_e164: str) -> ContactLookup:
        if phone_e164 in self.ambiguous_phones:
            return ContactLookup(ambiguous=True, match_count=2, message="ambiguous")
        if phone_e164 in self.contacts:
            return ContactLookup(found=True, match_count=1)
        return ContactLookup(found=False, message="missing")

    def open_contact_lists_panel(self) -> bool:
        return not self.fail_open_panel

    def read_lists_state(self, managed_names: tuple[str, ...]) -> ListsState:
        available = [n for n in managed_names if n in self.available_lists]
        if len(available) < len([n for n in managed_names if n]):
            # missing managed list
            missing = [n for n in managed_names if n not in self.available_lists]
            if missing:
                return ListsState(
                    available=available,
                    assigned=[],
                    ui_confirmed=True,
                    message=f"missing:{','.join(missing)}",
                )
        # assigned tracked per last opened phone via _current
        assigned = list(getattr(self, "_current_assigned", set()))
        return ListsState(
            available=list(self.available_lists),
            assigned=assigned,
            ui_confirmed=True,
        )

    def bind_phone(self, phone: str) -> None:
        self._current_assigned = set(self.contacts.get(phone, set()))

    def set_list_membership(
        self,
        list_name: str,
        *,
        assigned: bool,
        dry_run: bool,
    ) -> None:
        assert_action_allowed("assign_list" if assigned else "remove_list")
        self.clicks.append((list_name, assigned))
        if dry_run:
            return
        cur = set(getattr(self, "_current_assigned", set()))
        if assigned:
            cur.add(list_name)
        else:
            cur.discard(list_name)
        self._current_assigned = cur

    def save_lists_if_needed(self, *, dry_run: bool) -> None:
        if dry_run:
            return
        self.save_clicks += 1
        phone = getattr(self, "_bound_phone", None)
        if phone:
            self.contacts[phone] = set(getattr(self, "_current_assigned", set()))
