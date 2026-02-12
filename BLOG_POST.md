# N5ZY Co-Pilot: VHF Contest Automation for Rovers

**By Marcus N5ZY | February 2026**

After years of juggling multiple WSJT-X instances, manual grid updates, and the constant fear of losing QSOs to UDP packet drops during VHF contests, I finally built the tool I always wanted. The N5ZY Co-Pilot is a Python application designed specifically for VHF/UHF rover operations, automating the tedious stuff so you can focus on making contacts.

*Developed over January 2026 with assistance from Claude (Anthropic), turning scattered ideas into working code just in time for the January VHF Contest.*

## The Problem

Running a multi-radio rover station during ARRL VHF contests is an exercise in multitasking chaos:

- **Three radios, three WSJT-X instances** - IC-7610 on 6m, IC-9700 on 2m/70cm/23cm, IC-7300 with transverters on 1.25m/33cm
- **Existing tools dropping QSOs** - Apps using UDP multicast are fire-and-forget; when two radios decode simultaneously, packets vanish. Most also only support two WSJT-X instances, which doesn't cut it for a multi-band rover setup.
- **Manual grid updates** - Every boundary crossing means updating N1MM+, all three WSJT-X instances, and hoping you don't fat-finger it after 10 hours of contesting
- **No situational awareness** - Who else is on the band? Is there a Sporadic-E opening you're missing?

## The Solution

The Co-Pilot runs on my rover laptop and handles all of this automatically.

### Core Features

**Automatic Grid Updates**
A $9 USB GPS dongle feeds coordinates to the Co-Pilot, which converts them to Maidenhead grid squares and pushes updates to:
- All WSJT-X instances via UDP
- N1MM+ via RoverQTH
- APRS-IS for position beaconing

Cross a grid boundary? Everything updates automatically. No menus, no typos, no forgotten updates.

**Reliable QSO Relay**
The Co-Pilot listens directly to WSJT-X UDP broadcasts and queues QSOs for N1MM+ (or N3FJP). It uses a sequential queue with retry logic, supporting up to four WSJT-X instances simultaneously. No more lost contacts when multiple radios decode at once.

**Battery Monitoring**
Connected to my Victron SmartShunt via Bluetooth, the Co-Pilot displays voltage, current draw, and state of charge right in the status bar: `13.2V -15A 85%`. At a glance you know if you need to swap batteries at the next stop before getting on the air. Color-coded warnings (green/orange/red) and voice alerts catch critical voltage drops during transmit.

**Voice Announcements**
"Grid change. Entering EM15." 
"QSO logged. W5ABC."
"Warning: GPS lock lost."

Hands-free awareness while driving between grids.

### Situational Awareness

**PSK Reporter Integration**
Monitors PSK Reporter every 5 minutes for:
- Multi-hop Sporadic-E (the holy grail for 2m/70cm DX)
- Single-hop Sporadic-E openings
- Tropo ducting conditions
- Unusual mode activity (Q65 on 23cm? Someone's trying!)

Priority-coded alerts tell you when to stop calling CQ and start pointing the beam.

**QSY Advisor**
This might be my favorite feature. The QSY Advisor maintains a database of stations and what bands they operate, built from:
- ARRL contest public logs (January VHF, June VHF, September VHF, 222 MHz and Up)
- Manual entries for stations you know

When you work someone on 2m, it instantly tells you they also have 70cm and 23cm. Click their callsign to look them up on QRZ. Before the contest, browse stations in your target grids and reach out to coordinate schedules with high-band operators.

**APRS-IS Monitoring**
Track other rovers in your area via APRS-IS (internet, not RF - no 2m conflict). Get alerts when mobile stations enter your vicinity.

### Specialized Tools

**Manual Entry Tab**
For phone and CW contacts that don't go through WSJT-X. Supports both VHF contests (grid exchange) and State QSO Parties (county exchange).

**Grid Corner Tracker**
When multiple rovers meet at a grid corner for the "grid dance," this tool tracks who you've worked on which bands. Big colored buttons turn green as you log each band. Never lose track of whether you worked N0LD/R on 33cm in EM15 or EM16.

**Slack Notifications**
Post grid activations to Slack channels automatically:
> 📍 N5ZY/R now in EM15 on 6m, 2m, 1.25m, 70cm, 33cm, 23cm

Fixed stations watching the channel know exactly when to look for you.

### Contest Modes

- **VHF Contest** - Standard ARRL January/June/September VHF with 4-character grid exchange
- **222 MHz and Up** - 6-character grid precision
- **State QSO Party** - County-based exchange with N1MM+ integration

## Technical Details

- **Language:** Python 3 with Tkinter GUI
- **GPS:** Any NMEA-compatible USB dongle ($9 on Amazon)
- **Battery:** Victron SmartShunt via Bluetooth LE
- **Logging:** N1MM+ or N3FJP
- **WSJT-X:** Supports up to 4 simultaneous instances

The app runs on Windows and should work on Linux/Mac with minor tweaks.

## What's Next

- **Twilio SMS alerts** - Text message notifications to subscribers when activating new grids (waiting on A2P 10DLC approval)
- **PyInstaller executable** - One-click install for the less technically inclined
- **Open source release** - Coming soon to GitHub

## Get It

The Co-Pilot is available as donationware. If it helps your rover operation, consider buying me a coffee to offset development time.

**Download:** [Link to GitHub/releases]

**Questions?** Find me on the Oklahoma Rovers or North Texas VHF Slack, or email via QRZ.

73 de N5ZY/R 🚗📻

---

*The N5ZY Co-Pilot was developed with assistance from Claude (Anthropic) over numerous late-night coding sessions. The AI helped with Python implementation, debugging, and turning my scattered ideas into working code.*
