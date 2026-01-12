# Victron SmartShunt Battery Monitor - Setup Guide

## Overview
The battery monitor reads voltage and current from your Victron SmartShunt 300A using Bluetooth Low Energy (BLE) and the "Instant Readout" feature.

---

## Prerequisites

### 1. Enable Instant Readout on SmartShunt
**This is CRITICAL - without this, the Co-Pilot cannot read the data!**

1. Open **VictronConnect** app on your phone/tablet
2. Connect to your SmartShunt
3. Go to: **Settings** → **Product Info**
4. Enable: **"Instant readout via Bluetooth"**
5. Note the **Advertisement Key** (encryption key) - you'll need this!

### 2. Install Python Package
The `victron-ble` package should already be in requirements.txt, but if needed:

```bash
pip install victron-ble
```

---

## Finding Your SmartShunt Details

### Option 1: Use the Test Script (Recommended)
```bash
cd "C:\N5ZY CoPilot\n5zy-copilot"
python test_battery.py
```

This will:
1. Scan for Victron devices
2. Show you the MAC address
3. Test reading data with your encryption key
4. Display voltage, current, SOC, etc.

### Option 2: Use victron-ble CLI
```bash
victron-ble discover
```

This shows all nearby Victron devices with their MAC addresses.

---

## Configuring the Co-Pilot

### 1. Get Your SmartShunt Details
You need:
- **MAC Address**: Format `AA:BB:CC:DD:EE:FF` (shown in test_battery.py or victron-ble discover)
- **Advertisement Key**: From VictronConnect app (Settings → Product Info)

### 2. Enter in Co-Pilot Settings Tab

In the Co-Pilot application:
1. Go to **Settings** tab
2. Find **"Victron SmartShunt"** section
3. Enter:
   - **BLE Address**: Your MAC address (e.g., `D7:64:93:F2:17:C6`)
   - **Encryption Key**: Your advertisement key from VictronConnect
4. Click **"Save Settings"**

The Co-Pilot will automatically start monitoring every 10 seconds.

---

## Testing

### Quick Test in Co-Pilot
1. Go to **Test Mode** tab
2. Click **"Test Victron Connection"**
3. Check console output or Alerts tab for voltage reading

### Detailed Test Script
```bash
python test_battery.py
```

**Expected Output:**
```
Found Victron Device:
  Name: SmartShunt 500A/50mV
  MAC Address: D7:64:93:F2:17:C6
  RSSI: -45 dBm
  Device Type: SmartShuntMonitor

✅ Successfully parsed advertisement!

Data:
  Voltage: 13.2 V
  Current: -5.3 A
  State of Charge: 87%
  Remaining: 240 min (4.0 hours)
```

---

## Troubleshooting

### "No Victron devices found"
**Causes:**
1. SmartShunt not powered on
2. Bluetooth disabled on computer
3. "Instant Readout" not enabled in VictronConnect
4. Device out of range (try moving closer)

**Solutions:**
1. Check 12V power to SmartShunt (LED should be on)
2. Enable Bluetooth in Windows Settings
3. Enable Instant Readout (see Prerequisites above)
4. Move laptop closer to SmartShunt

### "Could not detect Victron device type"
**Cause:** "Instant Readout" is not enabled

**Solution:**
1. Open VictronConnect app
2. Connect to SmartShunt
3. Settings → Product Info → Enable "Instant readout via Bluetooth"
4. Wait a few seconds for it to activate
5. Try scanning again

### "Could not parse advertisement data"
**Cause:** Wrong encryption key

**Solution:**
1. Get the correct key from VictronConnect app
2. Settings → Product Info → Copy the advertisement key
3. Make sure you're copying the FULL key (no spaces, no typos)
4. Update in Co-Pilot Settings tab

### "Scanner object has no attribute get_data"
**Cause:** Old battery_monitor.py code (fixed in latest version)

**Solution:**
1. Extract the latest n5zy-copilot.zip
2. The new code uses BleakScanner and detect_device_type correctly

---

## How It Works

### BLE Advertisement Scanning
Instead of connecting to the SmartShunt (which would interfere with VictronConnect), the Co-Pilot:

1. **Scans** for BLE advertisements every 10 seconds
2. **Filters** for your specific SmartShunt MAC address
3. **Parses** the encrypted advertisement data using your key
4. **Extracts** voltage, current, SOC, and remaining time
5. **Updates** the main window display

This is non-intrusive and works alongside VictronConnect app!

### What Gets Displayed
- **Main Window**: Battery voltage (color-coded: green/orange/red)
- **Console**: Detailed readings every 10 seconds
- **Alerts**: Low voltage warnings

---

## MAC Address Formats

The Co-Pilot accepts various MAC address formats and normalizes them:

**Input Examples (all valid):**
- `D76493F217C6` → Normalized to `D7:64:93:F2:17:C6`
- `D7-64-93-F2-17-C6` → Normalized to `D7:64:93:F2:17:C6`
- `d7:64:93:f2:17:c6` → Normalized to `D7:64:93:F2:17:C6`

---

## During Contest

### If Battery Monitor Works:
✅ Real-time voltage monitoring  
✅ Color-coded alerts (green/orange/red)  
✅ Automatic low voltage warnings  

### If Battery Monitor Doesn't Work:
- **No impact on core functionality!**
- WSJT-X, N1MM+, GPS, Voice all work independently
- Just monitor battery manually with a voltmeter

**Priority:** Nice to have, not essential for contest operations

---

## Advanced: Manual CLI Testing

### Discover Devices
```bash
victron-ble discover
```

### Read Specific Device
```bash
victron-ble read "YOUR_MAC_ADDRESS@YOUR_ENCRYPTION_KEY"
```

Example:
```bash
victron-ble read "D7:64:93:F2:17:C6@0df4d0395b7d1a876c0c33ecb9e70dcd"
```

---

## Configuration File

Settings are stored in: `config/settings.json`

```json
{
  "victron_address": "D7:64:93:F2:17:C6",
  "victron_key": "0df4d0395b7d1a876c0c33ecb9e70dcd"
}
```

You can edit this file directly if needed.

---

## Summary

**Setup Steps:**
1. ✅ Enable "Instant Readout" in VictronConnect
2. ✅ Run `python test_battery.py` to find MAC and test
3. ✅ Enter MAC and key in Co-Pilot Settings
4. ✅ Save and restart if needed
5. ✅ Watch voltage update in main window!

**Expected Result:**
```
Battery: Scanning for D7:64:93:F2:17:C6...
Battery: 13.2V, -5.3A
```

---

Generated: 2026-01-11 Evening  
Based on: victron-ble package and Instant Readout feature
