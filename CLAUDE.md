# N5ZY VHF Contest Co-Pilot

## Project Overview
A Python/tkinter application for amateur radio VHF/UHF contest roving operations. Integrates GPS tracking, multiple WSJT-X instances, contest logging (N1MM+/N3FJP), battery monitoring, APRS messaging, and various contest aids into a single dashboard.

**Author:** Marcus, N5ZY  
**License:** Open source for amateur radio use

## Quick Start
```bash
python copilot.py
```

## Project Structure
```
N5ZY-CoPilot/
├── copilot.py                 # Main application (5600+ lines)
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
│   └── county_lookup.py       # GPS to county lookup (Census API)
├── tools/
│   └── parse_public_logs.py   # 3830scores scraper (used by QSY Advisor)
└── qsy_advisor_data/
    └── station_bands.json     # Station database
```

## Key Dependencies
```bash
pip install pyserial pynmea2 pyttsx3 requests beautifulsoup4 aprslib
pip install victron-ble bleak  # For Victron battery monitoring
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
4. **Voice alerts**: Use `self.voice.announce()` for hands-free operation while driving

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

## Feature Checklist
See `FEATURES.md` for detailed feature status and version history.

## Recent Changes (Feb 2025)
- Tab styling: bold active tab, spacing for mobile visibility
- GPS Logger: live display every 2 seconds
- APRS Messages: connection status updates, message filtering
- APRS: filter own position beacons, allow cross-SSID messages, filter acks
- Removed: fips_counties.py (replaced by county_lookup.py + JSON mappings)

## Hardware Setup
- **GPS**: VK172 USB dongle (9600 baud NMEA)
- **Battery**: Victron SmartShunt via BLE
- **Radios**: IC-9700, IC-7610, IC-7300 with WSJT-X
- **Vehicle**: Kia Niro EV (affects route planning for charging)
- **Antennas**: M2 beams, precision horn antennas (6m through 3cm)
