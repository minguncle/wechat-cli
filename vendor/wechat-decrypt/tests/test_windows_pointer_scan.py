import unittest

from find_all_keys_windows import _select_pointer_scan_targets


class WindowsPointerScanTests(unittest.TestCase):
    def test_selects_unseen_targets_for_current_pid(self):
        diagnostics = {
            "pointer_scan_targets": [
                {"pid": 1234, "target": "0x1000"},
                {"pid": 9999, "target": "0x2000"},
                {"pid": 1234, "target": "0x1000"},
                {"pid": 1234, "target": "0x3000"},
            ]
        }
        seen = set()

        targets = _select_pointer_scan_targets(diagnostics, 1234, seen, limit=10)

        self.assertEqual([target["target"] for target in targets], ["0x1000", "0x3000"])
        self.assertEqual(seen, {(1234, 0x1000), (1234, 0x3000)})


if __name__ == "__main__":
    unittest.main()
