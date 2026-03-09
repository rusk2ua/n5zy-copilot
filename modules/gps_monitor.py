"""
GPS Monitor Module
Reads GPS data from serial port and calculates Maidenhead grid square
"""

import serial
import pynmea2
import threading
import time
import struct
import ctypes
from datetime import datetime, timezone

# Common NMEA baud rates to try during auto-detection, ordered by likelihood
NMEA_BAUD_RATES = [9600, 4800, 19200, 38400, 57600, 115200]

# Auto-detect timeout per baud rate (seconds)
AUTO_DETECT_TIMEOUT = 2.0

# UBX protocol constants
UBX_SYNC = bytes([0xB5, 0x62])
UBX_CFG_PRT_CLASS = 0x06
UBX_CFG_PRT_ID = 0x00
UBX_CFG_RATE_CLASS = 0x06
UBX_CFG_RATE_ID = 0x08
UBX_CFG_CFG_CLASS = 0x06
UBX_CFG_CFG_ID = 0x09


def ubx_checksum(data):
    """Calculate UBX 8-bit Fletcher checksum.

    Args:
        data: bytes containing class, id, length, and payload

    Returns:
        tuple of (ck_a, ck_b)
    """
    ck_a = 0
    ck_b = 0
    for byte in data:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return ck_a, ck_b


def build_ubx_cfg_prt(baud_rate):
    """Build UBX CFG-PRT message to set UART1 baud rate.

    Args:
        baud_rate: desired baud rate (e.g. 9600, 19200, 38400)

    Returns:
        bytes: complete UBX frame ready to send
    """
    port_id = 1              # UART1
    reserved1 = 0
    tx_ready = 0x0000
    mode = 0x000008D0        # 8 data bits, no parity, 1 stop bit
    in_proto_mask = 0x0007   # UBX + NMEA + RTCM
    out_proto_mask = 0x0003  # UBX + NMEA
    flags = 0x0000
    reserved2 = 0x0000

    payload = struct.pack('<BBHIIHHhH',
        port_id, reserved1, tx_ready, mode, baud_rate,
        in_proto_mask, out_proto_mask, flags, reserved2)

    header = struct.pack('<BBH', UBX_CFG_PRT_CLASS, UBX_CFG_PRT_ID, len(payload))
    ck_a, ck_b = ubx_checksum(header + payload)

    return UBX_SYNC + header + payload + bytes([ck_a, ck_b])


def build_ubx_cfg_rate(meas_rate_ms=1000, nav_rate=1, time_ref=1):
    """Build UBX CFG-RATE message to set measurement/navigation rate.

    Args:
        meas_rate_ms: measurement interval in ms (1000=1Hz, 200=5Hz, 100=10Hz)
        nav_rate: number of measurements per nav solution (typically 1)
        time_ref: time reference (0=UTC, 1=GPS)

    Returns:
        bytes: complete UBX frame ready to send
    """
    payload = struct.pack('<HHH', meas_rate_ms, nav_rate, time_ref)
    header = struct.pack('<BBH', UBX_CFG_RATE_CLASS, UBX_CFG_RATE_ID, len(payload))
    ck_a, ck_b = ubx_checksum(header + payload)

    return UBX_SYNC + header + payload + bytes([ck_a, ck_b])


