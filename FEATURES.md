# N5ZY Co-Pilot Feature Checklist

This file documents all working features. Use this to verify nothing gets broken during updates.

## UI/Visual
- [x] Tab labels have padding/spacing for easier reading while mobile
- [x] Selected/active tab is **bold** for quick identification
- [x] Tab styling optimized for visibility in bright sunlight

## Settings Tab

### GPS Settings
- [ ] GPS COM Port is a **dropdown/Combobox** (not text Entry)
- [ ] "Refresh Ports" button scans for available COM ports
- [ ] **"Connect" button reconnects GPS without app restart**
- [ ] Grid Boundary Alerts checkbox toggles voice alerts
- [ ] GPS lock status shows in main status bar

### Data Files
- [ ] Super Check Partial file path entry
- [ ] "Download" button fetches from supercheckpartial.com
- [ ] "Browse" button for local file selection
- [ ] "Reload" button reloads current file
- [ ] Shows count of loaded callsigns
- [ ] **QRZ credentials (username/password) for fallback lookup**

### Contest Mode
- [ ] VHF Contest / 222 and Up / QSO Party radio buttons
- [ ] QSO Party shows county selector when selected
- [ ] QSOParty.sec file browse/reload
- [ ] County dropdown populates from selected party
- [ ] **Switching to QSO Party mode auto-detects county from GPS**
- [ ] **Changing QSO party re-detects county with new abbreviations**

### Station Info
- [ ] My Callsign field (auto-uppercase)

### Victron SmartShunt
- [ ] BLE Address field
- [ ] Encryption Key field
- [ ] Discover Devices button

### Contest Logger
- [ ] N1MM+ / N3FJP dropdown
- [ ] N1MM+ TCP port setting
- [ ] N3FJP API port setting

### WSJT-X Instances
- [ ] Multiple instance configuration (name, port, path)
- [ ] Add/remove instance buttons

### APRS-IS Settings
- [ ] Enable checkbox
- [ ] Callsign, passcode, SSID fields
- [ ] Beacon interval setting

### Slack Notifications
- [ ] Multiple webhook configuration
- [ ] Test webhooks button

## Manual Entry Tab

- [ ] **Band dropdown - shows bands from "My Bands" setting (all modes)**
- [ ] Mode radio buttons (USB/LSB/FM/CW)
- [ ] Frequency auto-fills based on band/mode
- [ ] Callsign field (auto-uppercase)
- [ ] **Super Check Partial panel on right side**
  - [ ] Shows matches as you type (updates on each keystroke)
  - [ ] Double-click or Enter fills callsign
  - [ ] Shows match count
  - [ ] **Includes callsigns from QSO Log marked as "(Worked)"**
  - [ ] **QRZ fallback when no SCP matches (requires QRZ credentials in Settings)**
- [ ] **Their Grid/Exchange field - label changes based on contest mode:**
  - [ ] "Their Grid:" in VHF/222up mode - **validates 4-6 char grid**
  - [ ] "Their Exchange:" in QSO Party mode - **accepts any text (state, serial, etc.)**
- [ ] **My Grid/County field - label changes based on contest mode:**
  - [ ] "My Grid:" in VHF/222up mode (auto-filled from GPS)
  - [ ] "My County:" in QSO Party mode (auto-filled from GPS county detection)
- [ ] RST Sent/Rcvd fields (defaults change for CW)
- [ ] Log QSO button
- [ ] Clear Form button

## QSO Log Tab

- [ ] Treeview shows: Time, Call, Grid, Band, Mode, My Grid, Source
- [ ] Delete button removes from display AND correct ADIF file
- [ ] Delete matches on call + band + TIME (not just call+band)
- [ ] Delete works on reloaded QSOs from previous days
- [ ] List sorts by time after delete (newest first)
- [ ] Reload Contest Log button
- [ ] Clear Display button

## Status Bar (Top)

- [ ] **GPS lock indicator (● symbol)** - green when locked, red when lost
- [ ] Grid display (large font) - label: "Grid:"
- [ ] County display (QSO Party mode) - label: "Cnty:"
- [ ] Battery voltage/current/SOC - label: "Bat:"
- [ ] WSJT-X instance status indicators
- [ ] APRS checkbox (toggles APRS beaconing)
- [ ] PSK checkbox (toggles PSK Reporter monitoring)
- [ ] Logger button (Send to N1MM+/N3FJP)

## ADIF Logging

- [ ] No duplicate entries (WSJT-X raw disabled)
- [ ] **No duplicate entries from CoPilot manual QSOs echoed back from N1MM+**
- [ ] WSJT-X QSOs include MY_STATE, MY_CNTY, MY_LAT, MY_LON, MY_GRIDSQUARE
- [ ] Mode/submode correct: FT8→MFSK+FT8, Q65→MFSK+Q65, USB→SSB+USB
- [ ] Daily log files: n5zy_copilot_YYYYMMDD.adi

## Process Monitoring

- [ ] N1MM+ process monitor (resends grid/county on restart)
- [ ] jt9.exe process monitor (resends grid on WSJT-X restart)
- [ ] Console shows "N1MM+ restarted!" when detected

## GPS & Location

- [ ] **GPS monitor calls callback every 5 seconds (not just on grid change)** - CRITICAL for county detection
- [ ] Grid square updates sent to all WSJT-X instances
- [ ] Grid sent to N1MM+ via RoverQTH
- [ ] County auto-detection from GPS coordinates (shapefile)
- [ ] County sent to N1MM+ in QSO Party mode
- [ ] Grid boundary voice alerts (5mi, 2mi, 1mi, 100yd, 50yd)

