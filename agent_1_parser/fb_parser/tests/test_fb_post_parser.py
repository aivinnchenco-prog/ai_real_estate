#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_FB_ROOT = Path(__file__).resolve().parents[1]
if str(_FB_ROOT) not in sys.path:
    sys.path.insert(0, str(_FB_ROOT))

from agent1b.fb_parser import (  # noqa: E402
    apply_post_listing,
    apply_swiped_gallery,
    canonicalize_facebook_post_url,
    extract_injected_post_gallery,
    extract_post_images_from_html,
    extract_post_text_from_html,
    facebook_pcb_photo_url,
    facebook_post_anchor,
    fallback_post_image_urls,
    is_post_share_url,
    is_share_url,
    _looks_like_facebook_post_url,
    ListingData,
    prepare_listing_url,
    resolve_share_redirect,
)

USER_SHARE_P = "https://www.facebook.com/share/p/18CkfMgxXB/"


SHARE_P = "https://www.facebook.com/share/p/19Zq5Tms6B/?mibextid=wwXIfr"
PERMALINK = (
    "https://www.facebook.com/permalink.php"
    "?story_fbid=pfbid0sJB85jR3MwGyPRx4psZ53N1HTVNkyfszHuGEZyjXGxdN5jfvY5HXzpfXphaXXfPvl"
    "&id=61579839465792"
)
POST_HTML = r'''
<meta property="og:title" content="Villa Rawai | Facebook">
<meta property="og:image" content="https://scontent.xx.fbcdn.net/v/t39.30808-6/villa.jpg">
<script>
{"message":{"text":"3 bedrooms villa in Rawai ฿80,000 / month\\nPool and sea view"}}
{"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/photo_big.jpg"}
{"owner":{"name":"Karen Lopez"}}
</script>
'''


