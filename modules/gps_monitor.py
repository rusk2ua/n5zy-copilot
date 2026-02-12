"""
GPS Monitor Module
Reads GPS data from serial port and calculates Maidenhead grid square
"""

import serial
import pynmea2
import threading
import time

def latlon_to_grid(lat, lon):
    """Convert latitude/longitude to Maidenhead grid square (6-character)"""
    
    # Adjust longitude
    lon = lon + 180
    lat = lat + 90
    
    # Field (first pair)
    field_lon = int(lon / 20)
    field_lat = int(lat / 10)
    
    # Square (second pair)
    lon = lon - (field_lon * 20)
    lat = lat - (field_lat * 10)
    square_lon = int(lon / 2)
    square_lat = int(lat)
    
    # Subsquare (third pair)
    lon = (lon - (square_lon * 2)) * 12
    lat = (lat - square_lat) * 24
    subsquare_lon = int(lon)
    subsquare_lat = int(lat)
    
    grid = (
        chr(ord('A') + field_lon) +
        chr(ord('A') + field_lat) +
        str(square_lon) +
        str(square_lat) +
        chr(ord('a') + subsquare_lon) +
        chr(ord('a') + subsquare_lat)
    )
    
    return grid

class GPSMonitor:
    def __init__(self, port, callback, grid_precision=4, lock_callback=None):
        """
        Initialize GPS monitor
        
        Args:
            port: COM port string (e.g., 'COM3')
            callback: Function to call with (grid, lat, lon) when position updates
            grid_precision: 4 or 6 character grid precision (default 4 for VHF contests)
            lock_callback: Function to call with (has_lock, message) when lock status changes
        """
        self.port = port
        self.callback = callback
        self.lock_callback = lock_callback
        self.grid_precision = grid_precision
        self.running = False
        self.thread = None
        self.current_grid = None
        self.current_lat = None
        self.current_lon = None
    
    def set_precision(self, precision):
        """Change grid precision (4 or 6). Triggers callback if grid changes."""
        if precision not in [4, 6]:
            print(f"GPS: Invalid precision {precision}, must be 4 or 6")
            return
        
        old_precision = self.grid_precision
        self.grid_precision = precision
        print(f"GPS: Grid precision changed from {old_precision} to {precision} characters")
        
        # If we have a position, recalculate and notify if grid changed
        if self.current_lat and self.current_lon:
            full_grid = latlon_to_grid(self.current_lat, self.current_lon)
            new_grid = full_grid[:precision]
            old_grid = self.current_grid
            
            if new_grid != old_grid:
                self.current_grid = new_grid
                print(f"GPS: Grid changed due to precision: {old_grid} → {new_grid}")
                self.callback(new_grid, self.current_lat, self.current_lon)
    
    def start(self):
        """Start GPS monitoring thread"""
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop GPS monitoring"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
    
    def _monitor_loop(self):
        """Main monitoring loop (runs in separate thread)"""
        while self.running:
            try:
                # Open serial connection
                with serial.Serial(self.port, baudrate=9600, timeout=1) as ser:
                    print(f"GPS: Connected to {self.port}")
                    had_fix = False  # Track if we previously had a fix
                    
                    while self.running:
                        try:
                            line = ser.readline().decode('ascii', errors='ignore').strip()
                            
                            if line.startswith('$GPGGA') or line.startswith('$GNGGA'):
                                # Parse NMEA sentence
                                msg = pynmea2.parse(line)
                                
                                # Check fix quality (0=no fix, 1=GPS fix, 2=DGPS fix, etc.)
                                has_fix = msg.latitude and msg.longitude and hasattr(msg, 'gps_qual') and msg.gps_qual > 0
                                
                                if has_fix:
                                    if not had_fix:
                                        # Just got a fix
                                        print(f"GPS: Lock acquired")
                                        if self.lock_callback:
                                            self.lock_callback(True, "GPS lock acquired")
                                        had_fix = True
                                    
                                    lat = msg.latitude
                                    lon = msg.longitude
                                    
                                    # Calculate full 6-char grid, then truncate to precision
                                    full_grid = latlon_to_grid(lat, lon)
                                    grid = full_grid[:self.grid_precision]
                                    
                                    # Always update stored position
                                    self.current_lat = lat
                                    self.current_lon = lon
                                    
                                    # Check if grid changed (for logging)
                                    grid_changed = (grid != self.current_grid)
                                    if grid_changed:
                                        self.current_grid = grid
                                        print(f"GPS: Position update - {grid} ({lat:.6f}, {lon:.6f})")
                                    
                                    # Always callback with current position
                                    # (needed for county tracking even when grid doesn't change)
                                    self.callback(grid, lat, lon)
                                else:
                                    if had_fix:
                                        # Lost fix
                                        print(f"GPS: Lock lost")
                                        if self.lock_callback:
                                            self.lock_callback(False, "GPS lock lost")
                                        had_fix = False
                        
                        except (pynmea2.ParseError, UnicodeDecodeError) as e:
                            # Ignore parse errors, just continue
                            pass
                        except Exception as e:
                            print(f"GPS: Error reading data: {e}")
                            time.sleep(1)
            
            except serial.SerialException as e:
                print(f"GPS: Could not open {self.port}: {e}")
                time.sleep(5)  # Wait before retry
            except Exception as e:
                print(f"GPS: Unexpected error: {e}")
                time.sleep(5)
    
    def get_current_position(self):
        """Get current position (returns None if no fix)"""
        if self.current_grid:
            return {
                'grid': self.current_grid,
                'lat': self.current_lat,
                'lon': self.current_lon
            }
        return None
