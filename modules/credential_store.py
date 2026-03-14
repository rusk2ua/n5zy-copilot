"""
Credential Store Module
Encrypts/decrypts sensitive fields in settings.json using Fernet (AES-128-CBC + HMAC-SHA256).

Key is stored in a user-specific file outside the repo:
  - Windows: %APPDATA%/n5zy-copilot/.credential_key
  - macOS/Linux: ~/.config/n5zy-copilot/.credential_key

If the `cryptography` package is not installed, all functions are no-ops
(credentials stay plain text) with a one-time console warning.
"""

import copy
import os
import sys
import stat
from pathlib import Path

# ── Sensitive field definitions ──────────────────────────────────────────────

SENSITIVE_KEYS = {
    'twilio_account_sid', 'twilio_auth_token',
    'twilio_from_number', 'twilio_to_number',
    'lotw_username', 'lotw_password',
    'qrz_username', 'qrz_password',
    'victron_key',
    'sms_subscribers',
}

# Nested structures: key → list of sub-keys to encrypt within each dict in the list
SENSITIVE_NESTED = {
    'slack_webhooks': ['url'],
}

ENCRYPTED_PREFIX = "ENC:"

# ── Fernet backend (optional dependency) ─────────────────────────────────────

_fernet = None
_warned = False


def _get_key_path():
    """Return platform-specific path for the encryption key file."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return base / 'n5zy-copilot' / '.credential_key'


def _restrict_permissions(path):
    """Set file permissions so only the current user can read/write."""
    try:
        if sys.platform == 'win32':
            # On Windows, use icacls to restrict to current user only
            import subprocess
            username = os.environ.get('USERNAME', '')
            if username:
                subprocess.run(
                    ['icacls', str(path), '/inheritance:r',
                     '/grant:r', f'{username}:(R,W)'],
                    capture_output=True, timeout=10
                )
        else:
            # Unix: chmod 600
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception as e:
        print(f"Credential store: Warning — could not restrict key file permissions: {e}")


def _load_or_create_key():
    """Load existing Fernet key or generate a new one."""
    key_path = _get_key_path()
    if key_path.exists():
        return key_path.read_bytes().strip()

    # Generate new key
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    _restrict_permissions(key_path)
    print(f"Credential store: New encryption key created at {key_path}")
    return key


def _get_fernet():
    """Get the cached Fernet instance, or None if cryptography is not available."""
    global _fernet, _warned
    if _fernet is not None:
        return _fernet

    try:
        from cryptography.fernet import Fernet
        key = _load_or_create_key()
        _fernet = Fernet(key)
        return _fernet
    except ImportError:
        if not _warned:
            print("Credential store: WARNING — 'cryptography' package not installed. "
                  "Credentials will be stored in plain text. "
                  "Install with: pip install cryptography")
            _warned = True
        return None
    except Exception as e:
        if not _warned:
            print(f"Credential store: WARNING — encryption unavailable ({e}). "
                  "Credentials will be stored in plain text.")
            _warned = True
        return None


# ── Encrypt / Decrypt single values ─────────────────────────────────────────

def encrypt_value(plaintext):
    """Encrypt a string value. Returns 'ENC:...' or the original if encryption unavailable."""
    if not plaintext or not isinstance(plaintext, str):
        return plaintext
    if plaintext.startswith(ENCRYPTED_PREFIX):
        return plaintext  # Already encrypted

    f = _get_fernet()
    if f is None:
        return plaintext

    try:
        token = f.encrypt(plaintext.encode('utf-8'))
        return ENCRYPTED_PREFIX + token.decode('ascii')
    except Exception as e:
        print(f"Credential store: Encryption error: {e}")
        return plaintext


def decrypt_value(token):
    """Decrypt an 'ENC:...' string. Returns plaintext or empty string on failure."""
    if not token or not isinstance(token, str):
        return token
    if not token.startswith(ENCRYPTED_PREFIX):
        return token  # Not encrypted, return as-is (plain text or migration)

    f = _get_fernet()
    if f is None:
        return ''  # Can't decrypt without cryptography

    try:
        encrypted_bytes = token[len(ENCRYPTED_PREFIX):].encode('ascii')
        return f.decrypt(encrypted_bytes).decode('utf-8')
    except Exception as e:
        print(f"Credential store: Decryption failed (wrong key or corrupted data): {e}")
        return ''  # User will need to re-enter credentials


# ── Config-level encrypt / decrypt ───────────────────────────────────────────

def encrypt_config(config):
    """Deep-copy config and encrypt all sensitive fields for writing to disk."""
    out = copy.deepcopy(config)

    # Top-level sensitive keys
    for key in SENSITIVE_KEYS:
        if key in out and out[key]:
            out[key] = encrypt_value(out[key])

    # Nested sensitive keys (e.g., slack_webhooks → url)
    for list_key, sub_keys in SENSITIVE_NESTED.items():
        if list_key in out and isinstance(out[list_key], list):
            for item in out[list_key]:
                if isinstance(item, dict):
                    for sk in sub_keys:
                        if sk in item and item[sk]:
                            item[sk] = encrypt_value(item[sk])

    return out


def decrypt_config(config):
    """Deep-copy config and decrypt all 'ENC:' fields for runtime use."""
    out = copy.deepcopy(config)

    # Top-level sensitive keys
    for key in SENSITIVE_KEYS:
        if key in out and isinstance(out[key], str) and out[key].startswith(ENCRYPTED_PREFIX):
            out[key] = decrypt_value(out[key])

    # Nested sensitive keys
    for list_key, sub_keys in SENSITIVE_NESTED.items():
        if list_key in out and isinstance(out[list_key], list):
            for item in out[list_key]:
                if isinstance(item, dict):
                    for sk in sub_keys:
                        val = item.get(sk, '')
                        if isinstance(val, str) and val.startswith(ENCRYPTED_PREFIX):
                            item[sk] = decrypt_value(val)

    return out


def needs_migration(config):
    """Check if any non-empty sensitive field is still plain text (not encrypted)."""
    f = _get_fernet()
    if f is None:
        return False  # No encryption available, nothing to migrate

    # Top-level
    for key in SENSITIVE_KEYS:
        val = config.get(key, '')
        if val and isinstance(val, str) and not val.startswith(ENCRYPTED_PREFIX):
            return True

    # Nested
    for list_key, sub_keys in SENSITIVE_NESTED.items():
        items = config.get(list_key, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    for sk in sub_keys:
                        val = item.get(sk, '')
                        if val and isinstance(val, str) and not val.startswith(ENCRYPTED_PREFIX):
                            return True

    return False
