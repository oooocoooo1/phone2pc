import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path


def get_app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Phone2PC"
    return Path.home() / ".phone2pc"


class PairingManager:
    """Issues device-bound tokens from a short, one-time pairing code."""

    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else get_app_data_dir() / "security.json"
        self._master_secret = self._load_or_create_secret()
        self._pairing_code = self._new_code()

    @property
    def pairing_code(self):
        return self._pairing_code

    def pair(self, device_id, code):
        if not self._valid_device_id(device_id):
            return None
        if not isinstance(code, str) or not hmac.compare_digest(code, self._pairing_code):
            return None
        token = self._token_for(device_id)
        self._pairing_code = self._new_code()
        return token

    def authenticate(self, device_id, token):
        if not self._valid_device_id(device_id) or not isinstance(token, str):
            return False
        return hmac.compare_digest(token, self._token_for(device_id))

    def authenticate_challenge(self, device_id, response, challenge):
        if not self._valid_device_id(device_id) or not isinstance(response, str) or not isinstance(challenge, str):
            return False
        token = self._token_for(device_id)
        expected = hmac.new(token.encode("ascii"), challenge.encode("ascii"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(response, expected)

    def reset_trusted_devices(self):
        self._master_secret = secrets.token_bytes(32)
        self._pairing_code = self._new_code()
        self._save_secret(self._master_secret)

    def _token_for(self, device_id):
        return hmac.new(self._master_secret, device_id.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _valid_device_id(device_id):
        return isinstance(device_id, str) and 8 <= len(device_id) <= 128

    @staticmethod
    def _new_code():
        return f"{secrets.randbelow(1_000_000):06d}"

    def _load_or_create_secret(self):
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            secret = bytes.fromhex(data["master_secret"])
            if len(secret) == 32:
                return secret
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass

        secret = secrets.token_bytes(32)
        try:
            self._save_secret(secret)
        except OSError:
            # Continue with an ephemeral secret when the profile directory is
            # read-only; pairing remains secure but must be repeated next run.
            pass
        return secret

    def _save_secret(self, secret):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"master_secret": secret.hex()}, indent=2)
        fd, temp_path = tempfile.mkstemp(prefix="security-", suffix=".tmp", dir=self.config_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temp_path, self.config_path)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