class SharePUrlTests(unittest.TestCase):
    def test_prepare_keeps_share_p(self):
        cleaned = "https://www.facebook.com/share/p/19Zq5Tms6B/"
        with patch("agent1b.fb_parser.resolve_share_redirect", side_effect=lambda url, **_: url):
            self.assertEqual(prepare_listing_url(SHARE_P), cleaned)
        self.assertTrue(is_share_url(SHARE_P))
        self.assertTrue(is_post_share_url(SHARE_P))
        self.assertFalse(is_post_share_url("https://www.facebook.com/share/abcXYZ/"))
        self.assertTrue(is_post_share_url(USER_SHARE_P))
        self.assertTrue(_looks_like_facebook_post_url(USER_SHARE_P))
        self.assertEqual(
            canonicalize_facebook_post_url(USER_SHARE_P + "?mibextid=wwXIfr"),
            USER_SHARE_P,
        )

    def test_prepare_resolves_marketplace_share_302(self):
        share = "https://www.facebook.com/share/19AdfaDDDX/?mibextid=wwXIfr"
        item = "https://www.facebook.com/marketplace/item/2104111113695472/?rdid=abc"

        class _Resp:
            status_code = 302
            url = share.split("?")[0]
            headers = {"Location": item}

        with patch("agent1b.fb_parser.requests.head", return_value=_Resp()):
            self.assertEqual(
                prepare_listing_url(share),
                "https://www.facebook.com/marketplace/item/2104111113695472/",
            )

    def test_prepare_resolves_digit_share_code(self):
        """App share links look like /share/14sjJatvPcD/ — not /share/p/."""
        share = "https://www.facebook.com/share/14sjJatvPcD/"
        item = "https://www.facebook.com/marketplace/item/1395707932002372/?rdid=abc"

        class _Resp:
            status_code = 302
            url = share
            headers = {"Location": item}

        with patch("agent1b.fb_parser.requests.head", return_value=_Resp()):
            self.assertEqual(
                prepare_listing_url(share),
                "https://www.facebook.com/marketplace/item/1395707932002372/",
            )
        self.assertTrue(is_share_url(share))
        self.assertFalse(is_post_share_url(share))

    def test_resolve_share_follows_relative_hop(self):
        share = "https://www.facebook.com/share/14sjJatvPcD/"

        class _First:
            status_code = 302
            url = share
            headers = {"Location": "/marketplace/item/1395707932002372/?rdid=x"}

        with patch("agent1b.fb_parser.requests.head", return_value=_First()):
            self.assertEqual(
                resolve_share_redirect(share),
                "https://www.facebook.com/marketplace/item/1395707932002372/",
            )

    def test_prepare_resolves_app_share_to_page_post(self):
        """facebook.com/share/CODE/ can 302 to a Page post, not Marketplace."""
        share = "https://www.facebook.com/share/1CJTVzkW1V/"
        post = (
            "https://www.facebook.com/100032753753481/posts/1757369152031526/"
            "?rdid=lZhLAYbsgnU0ka8m&share_url=https%3A%2F%2Fwww.facebook.com%2Fshare%2F1CJTVzkW1V%2F"
        )

        class _Resp:
            status_code = 302
            url = share
            headers = {"Location": post}

        with patch("agent1b.fb_parser.requests.head", return_value=_Resp()):
            resolved = prepare_listing_url(share)
        self.assertEqual(
            resolved,
            "https://www.facebook.com/100032753753481/posts/1757369152031526",
        )
        self.assertFalse(is_share_url(resolved))
        self.assertTrue(_looks_like_facebook_post_url(resolved))

    def test_resolve_share_keeps_url_on_network_error(self):
        import requests as req

        with patch("agent1b.fb_parser.requests.head", side_effect=req.RequestException("down")):
            self.assertEqual(
                resolve_share_redirect("https://www.facebook.com/share/19AdfaDDDX/"),
                "https://www.facebook.com/share/19AdfaDDDX/",
            )

    def test_canonicalize_permalink(self):
        self.assertEqual(
            canonicalize_facebook_post_url(PERMALINK + "&ref=share"),
            PERMALINK,
        )
        self.assertEqual(
            canonicalize_facebook_post_url(SHARE_P),
            "https://www.facebook.com/share/p/19Zq5Tms6B/",
        )
        group = (
            "https://www.facebook.com/groups/rentandsalephuket/"
            "permalink/4734339803459229/?rdid=abc"
        )
        self.assertEqual(
            canonicalize_facebook_post_url(group),
            "https://www.facebook.com/groups/rentandsalephuket/permalink/4734339803459229",
        )
        self.assertEqual(
            facebook_post_anchor(group),
            "4734339803459229",
        )

    def test_pcb_photo_url_from_href(self):
        html = (
            '<a href="/photo/?fbid=122153263766994648&amp;set=pcb.122153269316994648">'
            "thumb</a>"
        )
        self.assertEqual(
            facebook_pcb_photo_url(html),
            "https://www.facebook.com/photo/?fbid=122153263766994648&set=pcb.122153269316994648",
        )

    def test_pcb_photo_url_prefers_listing_hints(self):
        html = (
            '<a href="/photo/?fbid=28164116936556640&set=pcb.27998505936445202">other</a>'
            '<a href="/photo/?fbid=122153263766994648&set=pcb.122153269316994648">villa</a>'
        )
        url = facebook_pcb_photo_url(
            html,
            hint_urls=[
                "https://scontent.xx.fbcdn.net/v/t39.30808-6/788880701_122153263826994648_x_n.jpg"
            ],
        )
        self.assertIn("pcb.122153269316994648", url)


