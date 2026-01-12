"""
Battery Monitor Module
Monitors Victron SmartShunt via Bluetooth BLE using victron-ble package
Reads data from BLE advertisements (Instant Readout feature)
"""

import threading
import time
import asyncio
from bleak import BleakScanner
from victron_ble.devices import detect_device_type

class BatteryMonitor:
    def __init__(self, device_address, encryption_key, callback):
        """
        Initialize battery monitor
        
        Args:
            device_address: Bluetooth address of SmartShunt (MAC address)
            encryption_key: Advertisement key from VictronConnect app
            callback: Function to call with (voltage, current, soc, remaining_mins)
        """
        # Normalize MAC address format (uppercase with colons)
        self.device_address = self._normalize_mac_address(device_address)
        self.encryption_key = encryption_key
        self.callback = callback
        self.running = False
        self.thread = None
        
        # Current readings
        self.voltage = 0.0
        self.current = 0.0
        self.soc = 100.0
        self.remaining_mins = 65535
        
        print(f"Battery: Initialized for device: {self.device_address}")
        print(f"Battery: Using Instant Readout BLE advertisement scanning")
    
    def _normalize_mac_address(self, address):
        """
        Normalize MAC address to AA:BB:CC:DD:EE:FF format
        
        Handles various input formats:
        - AABBCCDDEEFF -> AA:BB:CC:DD:EE:FF
        - AA-BB-CC-DD-EE-FF -> AA:BB:CC:DD:EE:FF
        - aa:bb:cc:dd:ee:ff -> AA:BB:CC:DD:EE:FF (uppercase)
        """
        # Remove any existing separators
        clean = address.replace(':', '').replace('-', '').replace(' ', '').upper()
        
        # Check if it's a valid length (12 hex characters)
        if len(clean) != 12:
            print(f"Battery: Warning - MAC address '{address}' doesn't look valid (should be 12 hex chars)")
            return address  # Return as-is, let it fail later with better error
        
        # Add colons every 2 characters
        normalized = ':'.join(clean[i:i+2] for i in range(0, 12, 2))
        
        if normalized != address:
            print(f"Battery: Normalized MAC address from '{address}' to '{normalized}'")
        
        return normalized
    
    def start(self):
        """Start battery monitoring thread"""
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop battery monitoring"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
    
    def _monitor_loop(self):
        """Main monitoring loop (runs in separate thread)"""
        # Run async event loop in this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        while self.running:
            try:
                loop.run_until_complete(self._read_victron())
            except Exception as e:
                print(f"Battery: Error reading SmartShunt: {e}")
                import traceback
                traceback.print_exc()
            
            # Read every 10 seconds
            time.sleep(10)
    
    async def _read_victron(self):
        """Read data from Victron SmartShunt by scanning BLE advertisements"""
        try:
            # Only print scan message every 6th scan (once per minute)
            if not hasattr(self, '_scan_count'):
                self._scan_count = 0
            self._scan_count += 1
            
            if self._scan_count % 6 == 1:  # Print on 1st, 7th, 13th, etc.
                print(f"Battery: Scanning for {self.device_address}...")
            
            # Scan for BLE devices for 5 seconds
            devices = await BleakScanner.discover(timeout=5.0, return_adv=True)
            
            # Look for our specific device
            found_device = None
            found_adv = None
            
            for address, (device, advertisement_data) in devices.items():
                # Match by MAC address (case-insensitive)
                if address.upper() == self.device_address.upper():
                    found_device = device
                    found_adv = advertisement_data
                    break
            
            if not found_device:
                # Only print "not found" every 6th attempt
                if self._scan_count % 6 == 0:
                    print(f"Battery: Device {self.device_address} not found in last 6 scans")
                return
            
            # Try to detect and parse the Victron device
            try:
                # Extract Victron manufacturer data bytes
                manufacturer_data = found_adv.manufacturer_data
                
                # Victron uses manufacturer ID 737 (0x02E1)
                victron_mfg_id = 0x02E1
                
                if victron_mfg_id not in manufacturer_data:
                    print(f"Battery: No Victron manufacturer data found")
                    print(f"Battery: Available manufacturer IDs: {list(manufacturer_data.keys())}")
                    print(f"Battery: Make sure 'Instant Readout' is enabled")
                    return
                
                # Get raw manufacturer data bytes
                raw_data = manufacturer_data[victron_mfg_id]
                
                # Detect device type (returns a CLASS, not instance)
                device_class = detect_device_type(raw_data)
                
                if device_class is None:
                    print(f"Battery: Could not detect Victron device type")
                    return
                
                # Create device instance with encryption key
                device = device_class(self.encryption_key)
                
                # Parse the raw data
                parsed_data = device.parse(raw_data)
                
                if parsed_data is None:
                    print(f"Battery: Could not parse advertisement (check encryption key)")
                    return
                
                # Extract voltage and current
                # The parsed data structure varies by device type, but should have these fields
                voltage = getattr(parsed_data, 'get_voltage', lambda: None)()
                current = getattr(parsed_data, 'get_current', lambda: None)()
                soc = getattr(parsed_data, 'get_soc', lambda: None)()
                remaining = getattr(parsed_data, 'get_remaining_mins', lambda: None)()
                
                # SmartShunt provides voltage and current
                if voltage is not None and current is not None:
                    # Convert to proper units if needed
                    voltage_v = voltage / 100.0 if voltage > 100 else voltage  # mV to V
                    current_a = current / 1000.0 if abs(current) > 100 else current  # mA to A
                    
                    # Only callback if values changed significantly
                    if (abs(voltage_v - self.voltage) > 0.1 or 
                        abs(current_a - self.current) > 0.1):
                        
                        self.voltage = voltage_v
                        self.current = current_a
                        self.soc = soc if soc is not None else 100.0
                        self.remaining_mins = remaining if remaining is not None else 65535
                        
                        print(f"Battery: {voltage_v:.1f}V, {current_a:.1f}A")
                        
                        # Callback to update UI
                        if self.callback:
                            self.callback(voltage_v, current_a, self.soc, self.remaining_mins)
                else:
                    print(f"Battery: Advertisement parsed but no voltage/current data")
                    
            except Exception as e:
                print(f"Battery: Error parsing Victron advertisement: {e}")
                import traceback
                traceback.print_exc()
                
        except Exception as e:
            print(f"Battery: BLE scan error: {e}")
            raise
    
    @staticmethod
    async def discover_devices():
        """Discover available Victron devices"""
        try:
            from bleak import BleakScanner
            
            print("Battery: Scanning for Bluetooth devices...")
            devices = await BleakScanner.discover(timeout=10.0)
            
            victron_devices = []
            for device in devices:
                if device.name and ('Smart' in device.name or 'Victron' in device.name):
                    victron_devices.append({
                        'name': device.name,
                        'address': device.address
                    })
                    print(f"  Found: {device.name} ({device.address})")
            
            return victron_devices
        except Exception as e:
            print(f"Battery: Error during discovery: {e}")
            return []
