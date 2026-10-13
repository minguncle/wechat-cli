import hashlib
import hmac
import json
import re
import struct
import tempfile
import unittest

from key_scan_common import (
    PAGE_SZ,
    KEY_SZ,
    SALT_SZ,
    scan_memory_for_keys,
    scan_raw_key_candidates_for_any_salt,
    save_results,
    verify_passphrase,
)


def make_sqlcipher_page1(enc_key, salt):
    page = bytearray(PAGE_SZ)
    page[:SALT_SZ] = salt
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = bytes(page[SALT_SZ: PAGE_SZ - 80 + 16])
    hm = hmac.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    page[PAGE_SZ - 64: PAGE_SZ] = hm.digest()
    return bytes(page)


class KeyScanCandidateTests(unittest.TestCase):
    def setUp(self):
        self.key = bytes.fromhex("11" * 32)
        self.salt = bytes.fromhex("22" * 16)
        self.salt_hex = self.salt.hex()
        self.page1 = make_sqlcipher_page1(self.key, self.salt)
        self.db_files = [("message/message_0.db", "message_0.db", len(self.page1), self.salt_hex, self.page1)]
        self.salt_to_dbs = {self.salt_hex: ["message/message_0.db"]}
        self.old_hex_re = re.compile(b"x'([0-9a-fA-F]{64,192})'")

    def scan(self, data, diagnostics=None):
        key_map = {}
        remaining = set(self.salt_to_dbs)
        count = scan_memory_for_keys(
            data=data,
            hex_re=self.old_hex_re,
            db_files=self.db_files,
            salt_to_dbs=self.salt_to_dbs,
            key_map=key_map,
            remaining_salts=remaining,
            base_addr=0x1000,
            pid=1234,
            print_fn=lambda *args, **kwargs: None,
            diagnostics=diagnostics,
        )
        return count, key_map, remaining

    def test_finds_plain_64_hex_key_without_sql_literal_prefix(self):
        data = b"prefix " + self.key.hex().encode("ascii") + b" suffix"

        _, key_map, remaining = self.scan(data)

        self.assertEqual(key_map[self.salt_hex], self.key.hex())
        self.assertEqual(remaining, set())

    def test_finds_utf16le_hex_key(self):
        data = b"prefix " + self.key.hex().encode("utf-16le") + b" suffix"

        _, key_map, remaining = self.scan(data)

        self.assertEqual(key_map[self.salt_hex], self.key.hex())
        self.assertEqual(remaining, set())

    def test_finds_raw_32_byte_key_near_raw_salt(self):
        data = b"A" * 40 + self.salt + b"B" * 12 + self.key + b"C" * 40

        _, key_map, remaining = self.scan(data)

        self.assertEqual(key_map[self.salt_hex], self.key.hex())
        self.assertEqual(remaining, set())

    def test_records_salt_diagnostics_even_when_key_is_missing(self):
        diagnostics = {}
        data = b"salt-ascii " + self.salt_hex.encode("ascii") + b" raw " + self.salt

        _, key_map, remaining = self.scan(data, diagnostics=diagnostics)

        self.assertEqual(key_map, {})
        self.assertEqual(remaining, {self.salt_hex})
        self.assertGreaterEqual(diagnostics["ascii_salt_hits"], 1)
        self.assertGreaterEqual(diagnostics["raw_salt_hits"], 1)

    def test_records_raw_salt_page_alignment_diagnostics(self):
        diagnostics = {}
        data = self.salt + b"A" * 32 + b"B" * (4096 - 48) + b"C" + self.salt

        _, key_map, remaining = self.scan(data, diagnostics=diagnostics)

        self.assertEqual(key_map, {})
        self.assertEqual(remaining, {self.salt_hex})
        self.assertEqual(diagnostics["raw_salt_page_aligned_hits"], 1)
        self.assertEqual(diagnostics["raw_salt_non_page_aligned_hits"], 1)

    def test_records_redacted_salt_context_samples(self):
        diagnostics = {"salt_context_sample_limit": 4}
        data = (
            b"prefix WCDB cipher "
            + struct.pack("<Q", 0x0000012345678000)
            + b"A" * 16
            + self.salt
            + b"B" * 16
        )

        _, key_map, remaining = self.scan(data, diagnostics=diagnostics)

        self.assertEqual(key_map, {})
        self.assertEqual(remaining, {self.salt_hex})
        sample = diagnostics["salt_context_samples"][0]
        self.assertEqual(sample["salt"], self.salt_hex)
        self.assertEqual(sample["pid"], 1234)
        self.assertEqual(sample["address"], hex(0x1000 + data.index(self.salt)))
        self.assertIn("WCDB", sample["markers"])
        self.assertIn("cipher", sample["markers"])
        self.assertGreaterEqual(sample["pointer_like_qwords"], 1)
        self.assertIn("context_sha256", sample)
        self.assertNotIn("prefix", repr(sample))

    def test_collects_redacted_pointer_scan_targets_from_salt_context(self):
        ptr = 0x0000012345678000
        diagnostics = {
            "salt_context_sample_limit": 4,
            "pointer_scan_target_limit": 8,
        }
        data = (
            b"A" * 32
            + struct.pack("<Q", ptr)
            + b"B" * 16
            + self.salt
            + b"C" * 16
        )

        _, key_map, remaining = self.scan(data, diagnostics=diagnostics)

        self.assertEqual(key_map, {})
        self.assertEqual(remaining, {self.salt_hex})
        targets = diagnostics["pointer_scan_targets"]
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["pid"], 1234)
        self.assertEqual(targets[0]["salt"], self.salt_hex)
        self.assertEqual(targets[0]["target"], hex(ptr))
        self.assertIn("source_address", targets[0])
        self.assertIn("message/message_0.db", targets[0]["dbs"])

    def test_scans_aligned_raw_key_candidates_without_nearby_salt(self):
        diagnostics = {}
        key_map = {}
        remaining = set(self.salt_to_dbs)
        data = b"A" * 16 + self.key + b"B" * 16

        count = scan_raw_key_candidates_for_any_salt(
            data=data,
            db_files=self.db_files,
            salt_to_dbs=self.salt_to_dbs,
            key_map=key_map,
            remaining_salts=remaining,
            base_addr=0x2000,
            pid=1234,
            print_fn=lambda *args, **kwargs: None,
            source="test pointer target",
            diagnostics=diagnostics,
            stride=8,
        )

        self.assertGreater(count, 0)
        self.assertEqual(key_map[self.salt_hex], self.key.hex())
        self.assertEqual(remaining, set())
        self.assertGreaterEqual(diagnostics["raw_anywhere_candidates"], 1)

    def test_found_key_log_redacts_raw_key_material(self):
        logs = []
        key_map = {}
        remaining = set(self.salt_to_dbs)
        data = b"A" * 16 + self.key + b"B" * 16

        scan_raw_key_candidates_for_any_salt(
            data=data,
            db_files=self.db_files,
            salt_to_dbs=self.salt_to_dbs,
            key_map=key_map,
            remaining_salts=remaining,
            base_addr=0x2000,
            pid=1234,
            print_fn=logs.append,
            source="test pointer target",
            diagnostics={},
            stride=8,
        )

        joined = "\n".join(logs)
        self.assertIn("[FOUND]", joined)
        self.assertNotIn(self.key.hex(), joined)
        self.assertNotIn("enc_key=", joined)

    def test_save_results_preserves_existing_entries_when_partial_probe_matches(self):
        other_key = bytes.fromhex("33" * 32)
        other_salt = bytes.fromhex("44" * 16)
        other_salt_hex = other_salt.hex()
        other_page1 = make_sqlcipher_page1(other_key, other_salt)
        db_files = [
            ("message/message_2.db", "message_2.db", len(other_page1), other_salt_hex, other_page1),
            *self.db_files,
        ]
        salt_to_dbs = {
            other_salt_hex: ["message/message_2.db"],
            self.salt_hex: ["message/message_0.db"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            keys_path = f"{tmp}/all_keys.json"
            with open(keys_path, "w", encoding="utf-8") as f:
                json.dump({
                    "_db_dir": "old-db-dir",
                    "message/message_2.db": {
                        "enc_key": other_key.hex(),
                        "salt": other_salt_hex,
                        "size_mb": 0.0,
                    },
                }, f)

            save_results(
                db_files=db_files,
                salt_to_dbs=salt_to_dbs,
                key_map={self.salt_hex: self.key.hex()},
                db_dir="new-db-dir",
                out_file=keys_path,
                print_fn=lambda *_: None,
            )

            with open(keys_path, encoding="utf-8") as f:
                saved = json.load(f)

        self.assertEqual(saved["_db_dir"], "new-db-dir")
        self.assertEqual(saved["message/message_2.db"]["enc_key"], other_key.hex())
        self.assertEqual(saved["message/message_0.db"]["enc_key"], self.key.hex())

    def test_verifies_wcdb_passphrase_derived_key(self):
        passphrase = bytes.fromhex("33" * 32)
        enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, self.salt, 256000, dklen=KEY_SZ)
        page1 = make_sqlcipher_page1(enc_key, self.salt)

        self.assertTrue(verify_passphrase(passphrase, page1))
        self.assertFalse(verify_passphrase(bytes.fromhex("44" * 32), page1))


if __name__ == "__main__":
    unittest.main()