class PostHtmlExtractTests(unittest.TestCase):
    def test_text_and_images(self):
        fields = extract_post_text_from_html(POST_HTML)
        self.assertIn("Rawai", fields["description"])
        self.assertEqual(fields["seller"], "Karen Lopez")
        self.assertIn("Villa Rawai", fields["title"])
        images = extract_post_images_from_html(POST_HTML)
        self.assertTrue(any("photo_big.jpg" in u or "villa.jpg" in u for u in images))

    def test_text_prefers_permalink_over_earlier_group_post(self):
        html = r'''
<meta property="og:description" content="Laguna Villa Village For Rent Luxury Pool Villa">
{"id":"111","message":{"text":"The best unit for rent in Karon beach 2 bedrooms condo ฿45,000"}}
{"all_subattachments":{"count":2,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/karon1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/karon2.jpg"}}}
]}}
{"id":"4734339803459229","message":{"text":"Laguna Villa Village For Rent Luxury Pool Villa for Rent – Laguna Village, Phuket 4 bedrooms ฿550,000"}}
{"all_subattachments":{"count":2,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/laguna1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/laguna2.jpg"}}}
]}}
'''
        fields = extract_post_text_from_html(html, story_id="4734339803459229")
        self.assertIn("Laguna Villa Village", fields["description"])
        self.assertNotIn("Karon", fields["description"])
        images = extract_post_images_from_html(html, story_id="4734339803459229")
        self.assertTrue(all("laguna" in u for u in images))
        self.assertFalse(any("karon" in u for u in images))

    def test_pcb_url_skips_ambiguous_neighbour_albums(self):
        html = (
            '<a href="/photo/?fbid=111&set=pcb.111">other</a>'
            '<a href="/photo/?fbid=222&set=pcb.222">villa</a>'
        )
        self.assertIsNone(facebook_pcb_photo_url(html))
        self.assertIn(
            "pcb.222",
            facebook_pcb_photo_url(html, story_id="222") or "",
        )

    def test_drops_small_neighbour_photo(self):
        html = r'''
{"message":{"text":"POOL VILLA FOR RENT – MOUANA"}}
{"all_subattachments":{"count":3,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa3.jpg"}}}
]}}
{"image":{"height":443,"width":590,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/people_hallway.jpg"}}
<meta property="og:image" content="https://scontent.xx.fbcdn.net/v/t39.30808-6/people_hallway.jpg">
'''
        images = extract_post_images_from_html(html)
        self.assertEqual(len(images), 3)
        self.assertTrue(all("villa" in u for u in images))
        self.assertFalse(any("people_hallway" in u for u in images))

    def test_ignores_other_page_post_gallery(self):
        html = r'''
{"id":"pfbidLISTINGXYZ","message":{"text":"POOL VILLA FOR RENT – MOUANA RESIDENCE KOH KAEW 3 bedrooms swimming pool sea view"}}
{"all_subattachments":{"count":3,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa3.jpg"}}}
]}}
{"id":"pfbidNEWSABC","message":{"text":"Police arrested a tourist at the station after a long investigation downtown today"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police1.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police2.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police3.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police4.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police5.jpg"}}}
]}}
'''
        images = extract_post_images_from_html(html, story_id="pfbidLISTINGXYZ")
        self.assertEqual(len(images), 3)
        self.assertTrue(all("villa" in u for u in images))
        self.assertFalse(any("police" in u for u in images))

    def test_merges_split_listing_albums(self):
        html = r'''
{"id":"pfbidLISTINGXYZ","message":{"text":"POOL VILLA FOR RENT – MOUANA RESIDENCE KOH KAEW 3 bedrooms swimming pool sea view"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa3.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa4.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa5.jpg"}}}
]}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa6.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa7.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa8.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa9.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa10.jpg"}}}
]}}
{"id":"pfbidNEWSABC","message":{"text":"Police arrested a tourist at the station after a long investigation downtown today"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police1.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police2.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police3.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police4.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police5.jpg"}}}
]}}
'''
        images = extract_post_images_from_html(html, story_id="pfbidLISTINGXYZ")
        self.assertEqual(len(images), 10)
        self.assertTrue(all("villa" in u for u in images))
        self.assertFalse(any("police" in u for u in images))

    def test_comment_does_not_split_listing_albums(self):
        html = r'''
{"id":"pfbidLISTINGXYZ","message":{"text":"POOL VILLA FOR RENT – MOUANA RESIDENCE KOH KAEW 3 bedrooms swimming pool sea view"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa3.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa4.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa5.jpg"}}}
]}}
{"message":{"text":"Nice villa thanks"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa6.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa7.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa8.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa9.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa10.jpg"}}}
]}}
{"id":"pfbidNEWSABC","message":{"text":"Police arrested a tourist at the station after a long investigation downtown Phuket news"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police1.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police2.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police3.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police4.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police5.jpg"}}}
]}}
'''
        images = extract_post_images_from_html(html, story_id="pfbidLISTINGXYZ")
        self.assertEqual(len(images), 10)
        self.assertTrue(all("villa" in u for u in images))
        self.assertFalse(any("police" in u for u in images))

    def test_story_id_beats_longer_news_caption(self):
        html = r'''
{"id":"pfbidLISTINGXYZ","message":{"text":"POOL VILLA FOR RENT – MOUANA RESIDENCE KOH KAEW 3 bedrooms swimming pool sea view"}}
{"all_subattachments":{"count":3,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa3.jpg"}}}
]}}
{"id":"pfbidNEWSABC","message":{"text":"Police searched a house near the station after a long investigation downtown Phuket today with several officers"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police1.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police2.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police3.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police4.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police5.jpg"}}}
]}}
'''
        images = extract_post_images_from_html(html, story_id="pfbidLISTINGXYZ")
        self.assertEqual(len(images), 3)
        self.assertTrue(all("villa" in u for u in images))
        self.assertFalse(any("police" in u for u in images))

    def test_repeated_listing_caption_still_merges_albums(self):
        html = r'''
{"id":"pfbidLISTINGXYZ","message":{"text":"POOL VILLA FOR RENT – MOUANA RESIDENCE KOH KAEW 3 bedrooms swimming pool sea view"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa3.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa4.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa5.jpg"}}}
]}}
{"message":{"text":"POOL VILLA FOR RENT – MOUANA RESIDENCE KOH KAEW 3 bedrooms swimming pool sea view"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa6.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa7.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa8.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa9.jpg"}}},
  {"media":{"image":{"height":1152,"width":2048,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa10.jpg"}}}
]}}
{"id":"pfbidNEWSABC","message":{"text":"Police arrested a tourist at the station after a long investigation downtown today"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police1.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police2.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police3.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police4.jpg"}}},
  {"media":{"image":{"height":960,"width":1705,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/police5.jpg"}}}
]}}
'''
        images = extract_post_images_from_html(html, story_id="pfbidLISTINGXYZ")
        self.assertEqual(len(images), 10)
        self.assertTrue(all("villa" in u for u in images))
        self.assertFalse(any("police" in u for u in images))

    def test_other_listing_album_is_not_glued(self):
        html = r'''
{"id":"pfbidLISTINGXYZ","message":{"text":"POOL VILLA FOR RENT – MOUANA RESIDENCE KOH KAEW 3 bedrooms swimming pool sea view"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa3.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa4.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/villa5.jpg"}}}
]}}
''' + (" " * 90000) + r'''
{"id":"pfbidOTHERLISTING","message":{"text":"Modern condo for rent in Phuket town 2 bedrooms swimming pool near the beach club"}}
{"all_subattachments":{"count":5,"nodes":[
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/condo1.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/condo2.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/condo3.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/condo4.jpg"}}},
  {"media":{"image":{"height":2048,"width":1366,"uri":"https:\/\/scontent.xx.fbcdn.net\/v\/t39.30808-6\/condo5.jpg"}}}
]}}
'''
        images = extract_post_images_from_html(html, story_id="pfbidLISTINGXYZ")
        self.assertEqual(len(images), 5)
        self.assertTrue(all("villa" in u for u in images))
        self.assertFalse(any("condo" in u for u in images))

    def test_injected_viewer_gallery_beats_five_thumbs(self):
        urls = [
            f"https://scontent.xx.fbcdn.net/v/t39.30808-6/villa{i}.jpg" for i in range(1, 13)
        ]
        html = (
            POST_HTML
            + '<script id="openhome-post-gallery" type="application/json">'
            + __import__("json").dumps(urls)
            + "</script>"
        )
        self.assertEqual(extract_injected_post_gallery(html), urls)
        listing = ListingData(source_url=SHARE_P, backend="test")
        filled = apply_post_listing(
            listing,
            html=html,
            js_payload={"page_kind": "post", "canonical_url": PERMALINK, "image_urls": []},
            result=SimpleNamespace(media={"images": []}),
            fallback_url=SHARE_P,
        )
        self.assertEqual(len(filled.image_urls), 12)
        self.assertEqual(filled.debug["image_strategy"], "post_viewer")

    def test_apply_post_listing(self):
        listing = ListingData(source_url=SHARE_P, backend="test")
        result = SimpleNamespace(media={"images": []})
        filled = apply_post_listing(
            listing,
            html=POST_HTML,
            js_payload={
                "page_kind": "post",
                "canonical_url": PERMALINK,
                "description": "short",
                "image_urls": [
                    "https://scontent.xx.fbcdn.net/v/t39.30808-6/from_js.jpg",
                ],
            },
            result=result,
            fallback_url=SHARE_P,
        )
        self.assertEqual(filled.source_type, "facebook_post")
        self.assertEqual(filled.source_url, PERMALINK)
        self.assertIn("Rawai", filled.description)
        self.assertEqual(filled.bedrooms, 3)
        self.assertTrue(filled.image_urls)