## Voice Alerts

- [ ] Grid change announcements
- [ ] County change announcements
- [ ] QSO logged announcements
- [ ] Grid boundary proximity alerts
- [ ] Battery low warnings
- [ ] **GPS lock acquired/lost announcements**
- [ ] **PSK Monitor alerts:**
  - [ ] Band openings
  - [ ] Sporadic-E (Sp-E) on 2m/70cm
  - [ ] Multi-hop Sporadic-E (MSp-E) - "PULL OVER!"
  - [ ] Unusual mode activity (Q65, MSK144, FT4)

## QSY Advisor Tab

- [ ] Station database loaded
- [ ] Search/filter by callsign
- [ ] Filter by grid
- [ ] Shows bands each station operates

## PSK Reporter Tab

- [ ] Enable/disable monitoring
- [ ] **Uses "My Bands" setting (not hardcoded)**
- [ ] Shows nearby activity (VHF and HF based on bands)
- [ ] Configurable VHF and HF radius
- [ ] Dynamic frequency range based on selected bands

## APRS Messages Tab

- [x] **Connection status display** - Shows "Connected as N5ZY-9" when connected
- [x] Received messages inbox (treeview)
- [x] Compose area with To: callsign and Message fields
- [x] Send button (also sends on Enter key)
- [x] Quick reply buttons (73, QSL, QRV?, QSY?, TNX)
- [x] **Voice alert on received message**
- [x] Auto-fills reply To: field from received message
- [x] **Filters out echoed messages from self** (APRS-IS echo)
- [x] **Filters out ack/rej messages** (handled silently)
- [x] **Allows cross-SSID messages** (N5ZY phone → N5ZY-9 CoPilot)
- [ ] Clear Messages button

## APRS Nearby Station Alerts

- [x] Alerts when mobile stations are within radius
- [x] **Filters out own callsign** (any SSID) from alerts
- [x] Cooldown timer prevents spam (default 5 min)
- [x] **Duplicate packet filtering** (same-second APRS-IS dupes)
- [x] Voice announcement with callsign, distance, bearing
- [x] Recognizes mobile symbols (Car, Truck, Van, RV, etc.)
- [x] Ignores infrastructure (digipeaters, weather stations)

## GPS Logger Tab

- [x] **Live GPS data display (updates every 2 seconds)**
- [x] **Extended GPS data via get_full_data() method**
- [ ] **Track file recording (CSV format)**
  - [ ] Save path and filename selection
  - [ ] Start/Stop/Pause track buttons
  - [ ] Recording status display
- [x] **GPS Data Display:**
  - [x] Position (lat/lon)
  - [x] 6-character Maidenhead grid
  - [x] State: County
  - [x] Altitude (feet and meters)
  - [x] GPS Time (UTC)
  - [x] Satellites tracked
  - [x] Accuracy (HDOP with quality rating)
  - [x] Heading (degrees and compass direction)
  - [x] Speed (current mph)
  - [x] Average Speed (mph)
  - [x] Distance traveled (miles)
  - [ ] Points logged count
- [ ] **Annotations/Waypoints:**
  - [ ] Quick waypoint buttons (12 presets for rover scouting):
    - Elev. Overpass, Elev. Exit Ramp, Elev. Shoulder, Elev. Viewpoint
    - Utility Driveway, Field Driveway, Mesa Edge, Elev. Parking lot
    - School, Elev. Cemetery, Rest Area, Elev. Rest Area
  - [ ] Custom annotation entry
  - [ ] Recent waypoints list
  - [ ] Voice confirmation on waypoint add

---

## Version History

- **1.8.55** - APRS: duplicate packet filtering, cooldown logic fix
- **1.8.54** - APRS: allow cross-SSID messages (N5ZY → N5ZY-9), filter ack/rej
- **1.8.53** - APRS: filter own callsign from position alerts, filter echoed messages
- **1.8.52.1** - Fix: modules/__init__.py removed fips_counties import, tab styling for mobile visibility
- **1.8.52** - GPS Logger tab: track recording, extended GPS data, waypoint annotations
- **1.8.51** - APRS Messages tab: send/receive APRS messages with voice alerts
- **1.8.50** - PSK Monitor uses "My Bands" setting, dynamic frequency range, SCP includes worked calls
- **1.8.49** - Status bar: GPS lock indicator (●), PSK checkbox, shortened labels (Grid/Bat/Cnty)
- **1.8.48** - QRZ.com login debug output, fixed XML element deprecation warnings
- **1.8.47** - QRZ.com fallback lookup when SCP has no matches (requires QRZ XML subscription)
- **1.8.46** - Manual Entry: band dropdown always uses "My Bands" setting (not all bands)
- **1.8.45** - Manual Entry: exchange validation allows any text in QSO Party, my_grid/county auto-fills correctly
- **1.8.44** - **CRITICAL FIX**: GPS monitor now calls callback every 5 sec (not just on grid change) - fixes county detection while driving
- **1.8.42** - County auto-detect on mode/party change, GPS reconnect button
- **1.8.41** - GPS Connect button, COM port dropdown restored
- **1.8.40** - GPS COM port dropdown with Refresh
- **1.8.39** - SCP Download button
- **1.8.38** - Super Check Partial integration
- **1.8.37** - ADIF delete by source file, sort after delete
- **1.8.36** - ADIF fixes (duplicates, GPS fields, mode/submode)
