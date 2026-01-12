# N5ZY VHF Contest Co-Pilot

AI-powered assistant for VHF contest rover operations.

## Features (Tier 1 - January Contest Ready)

✅ **GPS Integration**
- Automatically monitors GPS and calculates Maidenhead grid square
- Updates all WSJT-X instances and N1MM+ when grid changes
- Voice announcements on grid crossings

✅ **Battery Monitoring**
- Real-time Victron SmartShunt monitoring via Bluetooth
- Voltage display with color-coded warnings
- Low battery alerts

✅ **WSJT-X Log Monitoring**
- Tracks worked grids per band
- Alerts on new grid multipliers
- Identifies stations calling you
- Reloads contest logs on startup

✅ **Voice Alerts**
- Text-to-speech announcements for important events
- Grid changes, new grids, stations calling

✅ **Manual Entry**
- Log phone/CW contacts to N1MM+
- Band, frequency, call, and grid entry

## Installation

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

**Note**: On Windows, `tkinter` is included with Python. If you get tkinter errors, reinstall Python with "tcl/tk and IDLE" enabled.

### 2. Install GPS Time Sync Software

Keep your existing GPS time sync software running on one GPS dongle (e.g., COM2).

### 3. Configure Victron SmartShunt

1. Open VictronConnect app on your phone
2. Connect to your SmartShunt
3. Go to Settings → Product Info
4. Enable "Instant Readout via Bluetooth"
5. Note the Bluetooth address and encryption key

## Configuration

### First Run

1. Launch the application:
   ```bash
   python copilot.py
   ```

2. Go to the **Settings** tab

3. Configure:
   - **GPS COM Port**: Set to your second GPS dongle (e.g., COM3)
   - **Victron BLE Address**: Enter from VictronConnect app
   - **Victron Encryption Key**: Enter from VictronConnect app
   - **WSJT-X Log Paths**: Browse to each WSJT-X instance's log folder
     - Example: `C:\Users\Marcus\AppData\Local\WSJT-X\6m\`

4. Click **Save Settings**

### WSJT-X Setup

For each WSJT-X instance:
1. Open WSJT-X
2. Go to Settings → Reporting
3. Enable "Accept UDP requests"
4. Note the UDP Server port (default: 2237)
   - If running multiple instances, each needs a unique port:
     - 6m instance: 2237
     - 2m instance: 2238
     - 222/902 instance: 2239

5. Update the Co-Pilot settings with these port numbers

### N1MM+ Setup

1. Open N1MM+
2. Go to Config → Configure Ports, Mode Control...
3. Enable "Broadcast data" on UDP port 12060 (default)

## Usage

### Normal Contest Operation

1. **Start Co-Pilot FIRST**
   - This ensures logs are loaded before contest starts

2. **Start WSJT-X instances**
   - 6m (always on)
   - 2m (usually on)
   - 222/902 (as needed)

3. **Start N1MM+**

4. **Drive and operate!**
   - Co-Pilot will:
     - Announce grid changes
     - Update WSJT-X and N1MM+ automatically
     - Alert you to new grids
     - Alert when stations call you
     - Monitor battery voltage

### Manual Entry (Phone/CW)

1. Go to **Manual Entry** tab
2. Select band
3. Enter frequency, callsign, and grid
4. Click "Log to N1MM"

### Test Mode

Before the contest, use **Test Mode** tab to:
- Test grid updates to WSJT-X/N1MM
- Test voice announcements
- Test Victron connection
- Reload logs

## Troubleshooting

### GPS Not Working

- Verify COM port in Device Manager
- Check GPS dongle has power (should have LED)
- Try unplugging and replugging GPS
- Check Settings tab shows correct COM port

### Victron Not Connecting

- Click "Discover Devices" in Settings to find your SmartShunt
- Verify encryption key is correct
- Ensure SmartShunt is within Bluetooth range (~30 feet)
- Check SmartShunt battery is connected and powered

### WSJT-X Not Updating

- Verify "Accept UDP requests" is enabled in WSJT-X
- Check port numbers match in Settings
- Try "Force Grid Update" button
- Restart WSJT-X and Co-Pilot

### No Voice Announcements

- Check Windows audio isn't muted
- Test voice with "Test Voice Announcement" button
- Windows may need to install speech synthesis voices

### WSJT-X Logs Not Being Monitored

- Verify log path points to correct directory
- Check directory contains ALL.TXT file
- Click "Reload WSJT-X Logs" in Test Mode
- Path should be instance-specific directory, not shared

## File Locations

- **Config**: `config/settings.json`
- **Logs**: `logs/` (application logs)

## Contest Day Checklist

- [ ] GPS dongle connected (dedicated one for Co-Pilot)
- [ ] Victron SmartShunt paired and in range
- [ ] Co-Pilot started and GPS shows valid grid
- [ ] WSJT-X instances started and accepting UDP
- [ ] N1MM+ started and broadcasting
- [ ] Test grid update works
- [ ] Voice announcements working
- [ ] Battery voltage showing correctly

## Known Limitations (January Contest)

- Manual grid square tracking only (no dynamic multiplier analysis yet)
- PSK Reporter monitoring not implemented
- SMS notifications not implemented  
- APRS integration not implemented
- Social media posting not implemented

These features are planned for June/September contests!

## Support

For issues during contest prep:
- Check this README
- Use Test Mode to diagnose
- All modules log to console for debugging

73 and good luck in the contest!