def build_ubx_cfg_cfg(save=True):
    """Build UBX CFG-CFG message to save/load configuration.

    Args:
        save: if True, saves current config to all non-volatile storage

    Returns:
        bytes: complete UBX frame ready to send
    """
    clear_mask = 0x00000000
    save_mask = 0x0000001F if save else 0x00000000  # all sections
    load_mask = 0x00000000

    payload = struct.pack('<III', clear_mask, save_mask, load_mask)
    header = struct.pack('<BBH', UBX_CFG_CFG_CLASS, UBX_CFG_CFG_ID, len(payload))
    ck_a, ck_b = ubx_checksum(header + payload)

    return UBX_SYNC + header + payload + bytes([ck_a, ck_b])


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
    def __init__(self, port, callback, grid_precision=4, lock_callback=None,
                 baudrate=None, status_callback=None):
        """
        Initialize GPS monitor

        Args:
            port: COM port string (e.g., 'COM3')
            callback: Function to call with (grid, lat, lon) when position updates
            grid_precision: 4 or 6 character grid precision (default 4 for VHF contests)
            lock_callback: Function to call with (has_lock, message) when lock status changes
            baudrate: Desired baud rate (None = auto-detect on connect)
            status_callback: Function to call with (status_dict) for baud/rate status updates
        """
        self.port = port
        self.callback = callback
        self.lock_callback = lock_callback
        self.status_callback = status_callback
        self.grid_precision = grid_precision
        self.running = False
        self.thread = None
        self.current_grid = None
        self.current_lat = None
        self.current_lon = None

        # Baud rate management
        self.baudrate = baudrate              # None = auto-detect on connect
        self.detected_baudrate = None         # Actual detected/active rate
        self.update_rate_hz = 1               # Current NMEA update rate
        self._serial_lock = threading.Lock()  # Protect serial port access

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

        # GPS Time Sync data (from RMC sentences)
        self.gps_datetime_utc = None    # Full datetime from GPRMC (date + time)
        self.gps_fix_quality = 0        # 0=none, 1=GPS, 2=DGPS
        self.last_rmc_date = None       # datestamp from last RMC sentence
        self.last_rmc_time = None       # timestamp from last RMC sentence

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

    def auto_detect_baudrate(self, port_name=None):
        """Try each common baud rate and return the first that produces valid NMEA.

        Args:
            port_name: COM port to test (defaults to self.port)

        Returns:
            int: detected baud rate, or None if no valid NMEA found
        """
        port_name = port_name or self.port

        for rate in NMEA_BAUD_RATES:
            try:
                print(f"GPS: Trying {rate} baud on {port_name}...")
                if self.status_callback:
                    self.status_callback({'detecting': True, 'trying_rate': rate})

                with serial.Serial(port_name, baudrate=rate, timeout=0.5) as ser:
                    ser.reset_input_buffer()
                    start = time.time()
                    valid_count = 0

                    while time.time() - start < AUTO_DETECT_TIMEOUT:
                        if not self.running:
                            return None  # Cancelled

                        try:
                            line = ser.readline().decode('ascii', errors='ignore').strip()
                            if line.startswith('$') and ('*' in line) and len(line) > 10:
                                valid_count += 1
                                if valid_count >= 2:
                                    print(f"GPS: Detected {rate} baud ({valid_count} valid NMEA sentences)")
                                    self.detected_baudrate = rate
                                    if self.status_callback:
                                        self.status_callback({
                                            'detecting': False,
                                            'detected_rate': rate
                                        })
                                    return rate
                        except (UnicodeDecodeError, serial.SerialException):
                            pass

            except serial.SerialException as e:
                print(f"GPS: Could not open {port_name} at {rate}: {e}")
                continue

        print(f"GPS: Auto-detect failed - no valid NMEA on any baud rate")
        if self.status_callback:
            self.status_callback({'detecting': False, 'detected_rate': None})
        return None

    def change_baudrate(self, new_rate):
        """Send UBX CFG-PRT to change the GPS device's baud rate.

        Sends the command at the current baud rate, waits for the device
        to switch, then verifies NMEA at the new rate and saves to flash.

        Args:
            new_rate: desired baud rate (4800, 9600, 19200, 38400)

        Returns:
            bool: True if baud rate change succeeded
        """
        current_rate = self.detected_baudrate or self.baudrate or 9600

        if new_rate == current_rate:
            print(f"GPS: Already at {new_rate} baud")
            return True

        print(f"GPS: Changing baud rate from {current_rate} to {new_rate}...")
        if self.status_callback:
            self.status_callback({'changing_rate': True, 'target_rate': new_rate})

        try:
            # Step 1: Open at current rate and send CFG-PRT
            with serial.Serial(self.port, baudrate=current_rate, timeout=1) as ser:
                cfg_prt = build_ubx_cfg_prt(new_rate)
                ser.write(cfg_prt)
                ser.flush()
                print(f"GPS: Sent CFG-PRT ({len(cfg_prt)} bytes) for {new_rate} baud")

            # Step 2: Wait for GPS to switch
            time.sleep(0.5)

            # Step 3: Reopen at new rate and verify NMEA
            with serial.Serial(self.port, baudrate=new_rate, timeout=1) as ser:
                ser.reset_input_buffer()
                start = time.time()
                valid_count = 0

                while time.time() - start < 3.0:
                    try:
                        line = ser.readline().decode('ascii', errors='ignore').strip()
                        if line.startswith('$') and ('*' in line) and len(line) > 10:
                            valid_count += 1
                            if valid_count >= 2:
                                break
                    except (UnicodeDecodeError, serial.SerialException):
                        pass

                if valid_count >= 2:
                    print(f"GPS: Baud rate change verified at {new_rate}")
                    self.detected_baudrate = new_rate
                    self.baudrate = new_rate

                    # Step 4: Save to GPS non-volatile memory
                    cfg_save = build_ubx_cfg_cfg(save=True)
                    ser.write(cfg_save)
                    ser.flush()
                    print(f"GPS: Sent CFG-CFG to save settings to flash")
                    time.sleep(0.2)

                    if self.status_callback:
                        self.status_callback({
                            'changing_rate': False,
                            'detected_rate': new_rate,
                            'success': True
                        })
                    return True
                else:
                    print(f"GPS: Baud rate change failed - no NMEA at {new_rate}")
                    if self.status_callback:
                        self.status_callback({
                            'changing_rate': False,
                            'detected_rate': current_rate,
                            'success': False,
                            'error': f'No NMEA data at {new_rate} baud'
                        })
                    return False

        except Exception as e:
            print(f"GPS: Baud rate change error: {e}")
            if self.status_callback:
                self.status_callback({
                    'changing_rate': False,
                    'success': False,
                    'error': str(e)
                })
            return False

    def change_update_rate(self, hz):
        """Send UBX CFG-RATE to change NMEA output rate.

        Args:
            hz: desired update rate (1, 2, 5, or 10 Hz)

        Returns:
            bool: True if rate change command was sent successfully
        """
        rate_map = {1: 1000, 2: 500, 5: 200, 10: 100}
        if hz not in rate_map:
            print(f"GPS: Invalid update rate {hz}Hz, must be 1, 2, 5, or 10")
            return False

        meas_rate_ms = rate_map[hz]
        current_baud = self.detected_baudrate or self.baudrate or 9600

        if hz > 1 and current_baud < 19200:
            print(f"GPS: Warning - {hz}Hz update rate may be unreliable at {current_baud} baud")

        try:
            with serial.Serial(self.port, baudrate=current_baud, timeout=1) as ser:
                # Send rate change
                cfg_rate = build_ubx_cfg_rate(meas_rate_ms)
                ser.write(cfg_rate)
                ser.flush()
                time.sleep(0.1)

                # Save to flash
                cfg_save = build_ubx_cfg_cfg(save=True)
                ser.write(cfg_save)
                ser.flush()

            self.update_rate_hz = hz
            print(f"GPS: Update rate set to {hz}Hz ({meas_rate_ms}ms)")

            if self.status_callback:
                self.status_callback({'update_rate_hz': hz})

            return True

        except Exception as e:
            print(f"GPS: Update rate change error: {e}")
            return False

    def _monitor_loop(self):
        """Main monitoring loop (runs in separate thread)"""
        while self.running:
            try:
                # Determine baud rate
                active_rate = self.baudrate  # May be None (auto-detect)

                if active_rate is None:
                    print(f"GPS: Auto-detecting baud rate on {self.port}...")
                    active_rate = self.auto_detect_baudrate()

                    if active_rate is None:
                        print(f"GPS: Auto-detect failed, defaulting to 9600")
                        active_rate = 9600

                    self.baudrate = active_rate
                    self.detected_baudrate = active_rate

                # Open serial connection at determined baud rate
                with serial.Serial(self.port, baudrate=active_rate, timeout=1) as ser:
                    print(f"GPS: Connected to {self.port} at {active_rate} baud")
                    self.detected_baudrate = active_rate
                    had_fix = False  # Track if we previously had a fix

                    if self.status_callback:
                        self.status_callback({
                            'connected': True,
                            'detected_rate': active_rate,
                            'port': self.port
                        })
                    
                    while self.running:
                        try:
                            line = ser.readline().decode('ascii', errors='ignore').strip()
                            
                            # Parse GGA sentences for position, altitude, satellites
                            if line.startswith('$GPGGA') or line.startswith('$GNGGA'):
                                msg = pynmea2.parse(line)
                                
                                # Always update satellite count (even before lock)
                                if hasattr(msg, 'num_sats'):
                                    try:
                                        self.satellites = int(msg.num_sats) if msg.num_sats else 0
                                    except:
                                        self.satellites = 0

                                # Check fix quality (0=no fix, 1=GPS fix, 2=DGPS fix, etc.)
                                if hasattr(msg, 'gps_qual'):
                                    try:
                                        self.gps_fix_quality = int(msg.gps_qual) if msg.gps_qual else 0
                                    except (ValueError, TypeError):
                                        self.gps_fix_quality = 0
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
                            
                            # Parse RMC sentences for speed, heading, and full date+time
                            elif line.startswith('$GPRMC') or line.startswith('$GNRMC'):
                                try:
                                    msg = pynmea2.parse(line)
                                    if hasattr(msg, 'spd_over_grnd') and msg.spd_over_grnd is not None:
                                        self.speed_knots = float(msg.spd_over_grnd)
                                        self.speed_mph = self.speed_knots * 1.15078
                                    if hasattr(msg, 'true_course') and msg.true_course is not None:
                                        self.heading = float(msg.true_course)
                                        self.compass = self._heading_to_compass(self.heading)
                                    # Extract full UTC date+time for GPS Time Sync
                                    if hasattr(msg, 'timestamp') and msg.timestamp and hasattr(msg, 'datestamp') and msg.datestamp:
                                        try:
                                            self.last_rmc_date = msg.datestamp
                                            self.last_rmc_time = msg.timestamp
                                            self.gps_datetime_utc = datetime.combine(
                                                msg.datestamp, msg.timestamp
                                            ).replace(tzinfo=timezone.utc)
                                        except (TypeError, ValueError):
                                            pass
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
                # If saved baud rate fails, try auto-detect on next attempt
                if self.baudrate is not None:
                    print(f"GPS: Will try auto-detect on next attempt")
                    self.baudrate = None
                if self.status_callback:
                    self.status_callback({'connected': False, 'error': str(e)})
                time.sleep(5)  # Wait before retry
            except Exception as e:
                print(f"GPS: Unexpected error: {e}")
                time.sleep(5)
    
    @staticmethod
    def is_admin():
        """Check if the current process is running with Administrator privileges."""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except (AttributeError, OSError):
            # Not on Windows or API not available
            return False

    def sync_system_clock(self):
        """Set Windows system clock from GPS UTC time.

        Requires Administrator privileges. Uses GPRMC date+time.

        Returns:
            dict: {
                'success': bool,
                'offset_ms': float (ms delta before sync),
                'gps_time': datetime,
                'fix_quality': int,
                'satellites': int,
                'error': str (if failed)
            }
        """
        # Require GPS fix
        if self.gps_fix_quality < 1:
            return {
                'success': False,
                'error': 'No GPS fix — cannot sync clock',
                'fix_quality': 0,
                'satellites': self.satellites,
            }

        gps_utc = self.gps_datetime_utc
        if gps_utc is None:
            return {
                'success': False,
                'error': 'No GPS date/time available (waiting for GPRMC)',
                'fix_quality': self.gps_fix_quality,
                'satellites': self.satellites,
            }

        # Check admin
        if not self.is_admin():
            return {
                'success': False,
                'error': 'Administrator privileges required',
                'fix_quality': self.gps_fix_quality,
                'satellites': self.satellites,
            }

        # Calculate offset before setting
        system_utc = datetime.now(timezone.utc)
        offset_delta = gps_utc - system_utc
        offset_ms = offset_delta.total_seconds() * 1000.0

        try:
            # Build SYSTEMTIME structure for SetSystemTime
            # SYSTEMTIME: WORD wYear, wMonth, wDayOfWeek, wDay,
            #             wHour, wMinute, wSecond, wMilliseconds
            class SYSTEMTIME(ctypes.Structure):
                _fields_ = [
                    ('wYear', ctypes.c_ushort),
                    ('wMonth', ctypes.c_ushort),
                    ('wDayOfWeek', ctypes.c_ushort),
                    ('wDay', ctypes.c_ushort),
                    ('wHour', ctypes.c_ushort),
                    ('wMinute', ctypes.c_ushort),
                    ('wSecond', ctypes.c_ushort),
                    ('wMilliseconds', ctypes.c_ushort),
                ]

            st = SYSTEMTIME()
            st.wYear = gps_utc.year
            st.wMonth = gps_utc.month
            st.wDayOfWeek = gps_utc.weekday()  # Monday=0
            st.wDay = gps_utc.day
            st.wHour = gps_utc.hour
            st.wMinute = gps_utc.minute
            st.wSecond = gps_utc.second
            # Use microseconds from GPS time if available
            st.wMilliseconds = gps_utc.microsecond // 1000

            result = ctypes.windll.kernel32.SetSystemTime(ctypes.byref(st))
            if result == 0:
                err_code = ctypes.GetLastError()
                return {
                    'success': False,
                    'error': f'SetSystemTime failed (error {err_code})',
                    'offset_ms': offset_ms,
                    'gps_time': gps_utc,
                    'fix_quality': self.gps_fix_quality,
                    'satellites': self.satellites,
                }

            fix_label = {0: 'None', 1: 'GPS', 2: 'DGPS'}.get(self.gps_fix_quality, str(self.gps_fix_quality))
            print(f"GPS Time Sync: Clock set to {gps_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                  f"(offset was {offset_ms:+.0f}ms, fix={fix_label}, sats={self.satellites})")

            return {
                'success': True,
                'offset_ms': offset_ms,
                'gps_time': gps_utc,
                'fix_quality': self.gps_fix_quality,
                'satellites': self.satellites,
            }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'fix_quality': self.gps_fix_quality,
                'satellites': self.satellites,
            }

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
        """Get all GPS data for GPS Logger tab display.
        Returns partial data (satellites, has_fix=False) when no lock yet."""
        if self.current_lat is None or self.current_lon is None:
            # No fix yet, but return satellite count so UI can show progress
            return {
                'has_fix': False,
                'satellites': self.satellites,
                'lat': None,
                'lon': None,
                'grid_6char': None,
                'baudrate': self.detected_baudrate or self.baudrate,
                'update_rate_hz': self.update_rate_hz,
                'gps_datetime_utc': self.gps_datetime_utc,
                'fix_quality': self.gps_fix_quality,
            }
        
        # Calculate average speed if we have track data
        avg_speed = None
        if self.track_start_time and self.track_distance_mi > 0:
            import datetime
            elapsed = (datetime.datetime.now() - self.track_start_time).total_seconds() / 3600  # hours
            if elapsed > 0:
                avg_speed = self.track_distance_mi / elapsed
        
        return {
            'has_fix': True,
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
            'total_distance_mi': self.track_distance_mi,
            'baudrate': self.detected_baudrate or self.baudrate,
            'update_rate_hz': self.update_rate_hz,
            'gps_datetime_utc': self.gps_datetime_utc,
            'fix_quality': self.gps_fix_quality,
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
