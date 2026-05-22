# N5ZY VHF Contest and State QSO Party Co-Pilot

A Python/tkinter dashboard for amateur radio VHF/UHF contest roving and state QSO party mobile operation. Integrates GPS tracking, multiple WSJT-X instances, contest logging (N1MM+/N3FJP), Victron SmartShunt bluetooth battery monitoring, APRS messaging, PSK Reporter, and voice alerts into a single application designed for hands-free mobile operation.

**Author:** Marcus, N5ZY, with full state QSO coverage and other tweaks by Rus, K2UA

## Features

- **GPS Tracking** - VK172 USB dongle, automatic grid square and county detection, grid boundary proximity alerts
- **GPS Time Sync** - Set system clock from GPS with safety guards (freshness check, offset limit, rate limiting)
- **Multi-Radio WSJT-X** - Monitor up to 4 WSJT-X instances simultaneously via UDP, updates 'My Grid' automatically through AutoGrid
- **Contest Logging** - N1MM+ (TCP/RoverQTH), N3FJP, and Log4OM integration with ADIF output
- **SMS Notifications** - Twilio SMS alerts for DX/New Grid, rover status broadcasts to subscriber list
- **APRS** - APRS-IS messaging, position beaconing, nearby mobile station alerts with voice announcements
- **PSK Reporter** - Band opening detection, Sporadic-E alerts, multi-hop Sp-E "PULL OVER!" alerts, priority alert aging
- **Priority Station Alerts** - DX! priority stations, DX2 (new DXCC entity), DX3 (new DXCC on band) with LoTW/cty.dat lookup, dynamic detection from ALL.TXT decodes
- **QSY Advisor** - Station database to find who's active on which bands
- **Voice Alerts** - Hands-free announcements for grid changes, QSOs, band openings, nearby stations
- **Slack Integration** - Webhook notifications to multiple Slack channels
- **Battery Monitor** - Victron SmartShunt via Bluetooth LE
- **Contest Modes** - VHF Contest, 222 and Up, QSO Party (with county auto-detection)
- **Super Check Partial** - Callsign lookup with QRZ.com fallback
- **GPS Logger** - Track recording, waypoint annotations for rover scouting
- **KML QSO Party Boundaries** - Contest-specific county/region boundary lookups using KML data from the [qsopartytracker](https://github.com/geoffeg/qsopartytracker) project

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
pip install cryptography          # Credential encryption in settings.json
pip install victron-ble bleak     # Victron battery monitoring
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

## County & Region Boundary Lookup

The copilot supports two complementary boundary data sources for determining your county/region from GPS coordinates:

### Census Shapefile (FIPS-based)

The traditional US Census TIGER/Line shapefile provides authoritative county boundaries with FIPS codes. This is used for general county identification (e.g., ADIF logging with `MY_CNTY`) and works nationwide regardless of whether a specific QSO party is active.

- **Data**: `data/us_counties_10m.shp` (Natural Earth 10m resolution)
- **Output**: County name, state, full FIPS code (e.g., `40109` for Oklahoma County, OK)
- **Library**: `pyshp` + `shapely` STRtree spatial index

### KML Contest Boundaries (Exchange-based)

Contest-specific KML boundary files provide the correct **contest exchange abbreviations** used by each QSO party. These files originate from the [qsopartytracker](https://github.com/geoffeg/qsopartytracker) project by Geoff, KE5FPG, and cover all active state QSO parties and multi-state events.

- **Data**: `data/kml_maps/` (60 KML files)
- **Output**: County/region name + contest exchange abbreviation (e.g., `Canadian` → `CAN` for OKQP)
- **Library**: Python stdlib `xml.etree.ElementTree` + `shapely` STRtree spatial index
- **No additional pip dependencies** beyond what's already in `requirements.txt`

#### Supported Contests

| Contest | ID | Files | Notes |
|---------|-----|-------|-------|
| Alabama QSO Party | ALQP | 1 | 67 counties |
| Arizona QSO Party | AZQP | 1 | 15 counties |
| Arkansas QSO Party | ARQP | 1 | 75 counties |
| California QSO Party | CQP | 1 | 58 counties |
| Colorado QSO Party | COQP | 1 | 64 counties |
| Connecticut QSO Party | CTQP | 1 | 8 counties |
| Delaware QSO Party | DEQP | 1 | 3 counties |
| Florida QSO Party | FQP | 1 | 67 counties |
| Georgia QSO Party | GAQP | 1 | 159 counties |
| Hawaii QSO Party | HIQP | 1 | 9 counties |
| Idaho QSO Party | IDQP | 1 | 44 counties |
| Illinois QSO Party | ILQP | 1 | 102 counties |
| Indiana QSO Party | INQP | 1 | 92 counties |
| Iowa QSO Party | IAQP | 1 | 99 counties |
| Kansas QSO Party | KSQP | 1 | 105 counties |
| Kentucky QSO Party | KYQP | 1 | 120 counties |
| Louisiana QSO Party | LAQP | 1 | 64 parishes |
| Maine QSO Party | MEQP | 1 | 16 counties |
| Maryland QSO Party | MDQP | 2 | 25 counties + DC |
| Massachusetts QSO Party | MAQP | 1 | 14 counties |
| Michigan QSO Party | MIQP | 1 | 83 counties |
| Minnesota QSO Party | MNQP | 1 | 87 counties |
| Mississippi QSO Party | MSQP | 1 | 82 counties |
| Missouri QSO Party | MOQP | 1 | 115 counties |
| Montana QSO Party | MTQP | 1 | 56 counties |
| Nebraska QSO Party | NEQP | 1 | 93 counties |
| Nevada QSO Party | NVQP | 1 | 17 counties |
| New Hampshire QSO Party | NHQP | 1 | 10 counties |
| New Jersey QSO Party | NJQP | 1 | 21 counties |
| New Mexico QSO Party | NMQP | 1 | 33 counties |
| New York QSO Party | NYQP | 1 | 62 counties |
| North Carolina QSO Party | NCQP | 1 | 100 counties |
| North Dakota QSO Party | NDQP | 1 | 53 counties |
| Ohio QSO Party | OHQP | 1 | 88 counties |
| Oklahoma QSO Party | OKQP | 1 | 77 counties |
| Ontario QSO Party | ONQP | 1 | 50 regions |
| Oregon QSO Party | ORQP | 1 | 36 counties |
| Pennsylvania QSO Party | PAQP | 1 | 67 counties |
| Rhode Island QSO Party | RIQP | 1 | 5 counties |
| South Carolina QSO Party | SCQP | 1 | 46 counties |
| South Dakota QSO Party | SDQP | 1 | 66 counties |
| Tennessee QSO Party | TNQP | 1 | 95 counties |
| Texas QSO Party | TQP | 1 | 254 counties |
| Utah QSO Party | UTQP | 1 | 29 counties |
| Vermont QSO Party | VTQP | 1 | 14 counties |
| Virginia QSO Party | VAQP | 1 | 134 counties/cities |
| Washington Salmon Run | PRIOR | 1 | 39 counties |
| West Virginia QSO Party | WVQP | 1 | 55 counties |
| Wisconsin QSO Party | WIQP | 1 | 72 counties |
| Wyoming QSO Party | WYQP | 1 | 23 counties |
| **7th Call Area QSO Party** | **7QP** | **7** | AZ, ID, MT, NV, OR, UT, WA, WY (state-prefixed abbreviations) |
| **New England QSO Party** | **NEWE** | **1** | CT planning regions (state-prefixed abbreviations) |

#### KML Name Format

Each KML `<Placemark>` uses the naming convention:

```
CountyName=ABBREVIATION N
```

- **CountyName**: Human-readable name (e.g., `Adair`, `ContraCosta`, `CT Capital`)
- **ABBREVIATION**: Contest exchange value sent/received during the contest (e.g., `ADA`, `CCOS`, `CTCAP`)
- **N**: Polygon part number for multi-polygon counties (merged automatically)

Examples:
- State QSO Party: `Adair=ADA 1` (Oklahoma)
- 7QP: `Adams=WAADA` (state prefix + abbreviation)
- NEWE: `CT Capital=CTCAP` (state prefix + region abbreviation)

#### Unified Lookup API

The `UnifiedLookupService` combines both data sources into a single query:

```python
from modules.county_lookup import UnifiedLookupService

service = UnifiedLookupService()
service.load_shapefile("data/us_counties_10m.shp")
service.load_kml_directory("data/kml_maps")

# Single lookup returns FIPS + contest exchange
result = service.lookup(35.4676, -97.5164, contest="OKQP")
print(result.county_name)           # "Oklahoma"
print(result.fips)                  # "40109"
print(result.exchange)              # "OKL"
print(result.state_abbrev)          # "OK"
print(result.contest)               # "OKQP"

# Find all contest regions at a position
results = service.lookup_all_contests(47.6062, -122.3321)
# Returns matches from WAQP, 7QP, Salmon Run, etc.
```

You can also use the KML service standalone:

```python
from modules.kml_lookup import KMLLookupService

service = KMLLookupService()
service.load_kml("data/kml_maps/OverlayOklahomaRev3.kml", contest="OKQP")

result = service.lookup(35.4676, -97.5164)
print(f"{result.county_name} ({result.abbreviation})")  # "Oklahoma (OKL)"
```

#### Performance

- **Load time**: All 60 KML files (3,462 regions) load in ~1.5 seconds
- **Lookup time**: Sub-millisecond per query (typically 0.04-0.2ms) using STRtree spatial indexing
- **Memory**: Moderate — all polygon geometries held in memory for fast lookups

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
  county_lookup.py      # Shapefile county lookup + UnifiedLookupService
  kml_lookup.py         # KML QSO Party boundary parser + KMLLookupService
  credential_store.py   # Fernet encryption for sensitive config fields
  priority_engine.py    # DX!/DX2/DX3 priority station detection
  lotw_client.py        # LoTW user activity lookup
  cty_lookup.py         # cty.dat DXCC entity lookup
tools/
  parse_public_logs.py  # 3830scores scraper for QSY Advisor database
  build_database.py     # Station database builder
  import_cabrillo.py    # Cabrillo log importer
data/
  station_bands.json    # QSY Advisor station database
  dxcc_entities.json    # DXCC entity database
  cty.dat               # Country/DXCC prefix file
  us_counties_10m.shp   # US Census county boundaries (shapefile)
  us_counties_10m.dbf   # Shapefile attribute table
  us_counties_10m.shx   # Shapefile spatial index
  us_counties_10m.cpg   # Shapefile character encoding
  county_mappings/      # FIPS to county code mappings (JSON)
  kml_maps/             # QSO Party contest boundary KMLs (60 files)
```

## Acknowledgments

- KML county boundary data from the [qsopartytracker](https://github.com/geoffeg/qsopartytracker) project by KC8FDU and K2UA — covering all US state QSO parties, 7QP, New England QSO Party (NEWE), and Salmon Run.
- Contest abbreviations follow N1MM+ naming conventions where applicable.

## License

Open source for amateur radio use. 73 de N5ZY!
