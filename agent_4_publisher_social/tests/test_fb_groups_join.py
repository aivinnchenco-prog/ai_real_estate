from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_4_publisher" / "scripts"))

import fb_groups_pipeline as fgg  # noqa: E402


class FbGroupsJoinTests(unittest.TestCase):
    def test_join_button_regex_matches_common_labels(self) -> None:
        for label in (
            "Join group",
            "Join",
            "Вступить в группу",
            "Вступить",
            "Приєднатися до групи",
            "Присоединиться к группе",
        ):
            self.assertIsNotNone(fgg.JOIN_BUTTON_RE.match(label), label)

    def test_member_button_regex_does_not_match_join(self) -> None:
        self.assertIsNone(fgg.JOIN_BUTTON_RE.match("В группе"))
        self.assertIsNotNone(fgg.MEMBER_BUTTON_RE.match("В группе"))

    def test_pending_join_detected_in_body(self) -> None:
        page = Mock()
        page.locator.return_value.inner_text.return_value = "Cancel request to join"
        page.get_by_role.return_value.count.return_value = 0
        self.assertTrue(fgg._join_request_pending(page))

    def test_join_dialog_hint_regex(self) -> None:
        self.assertTrue(fgg.JOIN_DIALOG_HINT_RE.search("Group rules and questions"))
        self.assertTrue(fgg.JOIN_DIALOG_HINT_RE.search("Я принимаю правила группы"))
        self.assertFalse(fgg.JOIN_DIALOG_HINT_RE.search("Напишите что-нибудь"))

    def test_join_submit_regex_matches_send_buttons(self) -> None:
        for label in ("Отправить", "Submit", "Send request", "Надіслати", "Готово"):
            self.assertIsNotNone(fgg.JOIN_SUBMIT_RE.search(label), label)

    def test_post_button_regex_matches_send(self) -> None:
        for label in ("Опубликовать", "Отправить", "Post", "Publish", "Надіслати", "Send"):
            self.assertIsNotNone(fgg.POST_BUTTON_RE.search(label), label)

    def test_find_join_button_skips_member_label(self) -> None:
        page = Mock()
        scope = Mock()
        member_btn = Mock()
        member_btn.inner_text.return_value = "В группе"
        join_btn = Mock()
        join_btn.inner_text.return_value = "Join group"
        loc = Mock()
        loc.count.side_effect = [1, 1]
        loc.nth.side_effect = [member_btn, join_btn]
        scope.get_by_role.return_value = loc
        page.locator.return_value = scope
        page.get_by_role.return_value = Mock(count=Mock(return_value=0))
        found = fgg._find_join_button(page)
        self.assertIs(found, join_btn)


if __name__ == "__main__":
    unittest.main()
