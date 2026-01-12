# Battery Monitor - Manufacturer Data & Device Instantiation Fix

## Issue Found
```
TypeError: a bytes-like object is required, not 'tuple'
TypeError: Device.parse() missing 1 required positional argument: 'data'
```

## Root Causes

### Issue 1: Wrong Data Type
The `detect_device_type()` function expects **raw manufacturer data bytes**, not the AdvertisementData object.

### Issue 2: Class vs Instance
The `detect_device_type()` function returns a **device CLASS**, not an instance. You must:
1. Instantiate the class with the encryption key
2. Then call parse() with the raw data

## The Complete Fix

### WRONG:
```python
# Wrong 1: Passing entire advertisement object
parsed_device = detect_device_type(advertisement_data)  # WRONG!

# Wrong 2: Trying to parse with just the key
parsed_data = parsed_device.parse(encryption_key)  # WRONG!
```

### CORRECT:
```python
# Step 1: Extract manufacturer data dictionary
manufacturer_data = advertisement_data.manufacturer_data

# Step 2: Get Victron manufacturer ID (737 = 0x02E1)
victron_mfg_id = 0x02E1

# Step 3: Extract the raw bytes for this manufacturer
raw_data = manufacturer_data[victron_mfg_id]

# Step 4: Detect device type (returns a CLASS)
device_class = detect_device_type(raw_data)

# Step 5: Instantiate the class with encryption key
device = device_class(encryption_key)

# Step 6: Parse the raw data
parsed_data = device.parse(raw_data)  # CORRECT!
```

## Files Fixed
- ✅ `test_battery.py` - Both discovery and read functions
- ✅ `modules/battery_monitor.py` - Main monitor loop

## Test Again
```bash
python test_battery.py
```

Should now show:
```
Found Victron Device:
  Name: SmartShunt HQ2502ZAFX7
  MAC Address: D7:64:93:F2:17:C6
  Device Type: BatteryMonitor
  
✅ Successfully parsed!
Data:
  Voltage: 13.2 V
  Current: -5.3 A
```
