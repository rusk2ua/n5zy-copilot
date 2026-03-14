# N5ZY VHF Contest Co-Pilot

A Python/tkinter dashboard for amateur radio VHF/UHF contest roving. Integrates GPS tracking, multiple WSJT-X instances, contest logging (N1MM+/N3FJP), battery monitoring, APRS messaging, PSK Reporter, and voice alerts into a single application designed for hands-free mobile operation.

**Author:** Marcus, N5ZY

## Features

- **GPS Tracking** - VK172 USB dongle, automatic grid square and county detection, grid boundary proximity alerts
- **GPS Time Sync** - Set system clock from GPS with safety guards (freshness check, offset limit, rate limiting)
- **Multi-Radio WSJT-X** - Monitor up to 4 WSJT-X instances simultaneously via UDP, updates 'My Grid' automatically through AutoGrid
- **Contest Logging** - N1MM+ (TCP/RoverQTH), N3FJP, and Log4OM integration with ADIF output
- **SMS Notifications** - Twilio SMS alerts for DX/New Grid, rover status broadcasts to subscriber list
- **APRS** - APRS-IS messaging, position beaconing, nearby mobile station alerts with voice announcements
- **PSK Reporter** - Band opening detection, Sporadic-E alerts, multi-hop Sp-E "PULL OVER!" alerts
- **Priority Station Alerts** - DX! priority stations, new DXCC entity, new DXCC on band with LoTW/cty.dat lookup
- **QSY Advisor** - Station database to find who's active on which bands
- **Voice Alerts** - Hands-free announcements for grid changes, QSOs, band openings, nearby stations
- **Slack Integration** - Webhook notifications to multiple Slack channels
- **Battery Monitor** - Victron SmartShunt via Bluetooth LE
- **Contest Modes** - VHF Contest, 222 and Up, QSO Party (with county auto-detection)
- **Super Check Partial** - Callsign lookup with QRZ.com fallback
- **GPS Logger** - Track recording, waypoint annotations for rover scouting

## Requirements

- Python 3.8+
- Windows (uses tkinter, COM ports, pyttsx3 for TTS)

## Installation

```bash
git clone https://github.com/N5ZY/n5zy-copilot.git
cd n5zy-copilot
pip install -r requirements.txt
```

### Additional dependencies (optional)

```bash
pip install victron-ble bleak     # Victron battery monitoring
pip install requests beautifulsoup4  # PSK Reporter, QRZ lookups
pip install aprslib               # APRS-IS messaging
```

## Quick Start

```bash
python copilot.py
```

On first run, a default `config/settings.json` is created. Configure your settings in the Settings tab:

1. Set your **callsign**
2. Select your **GPS COM port** and click Connect
3. Configure your **WSJT-X instances** (name, UDP port, log path)
4. Choose your **contest logger** (N1MM+ or N3FJP)
5. Set your **active bands**

## Configuration

Settings are stored in `config/settings.json` (not committed - contains credentials). See `config/settings.json.template` for the format.

Key settings:
- `my_call` - Your callsign
- `gps_port` - GPS dongle COM port
- `wsjt_instances` - Array of WSJT-X instances with name, log_path, and udp_port
- `my_bands` - Bands you operate (used by Manual Entry, PSK Monitor, QSY Advisor)
- `aprs_callsign` - Your APRS-IS callsign with SSID
- `contest_logger` - `"n1mm"` or `"n3fjp"`

## Hardware

This was built for a specific rover setup but should adapt to similar configurations:

- **GPS**: VK172 USB dongle (9600 baud NMEA)
- **Battery**: Victron SmartShunt via BLE
- **Radios**: Icom IC-9700, IC-7610, IC-7300 running WSJT-X
- **Antennas**: M2 beams, precision horn antennas (6m through 3cm)

## Project Structure

```
copilot.py              # Main application
config/
  settings.json.template  # Configuration template
modules/
  gps_monitor.py        # GPS serial port reader
  battery_monitor.py    # Victron SmartShunt BLE
  radio_updater.py      # WSJT-X UDP & N1MM/N3FJP integration
  log_monitor.py        # WSJT-X log file watcher
  voice_alerts.py       # Text-to-speech (pyttsx3)
  aprs_client.py        # APRS-IS messaging & nearby station alerts
  qsy_advisor.py        # Station database browser
  grid_boundary.py      # Grid edge proximity alerts
  psk_monitor.py        # PSK Reporter integration
  qsoparty_parser.py    # QSO Party .sec file parser
  county_lookup.py      # GPS to county lookup (Census API)
tools/
  parse_public_logs.py  # 3830scores scraper for QSY Advisor database
data/
  station_bands.json    # QSY Advisor station database
  county_mappings/      # FIPS to county code mappings
```

## License

Open source for amateur radio use. 73 de N5ZY!
