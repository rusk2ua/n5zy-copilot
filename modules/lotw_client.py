"""
LoTW (Logbook of The World) Client Module
Downloads and parses LoTW ADIF QSL credit data to determine confirmed DXCC entities.
Used by PriorityEngine for DX2 (new entity) and DX3 (new band/mode) alerts.

LoTW download endpoint:
  https://lotw.arrl.org/lotwuser/lotwreport.adi?login=CALL&password=PASS&qso_query=1&qso_qslsince=1970-01-01&qso_qsl=yes&qso_owncall=CALL
"""

import re
import os
import ssl
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, Tuple, Optional


# Mode groups per spec
MODE_GROUPS = {
    # CW
    'CW': 'CW',
    # PHONE
    'SSB': 'PHONE', 'USB': 'PHONE', 'LSB': 'PHONE',
    'FM': 'PHONE', 'AM': 'PHONE', 'DIGITALVOICE': 'PHONE',
    # DATA
    'FT8': 'DATA', 'FT4': 'DATA', 'JT65': 'DATA', 'JT9': 'DATA',
    'RTTY': 'DATA', 'PSK31': 'DATA', 'PSK63': 'DATA', 'PSK125': 'DATA',
    'MFSK': 'DATA', 'OLIVIA': 'DATA', 'CONTESTIA': 'DATA',
    'SSTV': 'DATA', 'HELL': 'DATA', 'ROS': 'DATA',
    'DOMINO': 'DATA', 'MT63': 'DATA', 'THOR': 'DATA',
    'JT4': 'DATA', 'Q65': 'DATA', 'MSK144': 'DATA',
    'FSK441': 'DATA', 'ISCAT': 'DATA',
}

# Band normalization (LoTW uses various band labels)
BAND_NORMALIZE = {
    '160M': '160m', '80M': '80m', '60M': '60m', '40M': '40m',
    '30M': '30m', '20M': '20m', '17M': '17m', '15M': '15m',
    '12M': '12m', '10M': '10m', '6M': '6m', '4M': '4m',
    '2M': '2m', '1.25M': '1.25m', '70CM': '70cm', '33CM': '33cm',
    '23CM': '23cm', '13CM': '13cm', '9CM': '9cm', '5CM': '5cm',
    '3CM': '3cm', '1.2CM': '1.2cm',
}