class GallerySwipeMergeTests(unittest.TestCase):
    def test_post_prefers_swipe_over_html_dump(self):
        listing = ListingData(source_url=SHARE_P, backend="test")
        listing.image_urls = [
            "https://scontent.xx.fbcdn.net/v/t39.30808-6/karon1.jpg",
            "https://scontent.xx.fbcdn.net/v/t39.30808-6/karon2.jpg",
        ]
        listing.debug["image_strategy"] = "post_html_attachments"
        apply_swiped_gallery(
            listing,
            {
                "error": "",
                "opened": True,
                "final_url": PERMALINK,
                "urls": [
                    "https://scontent.xx.fbcdn.net/v/t39.30808-6/111_222_333_n.jpg?stp=s1080",
                    "https://scontent.xx.fbcdn.net/v/t39.30808-6/444_555_666_n.jpg?stp=s1080",
                ],
            },
            prefer_swipe=True,
        )
        self.assertEqual(listing.debug["image_strategy"], "gallery_swipe_post")
        self.assertEqual(len(listing.image_urls), 2)
        self.assertTrue(all("karon" not in u for u in listing.image_urls))

    def test_post_keeps_html_if_swipe_empty(self):
        listing = ListingData(source_url=SHARE_P, backend="test")
        listing.image_urls = ["https://scontent.xx.fbcdn.net/v/t39.30808-6/html.jpg"]
        listing.debug["image_strategy"] = "post_html_attachments"
        apply_swiped_gallery(
            listing,
            {"error": "no_clickable_photo", "opened": False, "final_url": "", "urls": []},
            prefer_swipe=True,
        )
        self.assertEqual(listing.image_urls, ["https://scontent.xx.fbcdn.net/v/t39.30808-6/html.jpg"])
        self.assertEqual(listing.debug["image_strategy"], "post_html_attachments")

    def test_marketplace_never_shrinks(self):
        listing = ListingData(
            source_url="https://www.facebook.com/marketplace/item/1/",
            backend="test",
        )
        listing.image_urls = [
            "https://scontent.xx.fbcdn.net/v/t39.30808-6/111_222_333_n.jpg",
            "https://scontent.xx.fbcdn.net/v/t39.30808-6/444_555_666_n.jpg",
            "https://scontent.xx.fbcdn.net/v/t39.30808-6/777_888_999_n.jpg",
        ]
        apply_swiped_gallery(
            listing,
            {
                "error": "",
                "opened": True,
                "final_url": listing.source_url,
                "urls": ["https://scontent.xx.fbcdn.net/v/t39.30808-6/111_222_333_n.jpg"],
            },
            prefer_swipe=False,
        )
        self.assertEqual(len(listing.image_urls), 3)

    def test_post_keeps_html_if_swipe_only_one_frame(self):
        listing = ListingData(source_url=SHARE_P, backend="test")
        listing.image_urls = [
            "https://scontent.xx.fbcdn.net/v/t39.30808-6/111_222_333_n.jpg",
            "https://scontent.xx.fbcdn.net/v/t39.30808-6/444_555_666_n.jpg",
        ]
        listing.debug["image_strategy"] = "post_js"
        apply_swiped_gallery(
            listing,
            {
                "error": "",
                "opened": True,
                "final_url": PERMALINK,
                "urls": ["https://scontent.xx.fbcdn.net/v/t39.30808-6/111_222_333_n.jpg"],
            },
            prefer_swipe=True,
        )
        self.assertEqual(len(listing.image_urls), 2)
        self.assertEqual(listing.debug["image_strategy"], "post_js")

    def test_single_image_post_without_swipeable_gallery(self):
        """A post with only og:image / an <img> still yields a photo when swipe is empty."""
        html = """
        <meta property="og:title" content="Rawai house | Facebook">
        <meta content="https://scontent.xx.fbcdn.net/v/t39.30808-6/only_house.jpg" property="og:image">
        <div role="article">
          <img src="https://scontent.xx.fbcdn.net/v/t39.30808-6/only_house.jpg" />
        </div>
        """
        self.assertEqual(
            fallback_post_image_urls(html),
            ["https://scontent.xx.fbcdn.net/v/t39.30808-6/only_house.jpg"],
        )
        images = extract_post_images_from_html(html)
        self.assertEqual(images, ["https://scontent.xx.fbcdn.net/v/t39.30808-6/only_house.jpg"])
        listing = ListingData(source_url=USER_SHARE_P, backend="test")
        apply_post_listing(
            listing,
            html=html,
            js_payload={"page_kind": "post", "image_urls": []},
            result=SimpleNamespace(media={"images": []}),
            fallback_url=USER_SHARE_P,
        )
        apply_swiped_gallery(
            listing,
            {"error": "no_clickable_photo", "opened": False, "final_url": "", "urls": []},
            prefer_swipe=True,
        )
        self.assertEqual(
            listing.image_urls,
            ["https://scontent.xx.fbcdn.net/v/t39.30808-6/only_house.jpg"],
        )
        self.assertNotEqual(listing.debug["image_strategy"], "gallery_swipe_post")


if __name__ == "__main__":
    unittest.main()
