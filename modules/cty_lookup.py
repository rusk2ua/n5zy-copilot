"""
CTY.DAT Lookup Module
Parses AD1C's cty.dat file for callsign → DXCC entity resolution.
Provides lat/lon for bearing/distance calculations.

cty.dat format:
  - Each DXCC entity has a header line (colon-delimited fields) followed by
    comma-separated prefixes ending with a semicolon.
  - Header: Country Name: CQ_Zone: ITU_Zone: Continent: Lat: Lon: UTC_Offset: Primary_Prefix:
  - Prefix overrides: (CQ_zone), [ITU_zone], <lat/lon>, {continent}, ~UTC~
  - Exact callsign entries start with =
"""

import math
import re
import json
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple
from pathlib import Path


@dataclass
class DXCCEntity:
    """Represents a DXCC entity from cty.dat"""
    name: str = ''
    primary_prefix: str = ''
    continent: str = ''
    cq_zone: int = 0
    itu_zone: int = 0
    lat: float = 0.0
    lon: float = 0.0
    utc_offset: float = 0.0
    dxcc_number: int = 0  # Set from dxcc_entities.json or LoTW mapping


@dataclass
class PrefixEntry:
    """A prefix or exact callsign with optional zone/location overrides"""
    prefix: str = ''
    entity: Optional[DXCCEntity] = None
    is_exact: bool = False
    # Overrides (None = use entity defaults)
    cq_zone: Optional[int] = None
    itu_zone: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    continent: Optional[str] = None
    utc_offset: Optional[float] = None

    def get_lat(self) -> float:
        return self.lat if self.lat is not None else self.entity.lat

    def get_lon(self) -> float:
        return self.lon if self.lon is not None else self.entity.lon


# Operational modifiers — NOT country prefixes
OPERATIONAL_SUFFIXES = {'/R', '/P', '/M', '/QRP', '/MM', '/AM', '/B',
                        '/ROVER', '/PORTABLE', '/MOBILE', '/BEACON'}


