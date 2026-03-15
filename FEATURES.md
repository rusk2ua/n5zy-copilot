# N5ZY Co-Pilot Feature Checklist

This file documents all working features. Use this to verify nothing gets broken during updates.

## UI/Visual
- [x] Tab labels have padding/spacing for easier reading while mobile
- [x] Selected/active tab is **bold** for quick identification
- [x] Tab styling optimized for visibility in bright sunlight

## Status Bar (Top)

- [x] **GPS lock indicator (● symbol)** - green when locked, red when lost
- [x] Grid display (large font) - label: "Grid:"
- [x] Battery voltage/current/SOC - label: "Bat:"
- [x] WSJT-X instance status indicators
- [x] County display (large font) - label: "Cnty:"
- [x] PSK checkbox (toggles PSK Reporter monitoring)
- [x] APRS checkbox (toggles APRS beaconing)
- [x] Grid/County Update button (Send RoverQTH to N1MM+/N3FJP and send Grid to each WSJT-X instance)

## Settings Tab

### Contest Mode
- [x] VHF Contest / 222 and Up / QSO Party dropdown box
- [x] QSO Party shows state and county selectors when selected as the chosen contest
- [x] QSOParty.sec file browse/reload from N1MM
- [x] County dropdown populates from selected QSO Party and the Counties within that contest
- [x] **Switching to QSO Party mode auto-detects county from GPS**
- [x] **Changing QSO party re-detects county with new abbreviations**
- [x] GPS detected County sent to N1MM+ as RoverQTH is indicated below the County and reflected in the "Send to N1MM+" button of the status bar.

### Station Info
- [x] My Callsign field (auto-uppercase) to be used for ADIF logging, etc

### Data Files
- [x] Super Check Partial file path entry
- [x] "Download" button fetches from supercheckpartial.com
- [x] "Browse" button for local file selection
- [x] "Reload" button reloads current file
- [x] Shows count of loaded callsigns
- [x] **QRZ credentials (username/password) for fallback lookup**

### GPS Settings
- [x] GPS COM Port is a **dropdown/Combobox** (not text Entry)
- [x] "Refresh Ports" button scans for available COM ports
- [x] **"Connect" button reconnects GPS without app restart**
- [x] **GPS Baud Rate** dropdown (auto-detect or specific baud)
- [x] **GPS Update Rate** dropdown (1-10 Hz via UBX protocol)
- [x] Grid Boundary Alerts checkbox toggles voice alerts
- [x] GPS lock status shows in main status bar as a green or red ASCII large filled circle
- [x] **GPS Time Sync** enable/disable with interval and intermittent mode controls

### Victron SmartShunt
- [x] BLE Address field
- [x] Encryption Key field
- [x] Discover Devices button

### Contest Logger
- [x] N1MM+ / N3FJP dropdown
- [x] N1MM+ TCP port setting
- [x] N3FJP API port setting

### My Bands
- [x] Lists all amateur radio bands, 160m to 1mm, for use by QSO Manual Entry, PSK Monitor alerts, QSY Advisor alerts, and Grid Corner dance setup

### WSJT-X Instances
- [x] Multiple instance configuration (name, path, UDP port) for up to 4 WSJT instances

### APRS-IS Settings
- [x] Enable checkbox (checkbox also associated with APRS checkbox in status bar)
- [x] Callsign-SSID field
- [x] Beacon interval setting
- [x] Alert Radius setting
- [x] Beacon Comment

### PSK Reporter Monitor Settings
- [x] Enable checkbox (checkbox also associated with PSK checkbox in status bar)
- [x] VHF Spot Radius field for filtering interesting PSK Reporter data
- [x] HF Spot Radius field for filtering interesting PSK Reporter data
- [x] Baseline Period field for "Band opening" detection
- [x] Enable checkboxes for various alerts (Band opening, MSp-E, Sp-E, Unusual modes (Q65, MSK144, FT4) and Cross-ref QSY Advisor)
- [x] **Daily DX mode** — DX2/DX3 dynamic detection from ALL.TXT decodes via PriorityEngine

### Slack Notifications
- [x] Enable checkbox
- [x] Multiple webhook configuration for Slack Channel name and the URL (Channel Admin provided)
- [x] Test webhooks button

## Alerts Tab
- [x] A window for all alert messages in the session
- [x] Ignore station textbox for those WSJT aligators 
  - [x] Ignore for 30 min, Ignore Last, Show Ignored, and Clear All Ignored buttons

