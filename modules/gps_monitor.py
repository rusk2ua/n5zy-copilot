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
        
        # Extended GPS data for GPS Logger tab
        self.altitude_m = None
        self.altitude_ft = None
        self.satellites = 0
        self.hdop = None
        self.gps_time = None
        self.speed_knots = None
        self.speed_mph = None
        self.heading = None
        self.compass = None
        
        # Track statistics
        self.track_distance_mi = 0.0
        self.track_points = 0
        self.track_start_time = None
        self.last_track_lat = None
        self.last_track_lon = None
        self.grid_6char = None
    
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
                            
                            # Parse GGA sentences for position, altitude, satellites
                            if line.startswith('$GPGGA') or line.startswith('$GNGGA'):
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
                                    
                                    # Store extended data
                                    if hasattr(msg, 'altitude') and msg.altitude is not None:
                                        self.altitude_m = float(msg.altitude)
                                        self.altitude_ft = self.altitude_m * 3.28084
                                    
                                    if hasattr(msg, 'num_sats'):
                                        try:
                                            self.satellites = int(msg.num_sats) if msg.num_sats else 0
                                        except:
                                            self.satellites = 0
                                    
                                    if hasattr(msg, 'horizontal_dil') and msg.horizontal_dil:
                                        try:
                                            self.hdop = float(msg.horizontal_dil)
                                        except:
                                            pass
                                    
                                    if hasattr(msg, 'timestamp') and msg.timestamp:
                                        self.gps_time = msg.timestamp
                                    
                                    # Update track distance
                                    if self.last_track_lat is not None and self.last_track_lon is not None:
                                        dist = self._haversine_miles(self.last_track_lat, self.last_track_lon, lat, lon)
                                        if dist < 10:  # Sanity check - ignore jumps > 10 miles
                                            self.track_distance_mi += dist
                                    self.last_track_lat = lat
                                    self.last_track_lon = lon
                                    self.track_points += 1
                                    
                                    # Calculate full 6-char grid, then truncate to precision
                                    full_grid = latlon_to_grid(lat, lon)
                                    grid = full_grid[:self.grid_precision]
                                    
                                    # Always update lat/lon for GPS Logger display
                                    self.current_lat = lat
                                    self.current_lon = lon
                                    
                                    # Store full 6-char grid for GPS Logger
                                    self.grid_6char = full_grid
                                    
                                    # Only callback if grid changed (at configured precision)
                                    if grid != self.current_grid:
                                        self.current_grid = grid
                                        print(f"GPS: Position update - {grid} ({lat:.6f}, {lon:.6f})")
                                        self.callback(grid, lat, lon)
                                else:
                                    if had_fix:
                                        # Lost fix
                                        print(f"GPS: Lock lost")
                                        if self.lock_callback:
                                            self.lock_callback(False, "GPS lock lost")
                                        had_fix = False
                            
                            # Parse RMC sentences for speed and heading
                            elif line.startswith('$GPRMC') or line.startswith('$GNRMC'):
                                try:
                                    msg = pynmea2.parse(line)
                                    if hasattr(msg, 'spd_over_grnd') and msg.spd_over_grnd is not None:
                                        self.speed_knots = float(msg.spd_over_grnd)
                                        self.speed_mph = self.speed_knots * 1.15078
                                    if hasattr(msg, 'true_course') and msg.true_course is not None:
                                        self.heading = float(msg.true_course)
                                        self.compass = self._heading_to_compass(self.heading)
                                except:
                                    pass
                            
                            # Parse VTG sentences for speed/heading (alternative)
                            elif line.startswith('$GPVTG') or line.startswith('$GNVTG'):
                                try:
                                    msg = pynmea2.parse(line)
                                    if hasattr(msg, 'spd_over_grnd_kmph') and msg.spd_over_grnd_kmph:
                                        self.speed_mph = float(msg.spd_over_grnd_kmph) * 0.621371
                                    if hasattr(msg, 'true_track') and msg.true_track:
                                        self.heading = float(msg.true_track)
                                        self.compass = self._heading_to_compass(self.heading)
                                except:
                                    pass
                        
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
    
    def get_full_data(self):
        """Get all GPS data for GPS Logger tab display"""
        if self.current_lat is None or self.current_lon is None:
            return None
        
        # Calculate average speed if we have track data
        avg_speed = None
        if self.track_start_time and self.track_distance_mi > 0:
            import datetime
            elapsed = (datetime.datetime.now() - self.track_start_time).total_seconds() / 3600  # hours
            if elapsed > 0:
                avg_speed = self.track_distance_mi / elapsed
        
        return {
            'lat': self.current_lat,
            'lon': self.current_lon,
            'grid_6char': getattr(self, 'grid_6char', self.current_grid),
            'altitude_m': self.altitude_m,
            'altitude_ft': self.altitude_ft,
            'satellites': self.satellites,
            'hdop': self.hdop,
            'gps_time': self.gps_time,
            'speed_mph': self.speed_mph,
            'heading': self.heading,
            'compass': self.compass,
            'avg_speed_mph': avg_speed,
            'total_distance_mi': self.track_distance_mi
        }
    
    def reset_track_stats(self):
        """Reset track statistics (called when starting a new track)"""
        import datetime
        self.track_distance_mi = 0.0
        self.track_points = 0
        self.track_start_time = datetime.datetime.now()
        self.last_track_lat = self.current_lat
        self.last_track_lon = self.current_lon
    
    def _haversine_miles(self, lat1, lon1, lat2, lon2):
        """Calculate distance between two points in miles"""
        import math
        R = 3959  # Earth's radius in miles
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def _heading_to_compass(self, heading):
        """Convert heading in degrees to compass direction"""
        directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                      'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
        index = int((heading + 11.25) / 22.5) % 16
        return directions[index]
