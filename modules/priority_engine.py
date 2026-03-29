"""
Priority Engine Module
Centralized priority classification for all alert types.
Called by both PSK Monitor and Log Monitor to determine if a callsign
should trigger a priority alert.

Priority codes (in check order):
  DX!  — DX expedition callsigns (Daily DX mode only)
  AP!  — Friends/family/club callsigns (VHF Contest & QSO Party modes)
  DX2  — New DXCC entity (any mode, requires cty.dat + LoTW)
  DX3  — New DXCC on band/mode/both (any mode, requires cty.dat + LoTW)
"""

from dataclasses import dataclass
from typing import Optional, Set


@dataclass
class PriorityResult:
    """Result of a priority check."""
    code: str             # 'DX!', 'DX2', 'DX3', 'AP!'
    callsign: str         # The matched callsign
    entity_name: str      # DXCC entity name (or '' if N/A)
    dxcc_number: int      # DXCC entity number (or 0 if N/A)
    voice_msg: str        # Pre-formatted voice message
    tag: str              # Treeview tag name for styling


class PriorityEngine:
    """
    Centralized priority classification engine.

    Checks all priority rules in order: DX! → AP! → DX2 → DX3
    Returns the highest-priority match, or None if no match.

    Usage:
        engine = PriorityEngine()
        engine.configure(config, cty_lookup, lotw_client)
        result = engine.check('W1AW', '20m', 'FT8')
        if result:
            print(f"{result.code}: {result.callsign} — {result.entity_name}")
    """

    def __init__(self):
        # DX! stations (CSV parsed to set)
        self.dx_stations: Set[str] = set()
        # AP! stations (CSV parsed to set)
        self.ap_stations: Set[str] = set()

        # Feature enables
        self.priority_enabled = False
        self.dx2_enabled = False
        self.dx3_enabled = False
        self.dx3_granularity = 'band'  # 'band' | 'mode' | 'band_mode'

        # Contest mode (affects which alerts fire)
        self.contest_mode = 'vhf'

        # External modules (may be None if not loaded)
        self.cty_lookup = None
        self.lotw_client = None

    def configure(self, config: dict, cty_lookup=None, lotw_client=None):
        """
        Rebuild engine state from config and data modules.

        Call this after settings change or at startup.
        """
        self.priority_enabled = config.get('psk_priority_enabled', False)
        self.contest_mode = config.get('contest_mode', 'vhf')

        # Parse DX! stations
        dx_str = config.get('dx_priority_stations', '')
        if not dx_str:
            # Fallback: migrate from old psk_priority_stations
            dx_str = config.get('psk_priority_stations', '')
        self.dx_stations = {s.strip().upper() for s in dx_str.split(',') if s.strip()}

        # Parse AP! stations
        ap_str = config.get('ap_priority_stations', '')
        self.ap_stations = {s.strip().upper() for s in ap_str.split(',') if s.strip()}

        # DX2/DX3 toggles
        self.dx2_enabled = config.get('dx2_enabled', False)
        self.dx3_enabled = config.get('dx3_enabled', False)
        self.dx3_granularity = config.get('dx3_granularity', 'band')

        # External modules
        self.cty_lookup = cty_lookup
        self.lotw_client = lotw_client

        # Log configuration
        modes = []
        if self.dx_stations:
            modes.append(f"DX!({len(self.dx_stations)})")
        if self.ap_stations:
            modes.append(f"AP!({len(self.ap_stations)})")
        if self.dx2_enabled:
            modes.append("DX2")
        if self.dx3_enabled:
            modes.append(f"DX3({self.dx3_granularity})")

        if modes:
            print(f"PriorityEngine: Configured — {', '.join(modes)} | "
                  f"mode={self.contest_mode} | "
                  f"cty={'yes' if cty_lookup and cty_lookup._loaded else 'no'} | "
                  f"lotw={'yes' if lotw_client and lotw_client.is_loaded() else 'no'}")
        else:
            print("PriorityEngine: Configured — no alerts enabled")

    def check(self, callsign: str, band: str, mode: str = '') -> Optional[PriorityResult]:
        """
        Check a callsign against all priority rules.

        Args:
            callsign: The callsign to check (e.g., 'W1AW', 'N5ZY/R')
            band: Band string (e.g., '20m', '6m', '70cm')
            mode: Mode string (e.g., 'FT8', 'CW', 'SSB') — needed for DX3 mode checks

        Returns:
            PriorityResult if callsign matches any priority rule, None otherwise.
            Returns the HIGHEST priority match (DX! > AP! > DX2 > DX3).
        """
        if not self.priority_enabled:
            return None

        call_upper = callsign.upper().strip()
        if not call_upper:
            return None

        # Strip operational modifiers for matching against DX!/AP! lists
        base_call = self._strip_modifiers(call_upper)

        # ── DX! — DX Expedition (Daily DX mode only) ──
        if self.contest_mode == 'daily_dx' and self.dx_stations:
            if base_call in self.dx_stations or call_upper in self.dx_stations:
                return PriorityResult(
                    code='DX!',
                    callsign=call_upper,
                    entity_name=self._get_entity_name(call_upper),
                    dxcc_number=self._get_dxcc_number(call_upper),
                    voice_msg=f"D X Priority {self._call_to_voice(call_upper)} on {band}",
                    tag='dx',
                )

        # ── AP! — Friends/Family/Club (VHF, 222up, QSO Party modes) ──
        if self.contest_mode in ('vhf', '222up', 'qso_party') and self.ap_stations:
            if base_call in self.ap_stations or call_upper in self.ap_stations:
                return PriorityResult(
                    code='AP!',
                    callsign=call_upper,
                    entity_name='',
                    dxcc_number=0,
                    voice_msg=f"A Priority {self._call_to_voice(call_upper)} on {band}",
                    tag='ap',
                )

        # ── DX2 — New DXCC Entity (any mode) ──
        if self.dx2_enabled and self.cty_lookup and self.lotw_client:
            if self.cty_lookup._loaded and self.lotw_client.is_loaded():
                entity = self.cty_lookup.lookup(call_upper)
                if entity and entity.dxcc_number > 0:
                    if self.lotw_client.is_new_entity(entity.dxcc_number):
                        return PriorityResult(
                            code='DX2',
                            callsign=call_upper,
                            entity_name=entity.name,
                            dxcc_number=entity.dxcc_number,
                            voice_msg=f"D X 2 {self._call_to_voice(call_upper)} on {band}, {entity.name}",
                            tag='dx2',
                        )
                else:
                    print(f"PriorityEngine: {call_upper} — CTY lookup {'no match' if not entity else f'dxcc=0'}")
            else:
                print(f"PriorityEngine: {call_upper} — skipping DX2 "
                      f"(cty={'yes' if self.cty_lookup._loaded else 'no'}, "
                      f"lotw={'yes' if self.lotw_client.is_loaded() else 'no'})")

        # ── DX3 — New Band/Mode/Both (any mode) ──
        if self.dx3_enabled and self.cty_lookup and self.lotw_client:
            if self.cty_lookup._loaded and self.lotw_client.is_loaded():
                entity = self.cty_lookup.lookup(call_upper)
                if entity and entity.dxcc_number > 0:
                    dxcc = entity.dxcc_number

                    # Skip if entity is entirely new (that's DX2 territory)
                    if self.lotw_client.is_new_entity(dxcc):
                        # Already covered by DX2 if enabled, or report as DX3 if DX2 disabled
                        if not self.dx2_enabled:
                            return PriorityResult(
                                code='DX3',
                                callsign=call_upper,
                                entity_name=entity.name,
                                dxcc_number=dxcc,
                                voice_msg=f"D X 3 {self._call_to_voice(call_upper)} on {band}, {entity.name}",
                                tag='dx3',
                            )
                        return None  # DX2 would have caught it above

                    # Entity IS confirmed — check if new on this band/mode
                    is_new = False
                    mode_group = self.lotw_client.get_mode_group(mode) if mode else ''

                    if self.dx3_granularity == 'band':
                        is_new = self.lotw_client.is_new_band(dxcc, band)
                    elif self.dx3_granularity == 'mode' and mode_group:
                        is_new = self.lotw_client.is_new_mode(dxcc, mode_group)
                    elif self.dx3_granularity == 'band_mode' and mode_group:
                        is_new = self.lotw_client.is_new_band_mode(dxcc, band, mode_group)

                    if is_new:
                        gran_label = {
                            'band': f'new on {band}',
                            'mode': f'new {mode_group}',
                            'band_mode': f'new {band} {mode_group}',
                        }.get(self.dx3_granularity, 'new')

                        return PriorityResult(
                            code='DX3',
                            callsign=call_upper,
                            entity_name=entity.name,
                            dxcc_number=dxcc,
                            voice_msg=f"D X 3 {self._call_to_voice(call_upper)} on {band}, {entity.name}, {gran_label}",
                            tag='dx3',
                        )
                    else:
                        print(f"PriorityEngine: {call_upper} — {entity.name} (#{dxcc}) "
                              f"confirmed on {band}/{mode_group or '?'} (not DX3, "
                              f"granularity={self.dx3_granularity})")
            else:
                if not (self.dx2_enabled and self.cty_lookup and self.lotw_client):
                    # Already printed in DX2 block above
                    pass
                else:
                    print(f"PriorityEngine: {call_upper} — skipping DX3 "
                          f"(cty={'yes' if self.cty_lookup._loaded else 'no'}, "
                          f"lotw={'yes' if self.lotw_client.is_loaded() else 'no'})")

        return None

    # ── Helper Methods ──

    def _strip_modifiers(self, callsign: str) -> str:
        """Strip operational modifiers for matching against station lists."""
        modifiers = ['/R', '/P', '/M', '/QRP', '/MM', '/AM', '/B',
                     '/ROVER', '/PORTABLE', '/MOBILE', '/BEACON']
        call = callsign.upper()
        for mod in modifiers:
            if call.endswith(mod):
                return call[:-len(mod)]
        return call

    def _get_entity_name(self, callsign: str) -> str:
        """Get DXCC entity name for callsign, or '' if unavailable."""
        if not self.cty_lookup or not self.cty_lookup._loaded:
            return ''
        entity = self.cty_lookup.lookup(callsign)
        return entity.name if entity else ''

    def _get_dxcc_number(self, callsign: str) -> int:
        """Get DXCC entity number for callsign, or 0 if unavailable."""
        if not self.cty_lookup or not self.cty_lookup._loaded:
            return 0
        entity = self.cty_lookup.lookup(callsign)
        return entity.dxcc_number if entity else 0

    @staticmethod
    def _call_to_voice(callsign: str) -> str:
        """
        Format callsign for voice synthesis.
        Spells out each character with pauses for clarity.
        e.g., 'N5ZY' → 'N 5 Z Y'
        """
        # Strip modifiers for voice
        base = callsign.split('/')[0] if '/' in callsign else callsign
        return ' '.join(base.upper())
