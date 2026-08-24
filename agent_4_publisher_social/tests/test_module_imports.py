"""Smoke: publisher_social CLI and pipeline must import (browser FB chain depends on this)."""

from __future__ import annotations

import unittest


class ModuleImportTests(unittest.TestCase):
    def test_publisher_social_main_imports(self) -> None:
        import publisher_social.__main__ as main  # noqa: F401

        self.assertTrue(hasattr(main, "main"))

    def test_pipeline_imports(self) -> None:
        from publisher_social import pipeline

        self.assertTrue(callable(pipeline.run_publish_chain))

    def test_adb_imports_folder_helpers_from_base(self) -> None:
        from publisher_social.android.adb import (
            carousel_device_dir,
            marketplace_device_dir,
        )
        from publisher_social.channels.base import (
            carousel_folder_name,
            marketplace_folder_name,
        )

        oid = "A_20260819_001"
        self.assertEqual(carousel_folder_name(oid), f"Carousel {oid}")
        self.assertEqual(marketplace_folder_name(oid), f"Open Home {oid}")
        self.assertIn(
            carousel_folder_name(oid),
            carousel_device_dir("/sdcard/Download/publisher_social", oid),
        )
        self.assertIn(
            marketplace_folder_name(oid),
            marketplace_device_dir("/sdcard/Download/brand_open_home", oid),
        )

    def test_validate_production_channels(self) -> None:
        from publisher_social.config import validate_production_channels

        validate_production_channels(["fb_groups", "fb_marketplace"])
        with self.assertRaises(ValueError):
            validate_production_channels(["tiktok"])


if __name__ == "__main__":
    unittest.main()
