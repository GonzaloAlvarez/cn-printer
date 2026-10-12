"""Parser test against a real MFC-L2750DW maintenance page (serial scrubbed)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import brother_ews  # noqa: E402

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "ews" / "general" / "information.html"


class ParseMaintenance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = brother_ews.parse_maintenance(FIXTURE.read_text(encoding="iso-8859-1"))

    def test_identity(self):
        self.assertEqual(self.d["model"], "Brother MFC-L2750DW series")
        self.assertEqual(self.d["firmware"], "ZE")

    def test_counters(self):
        self.assertEqual(self.d["page_counter"], 1328)
        self.assertEqual(self.d["average_coverage_percent"], 5.91)
        self.assertEqual(self.d["drum_life_percent"], 89)
        self.assertEqual(self.d["toner_life_percent"], 70)

    def test_printed_breakdowns(self):
        self.assertEqual(self.d["printed_by_size"]["A4/Letter"], 1322)
        self.assertEqual(self.d["printed_by_size"]["A5"], 6)
        self.assertEqual(self.d["printed_by_type"]["Plain/Thin/Recycled"], 1328)
        self.assertEqual(self.d["printed_by_function"], {"total": 1328, "fax": 0, "copy": 20, "print": 1305, "others": 3})
        self.assertEqual(self.d["duplex_by_function"], {"total": 556, "fax": 0, "copy": 12, "print": 544, "others": 0})

    def test_scanned(self):
        self.assertEqual(self.d["scanned_total"], 446)
        self.assertEqual(self.d["scanned_by_source"], {"adf_simplex": 157, "adf_duplex": 228, "flatbed": 84})

    def test_replacements_and_jams(self):
        self.assertEqual(self.d["replacements"], {"toner": 1, "drum": 0})
        self.assertEqual(self.d["jams"], {"total": 0, "tray1": 0, "inside": 0, "rear": 0, "duplex": 0, "adf_simplex": 1, "adf_duplex": 0})

    def test_errors(self):
        self.assertEqual(self.d["errors"][0], (1, "Jam Inside", 1032))
        self.assertEqual(self.d["errors"][3], (4, "Replace Toner", 498))
        self.assertEqual(len(self.d["errors"]), 7)  # rows 8-10 are empty and skipped

    def test_login_page_is_rejected_by_cache(self):
        cache = brother_ews.EwsCache("")  # no URL → never fetches
        data, up, _ = cache.snapshot()
        self.assertIsNone(data)
        self.assertFalse(up)
        d = brother_ews.parse_maintenance("<html><body>Login</body></html>")
        self.assertNotIn("page_counter", d)


if __name__ == "__main__":
    unittest.main()