## Manual Entry Tab
- [x] **Band dropdown - shows bands from "My Bands" setting (all modes)**
- [x] Mode radio buttons (USB/LSB/FM/CW)
- [x] Frequency auto-fills based on band/mode
- [x] Callsign field (auto-uppercase)
- [x] **Super Check Partial panel on right side**
  - [x] Shows matches as you type (updates on each keystroke)
  - [x] Double-click or Enter fills callsign
  - [x] Shows match count
  - [x] **Includes callsigns from QSO Log marked as "(Worked)"**
  - [x] **QRZ fallback when no SCP matches (requires QRZ credentials in Settings)**
  - [x] Calllook.info fallback as an alternative when no SCP matches (no credentials required)
- [x] **Their Grid/Exchange field - label changes based on contest mode:**
  - [x] "Their Grid:" in VHF/222up mode - **validates 4-6 char grid**
  - [x] "Their Exchange:" in QSO Party mode - **accepts any text (state, serial, etc.)**
- [x] **My Grid/County field - label changes based on contest mode:**
  - [x] "My Grid:" in VHF/222up mode (auto-filled from GPS)
  - [x] "My County:" in QSO Party mode (auto-filled from GPS county detection)
- [x] RST Sent/Rcvd fields (defaults change for CW)
- [x] Log QSO button
- [x] Clear Form button

## QSO Log Tab
- [x] Treeview shows: Time, Call, Grid, Band, Mode, My Grid, Source
- [x] Delete Selected button removes from display AND correct ADIF file
- [x] Delete matches on call + band + TIME (not just call+band)
- [x] Delete works on reloaded QSOs from previous days
- [x] List sorts by time after delete (newest first)
- [x] Reload Contest Log button
- [x] Clear Display button

## ADIF Logging
- [x] No duplicate entries (WSJT-X raw disabled)
- [x] **No duplicate entries from CoPilot manual QSOs echoed back from N1MM+**
- [x] WSJT-X QSOs include proper ADIF fields for MY_STATE, MY_CNTY, MY_LAT, MY_LON, MY_GRIDSQUARE for importing to a daily logger and eventual upload to LoTW
- [x] **Log activity mapped to correct ADIF modes: FT8, FT4, Q65, etc. as primary modes per ADIF 3.1.1+** (fixes Log4OM FSQCALL issue)
- [x] SSB submode mapping: USB→SSB+USB, LSB→SSB+LSB
- [x] Daily log files: n5zy_copilot_YYYYMMDD.adi

## PSK Reporter Tab
- [x] Enable/disable monitoring
- [x] **Uses "My Bands" setting (not hardcoded)**
- [x] Shows nearby activity (VHF and HF based on bands)
- [x] Configurable VHF and HF radius on settings tab
- [x] Dynamic frequency range based on selected bands on settings tab
- [x] Button for refresh now and Clear Alerts
- [x] **Clear Alerts resets alert cooldowns** so cleared spots re-appear on next poll
- [x] **Priority Alerts pane** — deduplicates same callsign+band (updates timestamp, moves to top)
- [x] **Priority Alerts aging** — entries older than 30 minutes automatically removed
- [x] **Priority sort** — "--" (LOS) sorts as P6 (lowest), DX!/AP! sort as highest

## Process Monitoring
- [x] N1MM+ process monitor (resends grid/county on restart)
- [x] jt9.exe process monitor (resends grid on WSJT-X restart)
- [x] Console shows "N1MM+ restarted!" when detected

## GPS & Location
- [x] **GPS monitor calls callback every 5 seconds (not just on grid change)** - CRITICAL for county detection while moving
- [x] Grid square updates sent to all WSJT-X instances
- [x] Grid sent to N1MM+ via RoverQTH
- [x] County auto-detection from GPS coordinates (shapefile)
- [x] County sent to N1MM+ in QSO Party mode via RoverQTH
- [x] Grid boundary voice alerts (5mi, 2mi, 1mi, 100yd, 50yd)
- [x] County always used for ADIF logging

## Voice Alerts
- [x] Grid change announcements
- [x] County change announcements
- [x] QSO logged announcements
- [x] Grid boundary proximity alerts
- [x] Battery low warnings
- [x] **GPS lock acquired/lost announcements**
- [x] **Individual category enable/disable toggles:**
  - [x] New Grid (separate from Calling Me)
  - [x] Calling Me (separate from New Grid)
  - [x] Priority stations, band openings, QSO logged, etc.
