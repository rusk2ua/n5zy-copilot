# N5ZY Co-Pilot County Auto-Detection Update

## Version: 1.8.32 (County + ADIF Stamping + /R Rover Fix)

This update adds automatic county detection from GPS, GPS-stamped ADIF records for LoTW upload, and the /R rover DXCC fix for Log4OM.

## New Features

### Automatic County Detection (QSO Party Mode)
- Detects county from GPS coordinates using Census shapefile
- Voice announces county changes: "County change. Now in {county name}"
- Automatically updates N1MM+ via RoverQTH when county changes
- Works alongside existing grid change detection

### GPS-Stamped ADIF Records (VHF Contests & QSO Parties)
Every QSO written to the Co-Pilot ADIF file now includes:
- `MY_STATE` - State abbreviation (e.g., "OK")
- `MY_CNTY` - County name (e.g., "Oklahoma") 
- `MY_LAT` / `MY_LON` - GPS coordinates in ADIF format
- `MY_GRIDSQUARE` - 6-char grid from GPS
- `MY_CQ_ZONE` - CQ Zone (default: 4)
- `MY_ITU_ZONE` - ITU Zone (default: 7)
- `MY_DXCC` - DXCC entity (291 = USA)
- `MY_COUNTRY` - "United States"
- `CONTEST_ID` - Mapped to ADIF standard (e.g., "ARRL-VHF-JAN")

**Why this matters:** When you import these QSOs into Log4OM and upload to LoTW, 
all your rover QSOs will have the correct location data for each QSO!

### /R Rover DXCC Fix (Log4OM Compatibility)
**Problem:** Log4OM was misidentifying /R rover callsigns as European Russia.

**Solution:** When the contacted station has a /R suffix (e.g., W5ABC/R), 
the ADIF record now includes explicit DXCC fields:
- `DXCC` = 291 (United States)
- `CQZ` = 4 (CQ Zone)
- `ITUZ` = 7 (ITU Zone)  
- `COUNTRY` = "United States"
- `PFX` = Derived prefix (e.g., "W5" from W5ABC/R)

This prevents Log4OM from misinterpreting the /R suffix and ensures proper 
LoTW matching for rover-to-rover QSOs.

### How It Works
1. GPS position is continuously tracked
2. When any QSO is logged (WSJT-X, Manual Entry, Grid Corner):
   - County lookup determines your current state/county
   - ADIF record is stamped with full location data
   - /R callsigns get explicit DXCC fields
3. Import `logs/n5zy_copilot_YYYYMMDD.adi` into Log4OM
4. Sign and upload to LoTW with accurate per-QSO locations

## Data Flow

```
GPS → Shapefile → FIPS "40109" → OK.json → "OKL" → N1MM ROVERQTH
                              → "Oklahoma" → ADIF MY_CNTY → LoTW

WSJT-X QSO → RadioUpdater → LocationStamper → ADIF with MY_* fields
                                           → /R check → DXCC fields
```

## New/Updated Files

### modules/county_lookup.py (NEW)
Python port of the C# CountyLookupService. Uses:
- `pyshp` - Read shapefiles
- `shapely` - Point-in-polygon geometry
- STRtree spatial index for sub-millisecond lookups

### modules/radio_updater.py (UPDATED)
- Added `location_stamper` callback for GPS stamping
- Enhanced `_build_adif_record()` with full MY_* fields
- Added `/R` rover DXCC fix (prevents Log4OM European Russia bug)
- Added `to_adif_latitude()` / `to_adif_longitude()` helpers
- Added `map_contest_id()` for ADIF standard contest IDs
- Added `_derive_prefix()` for PFX field
- Updated ADIF header format

### copilot.py (UPDATED)  
- Added `_stamp_qso_location()` method
- Tracks `current_lat` / `current_lon` from GPS
- Stamps all QSOs before ADIF write (WSJT-X, Manual, Grid Corner)
- County auto-detection in QSO Party mode

### data/us_counties_10m.shp (+ .dbf, .shx, .cpg)
Simplified US county boundaries (10m accuracy, 23MB vs 129MB full TIGER).
Extracted from Census TIGER/Line tl_2025_us_county.shp.

### data/county_mappings/OK.json
FIPS code to Oklahoma QSO Party abbreviation mapping.
Add more state files as needed for other QSO parties.

## Installation

1. Copy `modules/county_lookup.py` to your `modules/` folder
2. Copy `modules/radio_updater.py` to your `modules/` folder (overwrites existing)
3. Copy `data/us_counties_10m.*` files to your `data/` folder  
4. Copy `data/county_mappings/OK.json` to `data/county_mappings/`
5. Replace your `copilot.py` with the updated version
6. Install new dependencies:
   ```
   pip install pyshp shapely
   ```

## New Config Options

In `config/settings.json`:
- `county_shapefile`: Path to shapefile (default: `data/us_counties_10m.shp`)
- `county_auto_detect`: Enable/disable auto-detection (default: `true`)

## UI Changes

In Settings → Contest Mode → QSO Party:
- New checkbox: "Auto-detect county from GPS"
- Status shows if shapefile is loaded

## Sample ADIF Output

### Normal QSO:
```
<call:5>W5ABC <qso_date:8>20260115 <time_on:6>143052 <band:2>2m <mode:3>FT8 
<freq:10>144.200000 <rst_sent:3>-10 <rst_rcvd:3>-08 <gridsquare:4>EM25 
<station_callsign:6>N5ZY/R <operator:6>N5ZY/R <owner_callsign:4>N5ZY 
<my_gridsquare:6>EM15fp <my_state:2>OK <my_cnty:8>Oklahoma 
<my_lat:11>N035 28.056 <my_lon:11>W097 30.984 <my_country:13>United States 
<my_cq_zone:1>4 <my_itu_zone:1>7 <my_dxcc:3>291 <programid:12>N5ZY-CoPilot <eor>
```

### Rover-to-Rover QSO (with /R fix):
```
<call:7>W5ABC/R <qso_date:8>20260115 ... <gridsquare:4>EM25 
<dxcc:3>291 <cqz:1>4 <ituz:1>7 <country:13>United States <pfx:2>W5 
<station_callsign:6>N5ZY/R ... <eor>
```

## Notes

- ADIF stamping works for ALL contest modes (VHF, 222 Up, QSO Party)
- County auto-detect only activates in QSO Party mode
- /R rover fix applies to ALL modes (VHF contests especially)
- Falls back gracefully if shapefile not found
- Sub-millisecond lookups won't affect performance

## Credits

Based on C# implementation from N5ZY.CoPilot.GeoLocation project.
Shapefile preprocessing using NetTopologySuite/ShapefilePreprocessor.
/R rover fix based on Log4OM DXCC lookup behavior analysis.

73 de N5ZY