class LoTWClient:
    """Downloads and parses LoTW QSL credit data for DXCC tracking."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._loaded = False
        self.cty_lookup = None  # Set externally for callsign → DXCC fallback

        # Confirmed entity sets
        self.confirmed_entities: Set[int] = set()          # All confirmed DXCC numbers
        self.confirmed_band: Dict[str, Set[int]] = defaultdict(set)  # {band: set(dxcc)}
        self.confirmed_mode: Dict[str, Set[int]] = defaultdict(set)  # {mode_group: set(dxcc)}
        self.confirmed_band_mode: Dict[Tuple[str, str], Set[int]] = defaultdict(set)  # {(band, mode_group): set(dxcc)}

        # Raw parsed records for cross-referencing
        self._records = []

        # Prefix → DXCC mapping extracted from LoTW data
        self._prefix_dxcc_map: Dict[str, int] = {}

        # Cache metadata
        self._last_refresh: Optional[datetime] = None
        self._record_count = 0

    # ── Download ──

    def download_credits(self, username: str = '', password: str = '',
                          owncall: str = '') -> bool:
        """
        Download QSL credits from LoTW.
        Returns True on success.

        Endpoint: lotwreport.adi with qso_qsl=yes to get confirmed QSOs only.
        """
        username = username or self.config.get('lotw_username', '')
        password = password or self.config.get('lotw_password', '')
        owncall = owncall or username  # Usually same as login

        if not username or not password:
            print("LoTW: No username/password configured")
            return False

        try:
            params = {
                'login': username,
                'password': password,
                'qso_query': '1',
                'qso_qslsince': '1970-01-01',
                'qso_qsl': 'yes',
                'qso_qsldetail': 'yes',   # Include DXCC/COUNTRY/IOTA detail
                'qso_mydetail': 'yes',     # Include own station detail
                'qso_owncall': owncall,
            }

            url = f"https://lotw.arrl.org/lotwuser/lotwreport.adi?{urllib.parse.urlencode(params)}"
            print(f"LoTW: Downloading credits for {username}...")

            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'N5ZY-CoPilot/1.9')

            # Build SSL context with Windows cert store (Python may not load it)
            ssl_ctx = ssl.create_default_context()
            if hasattr(ssl, 'enum_certificates'):
                for store_name in ('ROOT', 'CA'):
                    try:
                        for cert, encoding, trust in ssl.enum_certificates(store_name):
                            if encoding == 'x509_asn' and trust is True:
                                try:
                                    ssl_ctx.load_verify_locations(
                                        cadata=ssl.DER_cert_to_PEM_cert(cert))
                                except ssl.SSLError:
                                    pass
                    except OSError:
                        pass

            with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as response:
                data = response.read().decode('utf-8', errors='ignore')

            # Check for login errors
            if 'ARRL Logbook' in data and 'password' in data.lower():
                print("LoTW: Login failed — check username/password")
                return False

            if '<APP_LoTW' not in data and '<CALL:' not in data:
                # Might be an error page
                if len(data) < 500:
                    print(f"LoTW: Unexpected response: {data[:200]}")
                    return False

            print(f"LoTW: Downloaded {len(data)} bytes")

            # Parse and build sets
            self._records = self._parse_adif(data)
            self._build_confirmed_sets()
            self._loaded = True
            self._last_refresh = datetime.now()

            # Save cache
            cache_path = Path('data') / 'lotw_credits.adi'
            self.save_cache(str(cache_path), data)

            return True

        except Exception as e:
            print(f"LoTW: Download error: {e}")
            return False

    def download_credits_async(self, callback=None, username='', password=''):
        """Download in background thread. callback(success: bool) called when done."""
        def _worker():
            success = self.download_credits(username=username, password=password)
            if callback:
                try:
                    callback(success)
                except Exception:
                    pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t

    # ── File I/O ──

    def load_from_file(self, filepath: str) -> bool:
        """Load previously downloaded/manual ADIF file. Returns True on success."""
        filepath = Path(filepath)
        if not filepath.exists():
            print(f"LoTW: File not found: {filepath}")
            return False

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                data = f.read()

            self._records = self._parse_adif(data)
            self._build_confirmed_sets()
            self._loaded = True

            # Try to get file date
            try:
                self._last_refresh = datetime.fromtimestamp(filepath.stat().st_mtime)
            except Exception:
                self._last_refresh = None

            print(f"LoTW: Loaded {len(self._records)} confirmed QSOs from {filepath}")
            return True

        except Exception as e:
            print(f"LoTW: Error loading {filepath}: {e}")
            return False

    def save_cache(self, filepath: str, raw_data: str = None):
        """Save ADIF data to cache file."""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            if raw_data:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(raw_data)
                print(f"LoTW: Cached to {filepath}")
            else:
                print("LoTW: No raw data to cache")

        except Exception as e:
            print(f"LoTW: Error saving cache: {e}")

    # ── ADIF Parser ──

    def _parse_adif(self, text: str) -> list:
        """
        Parse ADIF text into list of record dicts.
        Each record has: CALL, DXCC, BAND, MODE, COUNTRY, CREDIT_GRANTED, etc.
        """
        records = []

        # Skip header (everything before first <EOH> or first record)
        eoh_match = re.search(r'<EOH>', text, re.IGNORECASE)
        if eoh_match:
            text = text[eoh_match.end():]

        # Split into records at <EOR>
        raw_records = re.split(r'<EOR>', text, flags=re.IGNORECASE)

        for raw in raw_records:
            if not raw.strip():
                continue

            record = {}
            # Find all ADIF fields: <FIELD_NAME:LENGTH[:TYPE]>VALUE
            field_pattern = r'<([A-Z_]+):(\d+)(?::[A-Z])?>'
            for match in re.finditer(field_pattern, raw, re.IGNORECASE):
                field_name = match.group(1).upper()
                field_len = int(match.group(2))
                value_start = match.end()
                value = raw[value_start:value_start + field_len].strip()
                record[field_name] = value

            if record:
                records.append(record)

        print(f"LoTW: Parsed {len(records)} ADIF records")
        return records

    # ── Build Confirmed Sets ──

    def _build_confirmed_sets(self):
        """
        Build confirmed entity sets from parsed records.

        DXCC number source priority:
          1. DXCC field in ADIF record (when qso_qsldetail=yes)
          2. APP_LOTW_DXCC field
          3. cty.dat lookup by CALL (fallback)
        """
        self.confirmed_entities.clear()
        self.confirmed_band.clear()
        self.confirmed_mode.clear()
        self.confirmed_band_mode.clear()
        self._prefix_dxcc_map.clear()

        resolved_via_cty = 0
        skipped_no_dxcc = 0

        for record in self._records:
            # Try multiple DXCC field names
            dxcc_str = (record.get('DXCC', '') or
                        record.get('APP_LOTW_DXCC', '') or
                        record.get('APP_LoTW_DXCC', ''))

            dxcc = 0
            if dxcc_str:
                try:
                    dxcc = int(dxcc_str)
                except ValueError:
                    dxcc = 0

            # Fallback: resolve CALL via cty.dat
            if dxcc <= 0 and self.cty_lookup:
                call = record.get('CALL', '')
                if call:
                    entity = self.cty_lookup.lookup(call)
                    if entity and entity.dxcc_number > 0:
                        dxcc = entity.dxcc_number
                        resolved_via_cty += 1

            if dxcc <= 0:
                skipped_no_dxcc += 1
                continue

            # Only count records that have DXCC credit (CREDIT_GRANTED contains "DXCC")
            # If CREDIT_GRANTED field exists but doesn't mention DXCC, it's not a DXCC credit
            credit = record.get('CREDIT_GRANTED', '')
            # If the field exists but doesn't contain DXCC, skip for DXCC tracking
            # (but if field is absent, assume the record IS a DXCC credit per qso_qsl=yes)
            if credit and 'DXCC' not in credit.upper():
                continue

            # Overall confirmed entity
            self.confirmed_entities.add(dxcc)

            # Per-band
            band_raw = record.get('BAND', '').upper()
            band = BAND_NORMALIZE.get(band_raw, band_raw.lower())
            if band:
                self.confirmed_band[band].add(dxcc)

            # Per-mode group
            mode_raw = record.get('MODE', '').upper()
            mode_group = MODE_GROUPS.get(mode_raw, '')
            if not mode_group:
                # Try APP_LoTW_MODEGROUP (CW, PHONE, DATA)
                mg = record.get('APP_LOTW_MODEGROUP', '').upper()
                if mg in ('CW', 'PHONE', 'DATA'):
                    mode_group = mg
            if mode_group:
                self.confirmed_mode[mode_group].add(dxcc)

            # Per-band+mode
            if band and mode_group:
                self.confirmed_band_mode[(band, mode_group)].add(dxcc)

        self._record_count = len(self._records)

        print(f"LoTW: Built confirmed sets — "
              f"{len(self.confirmed_entities)} entities, "
              f"{len(self.confirmed_band)} bands, "
              f"{len(self.confirmed_mode)} mode groups")
        if resolved_via_cty:
            print(f"LoTW: Resolved {resolved_via_cty} records via cty.dat fallback")
        if skipped_no_dxcc:
            print(f"LoTW: Skipped {skipped_no_dxcc} records with no DXCC resolution")

    # ── Query Methods ──

    def is_new_entity(self, dxcc: int) -> bool:
        """True if this DXCC entity is NOT yet confirmed (new entity = DX2)."""
        if not self._loaded or dxcc <= 0:
            return False
        return dxcc not in self.confirmed_entities

    def is_new_band(self, dxcc: int, band: str) -> bool:
        """True if this entity is NOT confirmed on this band."""
        if not self._loaded or dxcc <= 0 or not band:
            return False
        band = band.lower()
        return dxcc not in self.confirmed_band.get(band, set())

    def is_new_mode(self, dxcc: int, mode_group: str) -> bool:
        """True if this entity is NOT confirmed on this mode group."""
        if not self._loaded or dxcc <= 0 or not mode_group:
            return False
        return dxcc not in self.confirmed_mode.get(mode_group, set())

    def is_new_band_mode(self, dxcc: int, band: str, mode_group: str) -> bool:
        """True if this entity is NOT confirmed on this band+mode combination."""
        if not self._loaded or dxcc <= 0 or not band or not mode_group:
            return False
        band = band.lower()
        return dxcc not in self.confirmed_band_mode.get((band, mode_group), set())

    def get_mode_group(self, mode: str) -> str:
        """Map a mode string to its mode group (CW/PHONE/DATA)."""
        return MODE_GROUPS.get(mode.upper(), 'DATA')  # Default to DATA for unknown digital modes

    def get_prefix_to_dxcc_mapping(self) -> Dict[str, int]:
        """
        Return a mapping useful for enriching cty.dat.
        Builds prefix→DXCC from the LoTW records where we can infer the prefix.
        """
        # Build from records where we have both CALL and DXCC
        mapping = {}
        for record in self._records:
            dxcc_str = record.get('DXCC', '')
            call = record.get('CALL', '')
            if not dxcc_str or not call:
                continue

            try:
                dxcc = int(dxcc_str)
            except ValueError:
                continue

            if dxcc <= 0:
                continue

            # Extract likely prefix from callsign (first 1-3 chars before a digit)
            m = re.match(r'^([A-Z]{1,2}\d)', call.upper())
            if m:
                prefix = m.group(1).rstrip('0123456789')
                if prefix and len(prefix) <= 3:
                    # Only set if not already set (first occurrence wins)
                    if prefix not in mapping:
                        mapping[prefix] = dxcc

        return mapping

    # ── Status ──

    def needs_refresh(self, days: int = 7) -> bool:
        """Check if cached data is stale (older than N days)."""
        if not self._last_refresh:
            return True
        return datetime.now() - self._last_refresh > timedelta(days=days)

    def is_loaded(self) -> bool:
        """Whether LoTW data has been loaded (from download or cache)."""
        return self._loaded

    def get_status(self) -> dict:
        """Get current status for UI display."""
        return {
            'loaded': self._loaded,
            'record_count': self._record_count,
            'entity_count': len(self.confirmed_entities),
            'band_count': sum(len(s) for s in self.confirmed_band.values()),
            'last_refresh': self._last_refresh.isoformat() if self._last_refresh else '',
        }