- [x] **PSK Monitor alerts:**
  - [x] Band openings
  - [x] Sporadic-E (Sp-E) on 2m/70cm
  - [x] Multi-hop Sporadic-E (MSp-E) - "PULL OVER!"
  - [x] Unusual mode activity (Q65, MSK144, FT4)

## APRS Messages Tab
- [x] **Connection status display** - Shows "Connected as N5ZY-9" when connected
- [x] Received/Sent messages inbox (treeview)
- [x] Compose area with To: callsign and Message fields
- [x] Send button (also sends on Enter key)
- [x] Quick reply buttons (73, QSL, QRV?, QSY?, TNX)
- [x] **Voice alert on received message**
- [x] Auto-fills reply To: field from received message
- [x] **Filters out echoed messages from self** (APRS-IS echo)
- [x] **Filters out ack/rej messages** (handled silently)
- [x] **Allows cross-SSID messages** (N5ZY phone → N5ZY-9 CoPilot)
- [x] Clear Messages button

## APRS Nearby Station Alerts
- [x] Alerts when mobile stations are within radius
- [x] **Filters out own callsign** (any SSID) from alerts
- [x] Cooldown timer prevents spam (default 5 min)
- [x] **Duplicate packet filtering** (same-second APRS-IS dupes)
- [x] Voice announcement with callsign, distance, bearing
- [x] Recognizes mobile symbols (Car, Truck, Van, RV, etc.)
- [x] Ignores infrastructure (digipeaters, weather stations)

## Notify Tab (SMS/Slack/APRS Notifications)
- [x] **Twilio SMS Integration** (no extra pip dependency — uses urllib.request)
  - [x] Account SID, Auth Token (masked), From/To number configuration
  - [x] Test SMS button (validates with UI fields before saving)
  - [x] Save Settings button persists Twilio config
- [x] **Automatic SMS Alert Triggers** (to personal number):
  - [x] Master enable/disable toggle
  - [x] Priority Stations (DX!) alerts
  - [x] New DXCC Entity (DX2) alerts
  - [x] New DXCC on Band (DX3) alerts
  - [x] New Grid alerts
  - [x] 10-second global rate limiting between automatic SMS
  - [x] Per-station 5-minute cooldown (prevents SMS flooding from repeated decodes)
  - [x] Transmitter detection — only alerts when hearing the DX station transmit (not callers)
- [x] **Rover Status Messages** (broadcast to subscribers):
  - [x] Pre-filled templates: Grid Entry, Hilltop, Departing, QRT/Break, Band Change
  - [x] Template variables: {MyCall}, {MyGrid}, {bands} resolved from live app state
  - [x] Editable message field
  - [x] **Send SMS** button — broadcasts to subscriber list via Twilio (shows count)
  - [x] **Send Slack** button — posts to configured Slack webhooks
  - [x] **Send APRS** button — sends to all nearby APRS stations within 30 min window (shows live count)
- [x] **SMS Subscriber List**:
  - [x] Paste-friendly text area (Google Sheets compatible)
  - [x] Parses +1XXXXXXXXXX phone numbers with optional callsign
  - [x] Live subscriber count display
  - [x] Persisted in settings.json
- [x] **Notification Log** — timestamped log of all sent notifications

## GPS Time Sync
- [x] **Set system clock from GPS** (requires Administrator)
- [x] Configurable sync interval (1, 2, 5, 10, 15, 30 min or Manual)
- [x] Sync Now manual button
- [x] **Intermittent mode** — closes GPS serial port between syncs to reduce RF noise
  - [x] Auto-suppressed during VHF/222up/QSO Party modes
  - [x] Auto-suppressed while GPS Logger is active
  - [x] Auto-suppressed while vehicle is in motion (>2 mph)
- [x] **Safety guards against feedback loops:**
  - [x] GPS data freshness check — rejects stale timestamps (>30s old)
  - [x] Maximum offset limit — blocks syncs with offset exceeding ±30 seconds
  - [x] Monotonic rate limiting — minimum 60s between syncs (immune to clock changes)
  - [x] Alerts tab warning when safety guard blocks a sync
- [x] Optionally updates WSJT-X grid squares after sync
- [x] GPS baud rate control (9600 default, auto-detect)
- [x] GPS update rate control (1-10 Hz via UBX protocol)