class CTYLookup:
    """Callsign to DXCC entity lookup using cty.dat"""

    def __init__(self):
        self.entities = []                    # List[DXCCEntity]
        self.prefix_map = {}                  # {prefix_str: PrefixEntry}
        self.exact_call_map = {}              # {callsign: PrefixEntry}
        self._sorted_prefixes = []            # Sorted by length (longest first) for matching
        self._loaded = False
        self._file_date = ''

    def load_file(self, filepath: str) -> bool:
        """Parse cty.dat file. Returns True on success."""
        filepath = Path(filepath)
        if not filepath.exists():
            print(f"CTY: File not found: {filepath}")
            return False

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            self.entities.clear()
            self.prefix_map.clear()
            self.exact_call_map.clear()

            self._parse_cty_dat(content)

            # Build sorted prefix list (longest first for matching)
            self._sorted_prefixes = sorted(
                self.prefix_map.keys(), key=len, reverse=True
            )

            self._loaded = True
            self._file_date = filepath.stat().st_mtime

            print(f"CTY: Loaded {len(self.entities)} entities, "
                  f"{len(self.prefix_map)} prefixes, "
                  f"{len(self.exact_call_map)} exact calls")
            return True

        except Exception as e:
            print(f"CTY: Error loading {filepath}: {e}")
            return False

    def _parse_cty_dat(self, content: str):
        """Parse cty.dat content into entities and prefix maps."""
        # cty.dat entries: entity header line followed by prefix lines, ending with ;
        # Accumulate lines until we see a semicolon
        current_block = []

        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            current_block.append(line)

            # Check if this line ends the block (contains semicolon)
            if ';' in line:
                self._parse_entity_block(current_block)
                current_block = []

    def _parse_entity_block(self, lines):
        """Parse one entity block (header + prefix lines)."""
        if not lines:
            return

        # First line is the entity header
        header_line = lines[0]

        # The header is colon-delimited with exactly 8 fields
        # Country Name: CQ: ITU: Cont: Lat: Lon: UTC: Prefix:
        # But the prefix list may start on the same line after the last colon
        parts = header_line.split(':')
        if len(parts) < 8:
            return

        try:
            entity = DXCCEntity(
                name=parts[0].strip(),
                cq_zone=int(parts[1].strip()),
                itu_zone=int(parts[2].strip()),
                continent=parts[3].strip(),
                lat=float(parts[4].strip()),
                lon=-float(parts[5].strip()),  # cty.dat: + = West, we want + = East
                utc_offset=float(parts[6].strip()),
                primary_prefix=parts[7].strip().rstrip(';').strip(),
            )
        except (ValueError, IndexError):
            return

        self.entities.append(entity)

        # Collect all prefix text from header remainder and subsequent lines
        # Everything after the 8th colon on the first line, plus all subsequent lines
        prefix_text = ':'.join(parts[8:]) if len(parts) > 8 else ''
        for line in lines[1:]:
            prefix_text += ' ' + line

        # Remove trailing semicolon and split by comma
        prefix_text = prefix_text.replace(';', '').strip()
        if not prefix_text:
            # If no explicit prefixes, use primary prefix
            prefix_text = entity.primary_prefix

        prefixes = [p.strip() for p in prefix_text.split(',') if p.strip()]

        # Always add the primary prefix
        if entity.primary_prefix not in [p.lstrip('=') for p in prefixes]:
            self._add_prefix_entry(entity.primary_prefix, entity)

        # Parse each prefix with possible overrides
        for raw_prefix in prefixes:
            self._parse_prefix(raw_prefix, entity)

    def _parse_prefix(self, raw: str, entity: DXCCEntity):
        """Parse a single prefix entry with possible override markers."""
        prefix = raw.strip()
        if not prefix:
            return

        # Extract overrides
        cq_override = None
        itu_override = None
        lat_override = None
        lon_override = None
        cont_override = None
        utc_override = None

        # (nn) = CQ zone override
        m = re.search(r'\((\d+)\)', prefix)
        if m:
            cq_override = int(m.group(1))
            prefix = prefix[:m.start()] + prefix[m.end():]

        # [nn] = ITU zone override
        m = re.search(r'\[(\d+)\]', prefix)
        if m:
            itu_override = int(m.group(1))
            prefix = prefix[:m.start()] + prefix[m.end():]

        # <lat/lon> = location override
        m = re.search(r'<([^>]+)>', prefix)
        if m:
            try:
                coords = m.group(1).split('/')
                lat_override = float(coords[0])
                lon_override = -float(coords[1])  # cty.dat: + = West
            except (ValueError, IndexError):
                pass
            prefix = prefix[:m.start()] + prefix[m.end():]

        # {CC} = continent override
        m = re.search(r'\{([A-Z]{2})\}', prefix)
        if m:
            cont_override = m.group(1)
            prefix = prefix[:m.start()] + prefix[m.end():]

        # ~n.n~ = UTC offset override
        m = re.search(r'~([^~]+)~', prefix)
        if m:
            try:
                utc_override = float(m.group(1))
            except ValueError:
                pass
            prefix = prefix[:m.start()] + prefix[m.end():]

        prefix = prefix.strip()
        if not prefix:
            return

        # Check if exact callsign (starts with =)
        is_exact = prefix.startswith('=')
        if is_exact:
            prefix = prefix[1:]

        entry = PrefixEntry(
            prefix=prefix.upper(),
            entity=entity,
            is_exact=is_exact,
            cq_zone=cq_override,
            itu_zone=itu_override,
            lat=lat_override,
            lon=lon_override,
            continent=cont_override,
            utc_offset=utc_override,
        )

        if is_exact:
            self.exact_call_map[prefix.upper()] = entry
        else:
            self.prefix_map[prefix.upper()] = entry

    def _add_prefix_entry(self, prefix: str, entity: DXCCEntity):
        """Add a simple prefix entry (no overrides)."""
        entry = PrefixEntry(prefix=prefix.upper(), entity=entity)
        self.prefix_map[prefix.upper()] = entry

    # ── DXCC Entity Number Mapping ──

    def load_dxcc_mapping(self, filepath: str) -> bool:
        """Load primary prefix → DXCC entity number mapping from JSON file."""
        filepath = Path(filepath)
        if not filepath.exists():
            print(f"CTY: DXCC mapping not found: {filepath}")
            return False

        try:
            with open(filepath, 'r') as f:
                mapping = json.load(f)

            count = 0
            for entity in self.entities:
                pfx = entity.primary_prefix.upper()
                if pfx in mapping:
                    entity.dxcc_number = int(mapping[pfx])
                    count += 1

            print(f"CTY: Mapped {count}/{len(self.entities)} entities to DXCC numbers")
            return True

        except Exception as e:
            print(f"CTY: Error loading DXCC mapping: {e}")
            return False

    def set_dxcc_mapping(self, mapping: Dict[str, int]):
        """Accept prefix→DXCC number dict (e.g., from LoTW data) to enrich entities."""
        if not mapping:
            return
        count = 0
        for entity in self.entities:
            pfx = entity.primary_prefix.upper()
            if pfx in mapping and entity.dxcc_number == 0:
                entity.dxcc_number = mapping[pfx]
                count += 1
        if count:
            print(f"CTY: Enriched {count} entities with DXCC numbers from LoTW data")

    # ── Callsign Normalization ──

    def _normalize_callsign(self, callsign: str) -> Tuple[str, Optional[str]]:
        """
        Normalize a callsign, handling / suffixes correctly.

        Returns (base_call, country_prefix_override):
        - base_call: the core callsign for lookup
        - country_prefix_override: a country prefix to use instead of the base call,
          or None to look up the base call normally.

        Examples:
          N5ZY/R     → ('N5ZY', None)          — /R is Rover, strip it
          W1AW/4     → ('W1AW', None)          — call area suffix, strip it
          HK0/DF3TJ  → ('DF3TJ', 'HK0')       — prefix/call, HK0 is the country
          W1AW/VP9   → ('W1AW', 'VP9')         — call/prefix, VP9 is the country
          N5ZY       → ('N5ZY', None)           — no suffix
        """
        call = callsign.upper().strip()

        if '/' not in call:
            return (call, None)

        # Check for known operational modifiers (case-insensitive)
        for suffix in OPERATIONAL_SUFFIXES:
            if call.endswith(suffix):
                return (call[:-len(suffix)], None)

        parts = call.split('/')

        if len(parts) != 2:
            # Multiple slashes — take the longest part
            longest = max(parts, key=len)
            return (longest, None)

        left, right = parts

        # Single digit suffix = call area (e.g., W1AW/4)
        if len(right) == 1 and right.isdigit():
            return (left, None)

        # Check if right side is a known country prefix
        right_is_prefix = self._is_known_prefix(right)
        left_is_prefix = self._is_known_prefix(left)

        # If right is a known country prefix AND looks shorter/simpler than left
        # → Call/Prefix format (e.g., W1AW/VP9)
        if right_is_prefix and len(right) <= len(left):
            return (left, right)

        # If left is a known country prefix AND looks shorter/simpler than right
        # → Prefix/Call format (e.g., HK0/DF3TJ)
        if left_is_prefix and len(left) < len(right):
            return (right, left)

        # If right is a known prefix regardless of length
        if right_is_prefix:
            return (left, right)

        # If left is a known prefix regardless of length
        if left_is_prefix and not right_is_prefix:
            return (right, left)

        # Fallback: use the longer part as the base call
        if len(left) >= len(right):
            return (left, None)
        else:
            return (right, None)

    def _is_known_prefix(self, text: str) -> bool:
        """Check if text matches a known country prefix in the prefix map."""
        text = text.upper()
        # Direct match
        if text in self.prefix_map:
            return True
        # Check if it starts with any known prefix (but prefer exact)
        for pfx in self._sorted_prefixes:
            if text == pfx:
                return True
        return False

    # ── Special Prefix Rules ──
    # Some prefixes require exact suffix-length checks to distinguish entities.
    # Key: prefix, Value: required alpha-suffix length
    # If the alpha suffix length does NOT match, skip this prefix and continue
    # to shorter matches (which will typically hit the parent entity like K→USA).
    #
    # KG4xx  = Guantanamo Bay (exactly 2-letter alpha suffix)
    # KG4x   = United States  (1-letter suffix)
    # KG4xxx = United States  (3+ letter suffix)
    EXACT_SUFFIX_LENGTH_RULES = {
        'KG4': 2,   # KG4AA = Guantanamo, KG4LIO/KG4A = USA
    }

    # ── Lookup ──

    def lookup(self, callsign: str) -> Optional[DXCCEntity]:
        """
        Resolve callsign to DXCC entity.

        1. Normalize the callsign (handle /R, /P, /VP9, etc.)
        2. If country override prefix, look up that prefix
        3. Check exact callsign map
        4. Apply suffix-length rules (e.g., KG4)
        5. Try progressively shorter prefixes (longest match first)
        """
        if not self._loaded or not callsign:
            return None

        base_call, country_override = self._normalize_callsign(callsign)

        # If we have a country override prefix, look that up
        if country_override:
            entry = self._lookup_prefix(country_override)
            if entry:
                return entry.entity

        # Check exact callsign map
        if base_call in self.exact_call_map:
            return self.exact_call_map[base_call].entity

        # Longest prefix match with suffix-length rules
        entry = self._lookup_prefix_checked(base_call)
        if entry:
            return entry.entity

        return None

    def lookup_with_location(self, callsign: str) -> Optional[PrefixEntry]:
        """
        Like lookup() but returns the PrefixEntry with location overrides.
        Use this when you need the specific lat/lon for a callsign
        (which may differ from the entity default for zone/location overrides).
        """
        if not self._loaded or not callsign:
            return None

        base_call, country_override = self._normalize_callsign(callsign)

        if country_override:
            entry = self._lookup_prefix(country_override)
            if entry:
                return entry

        if base_call in self.exact_call_map:
            return self.exact_call_map[base_call]

        return self._lookup_prefix_checked(base_call)

    def _lookup_prefix_checked(self, text: str) -> Optional[PrefixEntry]:
        """
        Find the longest matching prefix, applying suffix-length rules.

        For prefixes like KG4 (Guantanamo Bay), only match if the suffix
        after the prefix is within the allowed length. Otherwise, skip that
        prefix and continue to shorter matches (which will hit K→USA).
        """
        text = text.upper()
        for length in range(len(text), 0, -1):
            candidate = text[:length]
            if candidate in self.prefix_map:
                # Check exact suffix-length rule (e.g., KG4)
                if candidate in self.EXACT_SUFFIX_LENGTH_RULES:
                    suffix = text[length:]
                    required_len = self.EXACT_SUFFIX_LENGTH_RULES[candidate]
                    # Extract alphabetic suffix only (ignore trailing digits)
                    alpha_suffix = ''.join(c for c in suffix if c.isalpha())
                    if len(alpha_suffix) != required_len:
                        continue  # Skip — suffix doesn't match, not this entity
                return self.prefix_map[candidate]
        return None

    def _lookup_prefix(self, text: str) -> Optional[PrefixEntry]:
        """Find the longest matching prefix for text (no suffix rules)."""
        text = text.upper()
        for length in range(len(text), 0, -1):
            candidate = text[:length]
            if candidate in self.prefix_map:
                return self.prefix_map[candidate]
        return None

    # ── Bearing & Distance ──

    def get_bearing_distance(self, my_lat: float, my_lon: float,
                              callsign: str) -> Optional[Tuple[float, float]]:
        """
        Return (bearing_degrees, distance_miles) from my position to callsign's
        entity location (using prefix entry overrides if available).
        Returns None if callsign cannot be resolved.
        """
        entry = self.lookup_with_location(callsign)
        if not entry:
            return None

        target_lat = entry.get_lat()
        target_lon = entry.get_lon()

        if target_lat == 0.0 and target_lon == 0.0:
            return None

        bearing = self._bearing(my_lat, my_lon, target_lat, target_lon)
        distance_km = self._haversine(my_lat, my_lon, target_lat, target_lon)
        distance_mi = distance_km * 0.621371

        return (bearing, distance_mi)

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in km."""
        R = 6371  # Earth's radius in km
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate bearing from point 1 to point 2 in degrees."""
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        dlon = math.radians(lon2 - lon1)
        x = math.sin(dlon) * math.cos(lat2_r)
        y = (math.cos(lat1_r) * math.sin(lat2_r) -
             math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon))
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    @staticmethod
    def bearing_to_compass(bearing: float) -> str:
        """Convert bearing degrees to compass direction string."""
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        index = round(bearing / 45) % 8
        return directions[index]

    # ── Auto-update ──

    def update_from_ad1c(self, data_dir: str = 'data') -> bool:
        """Download latest cty.dat from AD1C. Returns True on success."""
        import urllib.request

        url = 'https://www.country-files.com/big-cty/cty.dat'
        dest = Path(data_dir) / 'cty.dat'

        try:
            print(f"CTY: Downloading from {url}...")
            os.makedirs(data_dir, exist_ok=True)

            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'N5ZY-CoPilot/1.9')

            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read()

            with open(dest, 'wb') as f:
                f.write(data)

            print(f"CTY: Downloaded {len(data)} bytes to {dest}")

            # Reload
            return self.load_file(str(dest))

        except Exception as e:
            print(f"CTY: Download failed: {e}")
            return False

    # ── Status ──

    def get_status(self) -> dict:
        """Get current status for UI display."""
        return {
            'loaded': self._loaded,
            'entity_count': len(self.entities),
            'prefix_count': len(self.prefix_map),
            'exact_count': len(self.exact_call_map),
        }
