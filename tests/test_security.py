import sys
import tempfile
import unittest
import hashlib
import hmac
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_server"))

from security import PairingManager


class PairingManagerTests(unittest.TestCase):
    def test_pairing_issues_device_bound_token_and_rotates_code(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PairingManager(Path(directory) / "security.json")
            original_code = manager.pairing_code

            token = manager.pair("device-12345678", original_code)

            self.assertIsNotNone(token)
            self.assertNotEqual(original_code, manager.pairing_code)
            self.assertTrue(manager.authenticate("device-12345678", token))
            self.assertFalse(manager.authenticate("another-device", token))

            challenge = "0123456789abcdef"
            response = hmac.new(token.encode("ascii"), challenge.encode("ascii"), hashlib.sha256).hexdigest()
            self.assertTrue(manager.authenticate_challenge("device-12345678", response, challenge))
            self.assertFalse(manager.authenticate_challenge("device-12345678", response, "different"))

    def test_invalid_pairing_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PairingManager(Path(directory) / "security.json")
            self.assertIsNone(manager.pair("device-12345678", "000000" if manager.pairing_code != "000000" else "999999"))


if __name__ == "__main__":
    unittest.main()