## QSY Advisor Tab
- [x] Station database loaded
- [x] Button to Add stationary Station
- [x] Button to fetch ARRL VHF Public logs for last 24 months
- [x] Search/filter by callsign
- [x] Search/filter by grid
- [x] Search/filter by min number of bands
- [x] Shows bands each station activated in prior ARRL contests
- [x] Calculates distance and direction from center of current grid to center of their grid
- [x] Last Seen field indicates last ARRL contest date callsign appeared in

## Grid Corner Tab
- [x] My Grid Entry dropdown box with "Use GPS" button to fill
- [x] Session box with QSO count current/possible based on rovers in session
  - [x] New Session button to clear the current session and start again with different rovers/setups
  - [x] Export log button 
- [x] Add Rover section
  - [x] Callsign entry box to add each rover involved in grid dance
  - [x] Their starting grid entry box
  - [x] Their bands
- [x] Remove Selected button to remove a rover from the session
- [x] Update Grid button to change a companions grid when they relocate
- [x] Work Selected Rover section 
  - [x] Working rover indicator text w/their grid with larger bold font for easy line-of-sight data entry
  - [x] Band Buttons to log QSO for the selected rover, button changes white to green when pressed and enters combo into the log
    - [x] Band Buttons reset to white if My Grid is updated (I relocated)
  - [x] Radio buttons to choose Mode per band
  - [x] Frequency entry text box auto-fills based on band/mode and auto-advances to next band in series after logging
  - [x] Next and Prev button to change to next rover (or just select them in the session box)
- [x] Session log for convenience

## GPS Logger Tab
- [x] **Live GPS data display (updates every 2 seconds)**
- [x] **Extended GPS data via get_full_data() method**
- [x] **Track file recording (CSV format)**
  - [x] Save path and filename selection
  - [x] Start/Stop/Pause track buttons
  - [x] Recording status display
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
  - [x] Points logged count
- [x] **Annotations/Waypoints:**
  - [x] Quick waypoint buttons (12 presets for rover scouting):
    - Elev. Overpass, Elev. Exit Ramp, Elev. Shoulder, Elev. Viewpoint
    - Utility Driveway, Field Driveway, Mesa Edge, Elev. Parking lot
    - School, Elev. Cemetery, Rest Area, Elev. Rest Area
  - [x] Custom annotation entry
  - [x] Recent waypoints list
  - [x] Voice confirmation on waypoint add

## Test Mode tab
- [x] Grid Precision to test with (4-char v 6-char)
- [x] Manual grid entry field
- [x] Send to WSJT-X + Logging program
- [x] Test voice announcement
- [x] Test Victron Connection
- [x] Reload WSJT-X logs
- [x] APRS-IS Send Beacon Now button
- [x] Show APRS Stats button

## About tab
Displays version
Button to launch documentation link on blog
Button to launch Groups.io channel
Button to launch GitHub
Button to launch QRZ and lookup N5ZY
Button to launch Donate via PayPal
Credits section
---

## Version History

- **1.9.4** - Fix Priority pane: nearby callsign is blank when not hearing DX station directly (only shows your call when you decoded the priority station transmitting)
- **1.9.3** - PSK Monitor fixes: Clear Alerts resets cooldowns, Priority pane dedup (same callsign+band updates instead of duplicating), DX2/DX3 detection from ALL.TXT decodes via PriorityEngine, "--" sorts as P6, Priority Alerts auto-age out after 30 minutes
- **1.9.2** - Priority SMS: transmitter detection (only alerts when hearing DX station directly), per-station 5-min SMS cooldown, PSK Monitor fast first-poll retry (10s until first success)
- **1.9.1** - Credential encryption (Fernet AES-128-CBC + HMAC-SHA256), APRS SSID routing fix (keeps full callsign-SSID for nearby broadcasts)
- **1.9.0** - SMS Notify tab (Twilio SMS, Slack, APRS nearby broadcast), GPS time sync safety guards (fixes runaway feedback loop), voice alert category split (New Grid / Calling Me separated)
- **1.8.58** - Fix incorrect band in New Grid alerts (HF frequency range), fix FSQCALL mode in Log4OM (ADIF 3.1.1+ mode mapping)
- **1.8.57** - GPS baud rate control, GPS update rate control (UBX), GPS time sync feature, voice alert category filtering, PSK Reporter Entity column
- **1.8.56** - Priority Station Alerts (DX!/DX2/DX3), PSK Monitor split pane redesign, Log4OM integration, LoTW/cty.dat DXCC lookup, priority engine
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
