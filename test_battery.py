#!/usr/bin/env python3
"""
Test script for Victron SmartShunt battery monitoring
Tests the victron-ble package and BLE advertisement reading
"""

import asyncio
from bleak import BleakScanner
from victron_ble.devices import detect_device_type

async def discover_victron_devices():
    """Scan for Victron devices and show their details"""
    print("Scanning for Victron devices (10 seconds)...")
    print("Make sure 'Instant Readout' is enabled in VictronConnect app!")
    print()
    
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
    
    victron_devices = []
    
    for address, (device, advertisement_data) in devices.items():
        # Check if it's a Victron device
        if device.name and ('Smart' in device.name or 'Victron' in device.name or 'BMV' in device.name):
            print(f"Found Victron Device:")
            print(f"  Name: {device.name}")
            print(f"  MAC Address: {address}")
            print(f"  RSSI: {advertisement_data.rssi} dBm")
            
            # Try to detect device type
            try:
                # Extract Victron manufacturer data (manufacturer ID 0x02E1 = 737)
                manufacturer_data = advertisement_data.manufacturer_data
                
                # Victron uses manufacturer ID 737 (0x02E1)
                victron_mfg_id = 0x02E1
                
                if victron_mfg_id in manufacturer_data:
                    raw_data = manufacturer_data[victron_mfg_id]
                    device_class = detect_device_type(raw_data)
                    if device_class:
                        print(f"  Device Type: {device_class.__name__}")
                    else:
                        print(f"  Device Type: Unknown (Instant Readout may not be enabled)")
                else:
                    print(f"  Device Type: No Victron manufacturer data found")
            except Exception as e:
                print(f"  Error detecting type: {e}")
            
            print()
            victron_devices.append((address, device.name))
    
    if not victron_devices:
        print("No Victron devices found!")
        print()
        print("Troubleshooting:")
        print("1. Make sure the SmartShunt is powered on")
        print("2. Make sure Bluetooth is enabled on your computer")
        print("3. Enable 'Instant Readout' in VictronConnect app:")
        print("   Settings → Product Info → Instant readout via Bluetooth")
        print("4. Make sure the device is in range")
    
    return victron_devices


async def read_device(mac_address, encryption_key):
    """Read data from a specific Victron device"""
    print(f"Reading from {mac_address}...")
    print(f"Using encryption key: {encryption_key[:8]}...")
    print()
    
    # Scan for the specific device
    devices = await BleakScanner.discover(timeout=5.0, return_adv=True)
    
    found = False
    for address, (device, advertisement_data) in devices.items():
        if address.upper() == mac_address.upper():
            found = True
            print(f"Device found: {device.name}")
            
            try:
                # Extract Victron manufacturer data bytes
                manufacturer_data = advertisement_data.manufacturer_data
                
                # Victron uses manufacturer ID 737 (0x02E1)
                victron_mfg_id = 0x02E1
                
                if victron_mfg_id not in manufacturer_data:
                    print("ERROR: No Victron manufacturer data in advertisement")
                    print(f"Available manufacturer IDs: {list(manufacturer_data.keys())}")
                    print("Make sure 'Instant Readout' is enabled in VictronConnect app")
                    return
                
                # Get the raw manufacturer data bytes
                raw_data = manufacturer_data[victron_mfg_id]
                print(f"Manufacturer data length: {len(raw_data)} bytes")
                
                # Detect device type from raw data (returns a CLASS)
                device_class = detect_device_type(raw_data)
                
                if device_class is None:
                    print("ERROR: Could not detect Victron device type")
                    print("Make sure 'Instant Readout' is enabled in VictronConnect app")
                    return
                
                print(f"Device type: {device_class.__name__}")
                
                # Create device instance with encryption key
                device = device_class(encryption_key)
                
                # Parse the raw data
                parsed_data = device.parse(raw_data)
                
                if parsed_data is None:
                    print("ERROR: Could not parse advertisement data")
                    print("Check your encryption key - it should be from VictronConnect app")
                    return
                
                print("✅ Successfully parsed advertisement!")
                print()
                
                # Try to extract common fields
                print("Data:")
                
                # Different device types have different methods
                try:
                    voltage = getattr(parsed_data, 'get_voltage', lambda: None)()
                    if voltage is not None:
                        voltage_v = voltage / 100.0 if voltage > 100 else voltage
                        print(f"  Voltage: {voltage_v:.2f} V")
                except Exception as e:
                    print(f"  Voltage: N/A ({e})")
                
                try:
                    current = getattr(parsed_data, 'get_current', lambda: None)()
                    if current is not None:
                        current_a = current / 1000.0 if abs(current) > 100 else current
                        print(f"  Current: {current_a:.2f} A")
                except Exception as e:
                    print(f"  Current: N/A ({e})")
                
                try:
                    soc = getattr(parsed_data, 'get_soc', lambda: None)()
                    if soc is not None:
                        print(f"  State of Charge: {soc:.0f}%")
                except Exception as e:
                    print(f"  SOC: N/A ({e})")
                
                try:
                    remaining = getattr(parsed_data, 'get_remaining_mins', lambda: None)()
                    if remaining is not None and remaining != 65535:
                        hours = remaining / 60
                        print(f"  Remaining: {remaining} min ({hours:.1f} hours)")
                except Exception as e:
                    print(f"  Remaining: N/A ({e})")
                
                # Show all available attributes
                print()
                print("All available data:")
                for attr in dir(parsed_data):
                    if not attr.startswith('_') and callable(getattr(parsed_data, attr)):
                        try:
                            value = getattr(parsed_data, attr)()
                            if value is not None:
                                print(f"  {attr}: {value}")
                        except:
                            pass
                
            except Exception as e:
                print(f"ERROR: {e}")
                import traceback
                traceback.print_exc()
            
            break
    
    if not found:
        print(f"ERROR: Device {mac_address} not found in scan")
        print("Make sure the MAC address is correct")


async def main():
    """Main test program"""
    print("=" * 60)
    print("Victron SmartShunt Battery Monitor Test")
    print("=" * 60)
    print()
    
    # First, discover devices
    devices = await discover_victron_devices()
    
    if not devices:
        return
    
    print("=" * 60)
    print()
    
    # Ask user which device to read
    if len(devices) == 1:
        mac_address = devices[0][0]
        print(f"Using device: {devices[0][1]} ({mac_address})")
        print()
    else:
        print("Multiple devices found. Enter the number to test:")
        for i, (addr, name) in enumerate(devices):
            print(f"  {i+1}. {name} ({addr})")
        choice = int(input("Choice: ")) - 1
        mac_address = devices[choice][0]
        print()
    
    # Ask for encryption key
    print("Enter the advertisement key (encryption key) from VictronConnect app:")
    print("(In VictronConnect: Settings → Product Info → Instant readout via Bluetooth)")
    encryption_key = input("Key: ").strip()
    print()
    
    if not encryption_key:
        print("ERROR: Encryption key is required!")
        return
    
    print("=" * 60)
    print()
    
    # Read the device
    await read_device(mac_address, encryption_key)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
