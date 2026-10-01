#!/usr/bin/env python3
"""Share-link resolution and HTML sanitizing for the FB Marketplace parser."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_FB_ROOT = Path(__file__).resolve().parents[1]
if str(_FB_ROOT) not in sys.path:
    sys.path.insert(0, str(_FB_ROOT))

from agent1b.fb_page import (
    assert_crawled_item_page,
    attach_html_sanitizer,
    canonical_from_share_meta,
    exit_code_for_parser_error,
    html_has_listing_card,
    item_id_from_navigation_url,
    navigation_kind,
    resolve_share_target,
    sanitize_html,
    session_expired_message,
    share_wait_failed,
)

SHARE = "https://www.facebook.com/share/1Zwk7P4T5r/"
ITEM = "https://www.facebook.com/marketplace/item/1515735863510647/"
FEED = "https://www.facebook.com/marketplace/"
LOGIN = (
    "https://www.facebook.com/login.php?next="
    "https%3A%2F%2Fwww.facebook.com%2Fmarketplace%2Fitem%2F1515735863510647"
)
FEED_HTML = """
<html><body>
<a href="/marketplace/item/111/">villa</a>
<a href="/marketplace/item/222/">condo</a>
top picks
today's picks
</body></html>
"""
CARD_HTML = """
<html><head>
<meta property="og:title" content="3 bed villa" />
<meta property="og:url" content="https://www.facebook.com/marketplace/item/1515735863510647/?ref=share" />
</head><body>
<script>{"marketplace_listing_title":"Pool villa","redacted_description":{"text":"Long stay"}}</script>
</body></html>
"""


class SanitizeHtmlTest(unittest.TestCase):
    def test_strips_null_and_controls_keeps_text(self):
        raw = "วิลล่า\x00\x01\x08\x0b\x0c\x0e\x1f\x7f\n\t🏊 ok"
        cleaned = sanitize_html(raw)
        self.assertNotIn("\x00", cleaned)
        self.assertNotIn("\x0b", cleaned)
        self.assertNotIn("\x7f", cleaned)
        self.assertIn("วิลล่า", cleaned)
        self.assertIn("🏊", cleaned)
        self.assertIn("\n", cleaned)
        self.assertIn("\t", cleaned)
        self.assertIn("ok", cleaned)

    def test_strips_illegal_numeric_character_references(self):
        cleaned = sanitize_html("a&#0;b&#x0;c&#9;d&#32;e&#x41;")
        self.assertNotIn("&#0;", cleaned)
        self.assertNotIn("&#x0;", cleaned)
        self.assertIn("&#9;", cleaned)
        self.assertIn("&#32;", cleaned)
        self.assertIn("&#x41;", cleaned)
        self.assertTrue(cleaned.startswith("abc"))

    def test_bytes_input(self):
        self.assertEqual(sanitize_html(b"a\x00b"), "ab")

    def test_sanitized_strategy_passes_clean_html(self):
        class Base:
            def scrap(self, url, html, **kwargs):
                self.seen = html
                return html

        strategy = attach_html_sanitizer(Base)()
        out = strategy.scrap("https://facebook.com/share/x/", "pre\x00post")
        self.assertEqual(strategy.seen, "prepost")
        self.assertEqual(out, "prepost")

    def test_lxml_rejects_control_chars_until_html_is_sanitized(self):
        try:
            import lxml.html as lhtml
        except ImportError:
            self.skipTest("lxml is not installed")
        # crawl4ai copies parsed attribute values onto new elements. lxml raises
        # the exact error from the production log when a control character survived.
        dirty = "<html><body><p>วิลล่า&#1;ok</p><img alt='a&#1;b' src='http://x'/></body></html>"
        raw_doc = lhtml.document_fromstring(dirty)
        raw_img = raw_doc.xpath("//img")[0]
        with self.assertRaises(ValueError) as caught:
            copied = lhtml.Element("img")
            copied.set("alt", raw_img.get("alt") or "")
        self.assertIn("XML compatible", str(caught.exception))
        self.assertIn("control characters", str(caught.exception))

        doc = lhtml.document_fromstring(sanitize_html(dirty))
        paragraph = lhtml.Element("p")
        paragraph.text = doc.text_content()
        self.assertIn("วิลล่า", paragraph.text)
        self.assertIn("ok", paragraph.text)
        self.assertNotIn("\x01", paragraph.text)
        img = doc.xpath("//img")[0]
        neu = lhtml.Element("img")
        neu.set("alt", img.get("alt") or "")
        self.assertEqual(neu.get("alt"), "ab")


class ShareResolveTest(unittest.TestCase):
    def test_item_id_ignores_login_query(self):
        self.assertIsNone(item_id_from_navigation_url(LOGIN))
        self.assertEqual(navigation_kind(LOGIN), "login")
        self.assertEqual(item_id_from_navigation_url(ITEM), "1515735863510647")
        self.assertEqual(navigation_kind(FEED), "feed")
        self.assertEqual(navigation_kind(SHARE), "share")

    def test_share_redirect_to_item_is_canonical(self):
        resolved = resolve_share_target(
            "https://m.facebook.com/marketplace/item/1515735863510647/?ref=share",
            CARD_HTML,
        )
        self.assertEqual(resolved, ITEM)

    def test_feed_html_item_links_are_not_the_share_target(self):
        with self.assertRaises(RuntimeError) as caught:
            resolve_share_target(FEED, FEED_HTML)
        message = str(caught.exception)
        self.assertTrue(message.startswith("AUTH_REQUIRED:"))
        self.assertIn("Marketplace feed", message)
        self.assertNotIn("/marketplace/item/111", message)
        self.assertNotIn("/marketplace/item/222", message)
        self.assertIn("import_fb_state.py", message)
        self.assertIn("git pull", message)
        self.assertIn("openhome-agent1", message)
        self.assertEqual(exit_code_for_parser_error(message), 2)

    def test_staying_on_share_url_without_meta_is_session_expired(self):
        with self.assertRaises(RuntimeError) as caught:
            resolve_share_target(SHARE, FEED_HTML)
        self.assertIn("Marketplace feed", str(caught.exception))
        self.assertEqual(exit_code_for_parser_error(str(caught.exception)), 2)

    def test_og_url_on_share_page_resolves_before_scrape(self):
        html = (
            '<html><head><meta property="og:url" '
            'content="https:\\/\\/www.facebook.com\\/marketplace\\/item\\/1515735863510647\\/" />'
            "</head><body>share</body></html>"
        )
        self.assertEqual(
            canonical_from_share_meta(html),
            ITEM,
        )
        self.assertEqual(resolve_share_target(SHARE, html), ITEM)

    def test_feed_page_og_url_is_not_trusted(self):
        html = (
            '<meta property="og:url" content="https://www.facebook.com/marketplace/item/999/" />'
            + FEED_HTML
        )
        with self.assertRaises(RuntimeError) as caught:
            resolve_share_target(FEED, html)
        self.assertNotIn("999", str(caught.exception))

    def test_login_wall_is_auth_required(self):
        html = "<html><body>Log into Facebook email or mobile number forgot password</body></html>"
        with self.assertRaises(RuntimeError) as caught:
            resolve_share_target(LOGIN, html)
        self.assertIn("login wall", str(caught.exception).lower())
        self.assertEqual(exit_code_for_parser_error(str(caught.exception)), 2)

    def test_listing_payload_is_not_a_login_wall(self):
        resolved = resolve_share_target(ITEM, CARD_HTML + "log into facebook")
        self.assertEqual(resolved, ITEM)
        self.assertTrue(html_has_listing_card(CARD_HTML, "1515735863510647"))

    def test_crawled_feed_is_auth_required_not_a_random_item(self):
        with self.assertRaises(RuntimeError) as caught:
            assert_crawled_item_page(SHARE, FEED, FEED_HTML)
        self.assertTrue(str(caught.exception).startswith("AUTH_REQUIRED:"))
        self.assertEqual(exit_code_for_parser_error(str(caught.exception)), 2)

    def test_crawled_item_url_is_accepted(self):
        self.assertEqual(assert_crawled_item_page(ITEM, ITEM, CARD_HTML), ITEM)

    def test_non_facebook_page_is_wrong_page(self):
        with self.assertRaises(RuntimeError) as caught:
            assert_crawled_item_page(
                "https://example.com/listing",
                "https://example.com/listing",
                "<html>nope</html>",
            )
        self.assertTrue(str(caught.exception).startswith("WRONG_PAGE:"))
        self.assertEqual(exit_code_for_parser_error(str(caught.exception)), 4)

    def test_swallowed_wait_failure_is_detected(self):
        class Probe:
            error_message = "Unexpected error: Wait condition failed: Timeout 35000ms"

        self.assertTrue(share_wait_failed(Probe()))
        self.assertTrue(share_wait_failed(None))

        class Ok:
            error_message = ""

        self.assertFalse(share_wait_failed(Ok()))

    def test_session_message_has_server_steps_and_no_secrets(self):
        text = session_expired_message("detail")
        self.assertIn("login_fb.py", text)
        self.assertIn("export_fb_state.py", text)
        self.assertIn("import_fb_state.py", text)
        self.assertNotIn("FB_PASSWORD", text)
        self.assertNotIn("c_user=", text)


if __name__ == "__main__":
    unittest.main()
