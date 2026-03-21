# N5ZY VHF Contest Co-Pilot

## Project Overview
A Python/tkinter application for amateur radio VHF/UHF contest roving operations. Integrates GPS tracking, multiple WSJT-X instances, contest logging (N1MM+/N3FJP/Log4OM), battery monitoring, APRS messaging, SMS notifications (Twilio), Slack webhooks, PSK Reporter, and various contest aids into a single dashboard.

**Author:** Marcus, N5ZY  
**License:** Open source for amateur radio use

## Quick Start
```bash
python copilot.py
```

## Project Structure
```
N5ZY-CoPilot/
├── copilot.py                 # Main application (7500+ lines)
├── config/
│   └── settings.json          # Configuration (auto-created)
├── logs/
│   └── *.adi                  # ADIF log files (auto-created)
├── data/
│   └── county_mappings/
│       └── {STATE}.json       # FIPS to county code mappings
├── modules/
│   ├── __init__.py
│   ├── gps_monitor.py         # GPS serial port reader (VK172 dongle)
│   ├── battery_monitor.py     # Victron SmartShunt BLE
│   ├── radio_updater.py       # WSJT-X UDP & N1MM/N3FJP integration
│   ├── log_monitor.py         # WSJT-X log file watcher
│   ├── voice_alerts.py        # Text-to-speech (pyttsx3)
│   ├── aprs_client.py         # APRS-IS messaging & nearby station alerts
│   ├── qsy_advisor.py         # Station database browser
│   ├── grid_boundary.py       # Grid edge proximity alerts
│   ├── psk_monitor.py         # PSK Reporter integration
│   ├── qsoparty_parser.py     # QSO Party .sec file parser
│   ├── county_lookup.py       # GPS to county lookup (Census API)
│   └── credential_store.py    # Fernet encryption for sensitive config fields
├── tools/
│   └── parse_public_logs.py   # 3830scores scraper (used by QSY Advisor)
└── qsy_advisor_data/
    └── station_bands.json     # Station database
```

## Key Dependencies
```bash
pip install pyserial pynmea2 pyttsx3
pip install cryptography        # For credential encryption in settings.json
pip install victron-ble bleak   # For Victron battery monitoring
```

## Development Guidelines

### Code Style
- Python 3.8+ compatible
- tkinter for GUI (ttk widgets preferred)
- Threading for background tasks (GPS, APRS, battery monitoring)
- Use `self.root.after()` to update UI from background threads

### Important Patterns
1. **GPS callbacks**: `gps_monitor.py` calls back to `copilot.py` on position changes
2. **APRS filtering**: Must filter own callsign (any SSID) from position alerts, but allow cross-SSID messages
3. **Contest modes**: VHF Contest, 222 and Up, QSO Party - affects exchange fields and logging
4. **Voice alerts**: Use `self.voice.announce()` for hands-free operation while driving; categories are individually togglable (new_grid, calling_me, etc.)
5. **GPS Time Sync safety**: `sync_system_clock()` in gps_monitor.py has four safety guards — freshness check (30s), max offset (±30s), rate limit (60s monotonic), and forward-only gate (never sets clock backward). Uses median of 5 offset samples to smooth GPS jitter. Never bypass these.
6. **SMS routing**: Automatic alerts (DX!/DX2/DX3/New Grid) go to personal number via `send_sms()`. Rover status broadcasts go to subscriber list via `_send_rover_sms()`. Both use Twilio REST API via `urllib.request` (no pip dependency).
7. **ADIF modes**: FT8/FT4/Q65 are primary modes per ADIF 3.1.1+ (not MFSK submodes). Only SSB gets submode mapping (USB/LSB → SSB).
8. **Credential encryption**: Sensitive config fields are Fernet-encrypted at rest (AES-128-CBC + HMAC-SHA256). Add new secret field names to `SENSITIVE_KEYS` in `modules/credential_store.py`. Key stored in user-specific dotfile outside the repo (`%APPDATA%/n5zy-copilot/.credential_key` on Windows, `~/.config/n5zy-copilot/.credential_key` on Linux/Mac).

### Testing Considerations
- GPS: Use VK172 USB dongle on Windows COM port
- APRS: Test with aprs.fi and APRSdroid on phone
- WSJT-X: Multiple instances on different UDP ports
- N1MM+: TCP connection for ROVERQTH commands

### Common Issues
- `modules/__init__.py` must not import deleted modules
- GPS Logger needs `get_full_data()` method in gps_monitor.py
- APRS-IS echoes packets back - filter own callsign to avoid duplicates
- APRS ack messages should be handled silently, not displayed
- GPS Time Sync can enter feedback loop if `gps_datetime_utc` becomes stale — safety guards in `sync_system_clock()` prevent this (do not remove them)
- Log4OM requires FT8/FT4 as primary mode (not MFSK+submode) — see `_build_adif_record()` in radio_updater.py
- WSJT-X ALL.TXT frequency extraction must cover full HF range (1.8-30000 MHz), not just 28+ — see `log_monitor.py`

## Feature Checklist
See `FEATURES.md` for detailed feature status and version history.

## Recent Changes (Mar 2025)
- **v1.9.10**: GPS Time Sync — forward-only corrections with 5-sample median averaging to prevent WSJT-X audio desync
- **v1.9.9**: ALL.TXT decode pipeline — extract mode (FT8/FT4/Q65) for DX3 mode/band_mode granularity checks
- **v1.9.8**: Bison icon, LoTW SSL fix bump
- **v1.9.7**: Daily DX alert suppression — suppress DX!/DX2/DX3 for already-worked stations (21-day ADIF lookback)
- **v1.9.6**: Fix LoTW SSL certificate error — load Windows cert store for ARRL trust chain
- **v1.9.5**: PSK Monitor — change '--' priority to 'P5' for sorting, HF-aware propagation (GW/Sky instead of Sp-E/Tropo)
- **v1.9.4**: Fix Priority pane nearby callsign — blank when not hearing DX station directly
- **v1.9.3**: PSK Monitor fixes — Clear Alerts resets cooldowns, Priority pane dedup, DX2/DX3 from ALL.TXT decodes, "--" sorts as P6, Priority Alerts 30-min auto-aging
- **v1.9.2**: Priority SMS transmitter detection, per-station 5-min SMS cooldown, PSK Monitor fast first-poll retry
- **v1.9.1**: Credential encryption (Fernet), APRS SSID routing fix
- **v1.9.0**: SMS Notify tab (Twilio SMS, Slack, APRS nearby broadcast), GPS time sync safety guards, voice alert category split
- **v1.8.58**: Fix hardcoded band in New Grid alerts, fix FSQCALL mode in Log4OM
- **v1.8.57**: GPS baud/update rate control, GPS time sync, voice category filtering, PSK Entity column
- **v1.8.56**: Priority Station Alerts (DX!/DX2/DX3), PSK Monitor redesign, Log4OM integration, DXCC lookup

## Hardware Setup
- **GPS**: VK172 USB dongle (9600 baud NMEA, configurable baud/update rate)
- **Battery**: Victron SmartShunt via BLE
- **Radios**: IC-9700, IC-7610, IC-7300 with WSJT-X
- **Vehicle**: Kia Niro EV (affects route planning for charging)
- **Antennas**: M2 beams, precision horn antennas (6m through 3cm)
