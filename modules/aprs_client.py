"""
APRS-IS Client Module
Handles APRS beaconing, nearby station detection, and message alerts via internet.

Uses APRS-IS (internet) so no RF conflict with VHF contesting.
Requires internet connection (Starlink).
"""

import socket
import threading
import time
import re
import math
from datetime import datetime

class APRSClient:
    # APRS-IS servers
    SERVERS = [
        ('rotate.aprs2.net', 14580),
        ('noam.aprs2.net', 14580),
    ]
    
    # Mobile APRS symbols (people/vehicles we care about)
    MOBILE_SYMBOLS = {
        '[': 'Human/Jogger',
        '>': 'Car',
        'v': 'Van',
        'R': 'RV',
        'k': 'Truck',
        'u': 'Truck 18-wheeler',
        '<': 'Motorcycle',
        'j': 'Jeep',
        'b': 'Bicycle',
        'O': 'Balloon',
        's': 'Boat',
        'Y': 'Yacht',
        '^': 'Large Aircraft',
        '\'': 'Small Aircraft',
        'X': 'Helicopter',
    }
    
    # Symbols to ignore (infrastructure, fixed stations)
    IGNORE_SYMBOLS = {
        '_': 'Weather Station',
        '#': 'Digipeater',
        '&': 'HF Gateway',
        '-': 'House/QTH',
        'r': 'Antenna',
        'I': 'TCP/IP',
        'W': 'NWS site',
        'x': 'X symbol',
    }
    
    def __init__(self, callsign, passcode=None, callback_position=None, 
                 callback_message=None, beacon_interval=600):
        """
        Initialize APRS-IS client
        
        Args:
            callsign: Your callsign (e.g., 'N5ZY')
            passcode: APRS-IS passcode (None for receive-only)
            callback_position: Function(callsign, lat, lon, distance_mi, bearing, symbol_desc)
            callback_message: Function(from_call, message, msgno)
            beacon_interval: Seconds between position beacons (default 600 = 10 min)
        """
        self.callsign = callsign.upper()
        self.passcode = passcode or self._calculate_passcode(callsign)
        self.callback_position = callback_position
        self.callback_message = callback_message
        self.beacon_interval = beacon_interval
        
        # Current position
        self.my_lat = None
        self.my_lon = None
        self.my_grid = None
        
        # Beacon comment (can be set before calling start())
        self.beacon_comment = 'VHF Rover'
        
        # Alert radius in km (10 miles = ~16 km)
        self.alert_radius_km = 16
        
        # Track seen stations to avoid repeat alerts
        self.seen_stations = {}  # {callsign: last_alert_time}
        self.seen_stations_lock = threading.Lock()  # Prevent duplicate alerts
        self.alert_cooldown = 300  # 5 minutes between alerts for same station
        
        # Connection state
        self.socket = None
        self.running = False
        self.connected = False
        self.receive_thread = None
        self.beacon_thread = None
        
        # Stats
        self.packets_received = 0
        self.beacons_sent = 0
        self.last_beacon_time = None
    
    def _calculate_passcode(self, callsign):
        """Calculate APRS-IS passcode from callsign"""
        # Standard APRS passcode algorithm
        call = callsign.upper().split('-')[0]  # Remove SSID
        code = 0x73e2
        for i in range(0, len(call), 2):
            code ^= ord(call[i]) << 8
            if i + 1 < len(call):
                code ^= ord(call[i + 1])
        return code & 0x7fff
    
    def set_position(self, lat, lon, grid=None):
        """Update current position for beaconing and distance calculations"""
        first_position = (self.my_lat is None)
        
        self.my_lat = lat
        self.my_lon = lon
        self.my_grid = grid
        
        # Update filter if connected
        if self.connected and self.socket:
            self._send_filter()
            
            # Send beacon immediately if this is our first position fix
            if first_position:
                print("APRS: Got first GPS fix, sending beacon...")
                self._send_beacon()
    
    def _send_filter(self):
        """Send position filter to APRS-IS server"""
        if self.my_lat and self.my_lon:
            # Request packets within radius of our position
            radius_km = self.alert_radius_km + 50  # Get a bit more than alert radius
            filter_str = f"#filter r/{self.my_lat:.4f}/{self.my_lon:.4f}/{radius_km}\r\n"
            try:
                self.socket.send(filter_str.encode())
                print(f"APRS: Filter set to {radius_km}km radius around {self.my_lat:.4f}, {self.my_lon:.4f}")
            except Exception as e:
                print(f"APRS: Error sending filter: {e}")
    
    def start(self):
        """Start APRS-IS connection and threads"""
        self.running = True
        
        # Start receive thread
        self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.receive_thread.start()
        
        # Start beacon thread
        self.beacon_thread = threading.Thread(target=self._beacon_loop, daemon=True)
        self.beacon_thread.start()
        
        print(f"APRS: Started client for {self.callsign}")
    
    def stop(self):
        """Stop APRS-IS connection"""
        self.running = False
        self.connected = False
        
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        
        if self.receive_thread:
            self.receive_thread.join(timeout=2)
        if self.beacon_thread:
            self.beacon_thread.join(timeout=2)
        
        print("APRS: Stopped")
    
    def _connect(self):
        """Connect to APRS-IS server"""
        for server, port in self.SERVERS:
            try:
                print(f"APRS: Connecting to {server}:{port}...")
                
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(30)
                self.socket.connect((server, port))
                
                # Read server greeting
                greeting = self.socket.recv(512).decode('utf-8', errors='ignore')
                print(f"APRS: Server says: {greeting.strip()}")
                
                # Login
                login_str = f"user {self.callsign} pass {self.passcode} vers N5ZY-CoPilot 1.0\r\n"
                self.socket.send(login_str.encode())
                
                # Check login response
                response = self.socket.recv(512).decode('utf-8', errors='ignore')
                print(f"APRS: Login response: {response.strip()}")
                
                if 'logresp' in response.lower() and 'verified' in response.lower():
                    self.connected = True
                    print(f"APRS: Connected and verified to {server}")
                    
                    # Set filter if we have position
                    if self.my_lat and self.my_lon:
                        self._send_filter()
                    
                    return True
                else:
                    print(f"APRS: Login may have failed, continuing anyway")
                    self.connected = True
                    return True
                    
            except Exception as e:
                print(f"APRS: Failed to connect to {server}: {e}")
                continue
        
        print("APRS: Could not connect to any APRS-IS server")
        return False
    
    def _receive_loop(self):
        """Main receive loop - runs in thread"""
        while self.running:
            try:
                if not self.connected:
                    if not self._connect():
                        time.sleep(30)  # Wait before retry
                        continue
                
                # Set timeout for recv
                self.socket.settimeout(60)
                
                # Receive data
                data = self.socket.recv(1024)
                if not data:
                    print("APRS: Connection closed by server")
                    self.connected = False
                    continue
                
                # Process each line
                lines = data.decode('utf-8', errors='ignore').split('\r\n')
                for line in lines:
                    if line and not line.startswith('#'):
                        self._process_packet(line)
                        self.packets_received += 1
                
            except socket.timeout:
                # Send keepalive
                try:
                    self.socket.send(b"#keepalive\r\n")
                except:
                    self.connected = False
            except Exception as e:
                print(f"APRS: Receive error: {e}")
                self.connected = False
                time.sleep(5)
    
    def _process_packet(self, packet):
        """Process an APRS packet"""
        try:
            # Parse basic packet structure: FROM>TO,PATH:DATA
            if '>' not in packet or ':' not in packet:
                return
            
            from_call = packet.split('>')[0].strip()
            data_part = packet.split(':', 1)[-1] if ':' in packet else ''
            
            # Check for message addressed to us
            if data_part.startswith(':'):
                self._check_message(from_call, data_part)
                return
            
            # Check for position packet
            if data_part and data_part[0] in ['!', '/', '@', '=']:
                self._check_position(from_call, data_part)
                
        except Exception as e:
            # Silently ignore parse errors - APRS has lots of malformed packets
            pass
    
    def _check_message(self, from_call, data):
        """Check if this is a message to us"""
        try:
            # Message format: :ADDRESSEE:message{msgno}
            # Addressee is 9 chars padded with spaces
            if len(data) < 11:
                return
            
            addressee = data[1:10].strip().upper()
            
            # Check if it's for us (with or without SSID)
            my_call_base = self.callsign.split('-')[0]
            if addressee == self.callsign or addressee == my_call_base:
                # Skip messages from our EXACT callsign (APRS-IS echoes our own packets)
                # But allow messages from same base call with different SSID (e.g., N5ZY to N5ZY-9)
                if from_call.upper() == self.callsign.upper():
                    return
                
                # Extract message
                msg_part = data[11:]  # After ":ADDRESSEE:"
                
                # Check for message number
                msgno = None
                if '{' in msg_part:
                    msg_part, msgno = msg_part.rsplit('{', 1)
                    msgno = msgno.rstrip('}')
                
                message = msg_part.strip()
                
                # Skip ack messages - handle them silently
                if message.startswith('ack'):
                    print(f"APRS: Received ACK from {from_call}: {message}")
                    return
                
                # Skip rej messages too
                if message.startswith('rej'):
                    print(f"APRS: Received REJ from {from_call}: {message}")
                    return
                
                print(f"APRS: Message from {from_call}: {message}")
                
                if self.callback_message:
                    self.callback_message(from_call, message, msgno)
                
                # Send ACK if we have msgno
                if msgno and self.connected:
                    self._send_ack(from_call, msgno)
                    
        except Exception as e:
            print(f"APRS: Error parsing message: {e}")
    
    def _send_ack(self, to_call, msgno):
        """Send message acknowledgment"""
        try:
            # Format: N5ZY>APRS::TO_CALL  :ack{msgno}
            to_padded = to_call.ljust(9)[:9]
            ack_packet = f"{self.callsign}>APRS,TCPIP*::{to_padded}:ack{msgno}\r\n"
            self.socket.send(ack_packet.encode())
            print(f"APRS: Sent ACK to {to_call} for msg {msgno}")
        except Exception as e:
            print(f"APRS: Error sending ACK: {e}")
    
    def _check_position(self, from_call, data):
        """Check if this is a mobile station nearby"""
        try:
            # Skip if we don't have our own position
            if not self.my_lat or not self.my_lon:
                return
            
            # Skip our own callsign (any SSID) - don't alert about ourselves!
            my_call_base = self.callsign.split('-')[0].upper()
            from_call_base = from_call.split('-')[0].upper()
            if from_call_base == my_call_base:
                return
            
            # Parse position from various formats
            lat, lon, symbol = self._parse_position(data)
            if lat is None or lon is None:
                return
            
            # Get symbol character (last char of symbol pair)
            symbol_char = symbol[-1] if symbol else '?'
            
            # Skip if not a mobile symbol
            if symbol_char in self.IGNORE_SYMBOLS:
                return
            
            # Check if it's a mobile symbol we care about
            symbol_desc = self.MOBILE_SYMBOLS.get(symbol_char, None)
            if not symbol_desc:
                # Not in our mobile list, but also not ignored - might be interesting
                # For now, skip unless it's moving
                return
            
            # Calculate distance
            distance_km = self._haversine(self.my_lat, self.my_lon, lat, lon)
            distance_mi = distance_km * 0.621371
            
            # Check if within alert radius
            if distance_km > self.alert_radius_km:
                return
            
            # Calculate bearing
            bearing = self._bearing(self.my_lat, self.my_lon, lat, lon)
            bearing_str = self._bearing_to_direction(bearing)
            
            # Check cooldown - don't re-alert for same station too often
            # Use lock to prevent duplicate alerts from near-simultaneous packets
            with self.seen_stations_lock:
                now = time.time()
                last_alert = self.seen_stations.get(from_call, 0)
                
                # Must have at least 2 seconds between alerts (catches rapid-fire APRS-IS duplicates)
                # and respect the full cooldown period
                min_gap = max(2, self.alert_cooldown)
                time_since_last = now - last_alert
                
                if time_since_last < min_gap:
                    # Suppressed - too soon since last alert
                    return
                
                # Mark as seen BEFORE alerting (inside lock)
                self.seen_stations[from_call] = now
                self.seen_stations[from_call] = now
            
            # Alert! (outside lock)
            print(f"APRS: Mobile station {from_call} ({symbol_desc}) - {distance_mi:.1f} mi {bearing_str}")
            
            if self.callback_position:
                self.callback_position(from_call, lat, lon, distance_mi, bearing_str, symbol_desc)
                
        except Exception as e:
            pass  # Silently ignore parse errors
    
    def _parse_position(self, data):
        """Parse position from APRS data field"""
        try:
            # Compressed format: /YYYYXXXX$csT
            # Uncompressed format: !DDMM.MMN/DDDMM.MMW$
            
            symbol = None
            lat = None
            lon = None
            
            if len(data) < 10:
                return None, None, None
            
            first_char = data[0]
            
            if first_char in ['/', '@']:
                # With timestamp - skip 7 chars
                data = data[7:]
                first_char = data[0] if data else ''
            
            if first_char in ['!', '=']:
                # Remove leading char
                data = data[1:]
            
            # Try uncompressed format: DDMM.MMN/DDDMM.MMWs
            match = re.match(r'(\d{4}\.\d{2})([NS])(.)((\d{5}\.\d{2})([EW]))(.).*', data)
            if match:
                lat_str, lat_dir, sym1, _, lon_str, lon_dir, sym2 = match.groups()
                
                # Parse latitude
                lat_deg = int(lat_str[:2])
                lat_min = float(lat_str[2:])
                lat = lat_deg + lat_min / 60
                if lat_dir == 'S':
                    lat = -lat
                
                # Parse longitude
                lon_deg = int(lon_str[:3])
                lon_min = float(lon_str[3:])
                lon = lon_deg + lon_min / 60
                if lon_dir == 'W':
                    lon = -lon
                
                symbol = sym1 + sym2
                return lat, lon, symbol
            
            # Try compressed format (starts with symbol table ID)
            if len(data) >= 13 and data[0] in ['/', '\\']:
                sym_table = data[0]
                # Compressed lat/lon in base-91
                # This is complex - simplified version
                pass
            
            return None, None, None
            
        except Exception as e:
            return None, None, None
    
    def _haversine(self, lat1, lon1, lat2, lon2):
        """Calculate distance between two points in km"""
        R = 6371  # Earth's radius in km
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def _bearing(self, lat1, lon1, lat2, lon2):
        """Calculate bearing from point 1 to point 2 in degrees"""
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lon = math.radians(lon2 - lon1)
        
        x = math.sin(delta_lon) * math.cos(lat2_rad)
        y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)
        
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360
    
    def _bearing_to_direction(self, bearing):
        """Convert bearing to compass direction"""
        directions = ['north', 'northeast', 'east', 'southeast', 
                      'south', 'southwest', 'west', 'northwest']
        index = round(bearing / 45) % 8
        return directions[index]
    
    def _beacon_loop(self):
        """Beacon position periodically"""
        # Wait for connection and position, then send initial beacon
        for i in range(12):  # Try for up to 60 seconds
            time.sleep(5)
            if not self.running:
                return
            
            if self.connected and self.my_lat and self.my_lon:
                print(f"APRS: Sending initial beacon (position: {self.my_lat:.4f}, {self.my_lon:.4f})...")
                self._send_beacon()
                break
            else:
                print(f"APRS: Waiting for position... (connected={self.connected}, lat={self.my_lat})")
        
        # Regular beacon loop
        while self.running:
            try:
                # Wait for beacon interval
                time.sleep(self.beacon_interval)
                
                if not self.running:
                    break
                
                # Send beacon if connected and have position
                if self.connected and self.my_lat and self.my_lon:
                    self._send_beacon()
                else:
                    print(f"APRS: Cannot beacon - connected={self.connected}, position={self.my_lat is not None}")
                    
            except Exception as e:
                print(f"APRS: Beacon error: {e}")
    
    def send_beacon_now(self):
        """Send a beacon immediately"""
        if self.connected and self.my_lat and self.my_lon:
            self._send_beacon()
        else:
            print("APRS: Cannot beacon - not connected or no position")
    
    def _send_beacon(self):
        """Send position beacon to APRS-IS"""
        try:
            # Build position string in uncompressed format
            # Latitude: DDMM.MMN
            lat_dir = 'N' if self.my_lat >= 0 else 'S'
            lat_abs = abs(self.my_lat)
            lat_deg = int(lat_abs)
            lat_min = (lat_abs - lat_deg) * 60
            lat_str = f"{lat_deg:02d}{lat_min:05.2f}{lat_dir}"
            
            # Longitude: DDDMM.MMW
            lon_dir = 'E' if self.my_lon >= 0 else 'W'
            lon_abs = abs(self.my_lon)
            lon_deg = int(lon_abs)
            lon_min = (lon_abs - lon_deg) * 60
            lon_str = f"{lon_deg:03d}{lon_min:05.2f}{lon_dir}"
            
            # Symbol: /> = Car
            symbol_table = '/'
            symbol_code = '>'
            
            # Comment - use configured comment, or default
            comment = getattr(self, 'beacon_comment', 'VHF Rover')
            # Append grid if we have one and it's not already in the comment
            if self.my_grid and self.my_grid not in comment:
                comment = f"{comment} {self.my_grid}"
            
            # Build packet: N5ZY>APRS,TCPIP*:=DDMM.MMN/DDDMM.MMW>comment
            packet = f"{self.callsign}>APRS,TCPIP*:={lat_str}{symbol_table}{lon_str}{symbol_code}{comment}\r\n"
            
            self.socket.send(packet.encode())
            self.beacons_sent += 1
            self.last_beacon_time = datetime.now()
            
            print(f"APRS: Beacon sent - {self.my_grid or 'no grid'} ({self.my_lat:.4f}, {self.my_lon:.4f})")
            
        except Exception as e:
            print(f"APRS: Error sending beacon: {e}")
            self.connected = False
    
    def send_message(self, to_call, message):
        """Send an APRS message"""
        if not self.connected:
            print("APRS: Cannot send message - not connected")
            return False
        
        try:
            # Generate message number
            msgno = str(int(time.time()) % 100000)
            
            # Pad addressee to 9 chars
            to_padded = to_call.upper().ljust(9)[:9]
            
            # Build packet
            packet = f"{self.callsign}>APRS,TCPIP*::{to_padded}:{message}{{{msgno}\r\n"
            
            self.socket.send(packet.encode())
            print(f"APRS: Message sent to {to_call}: {message}")
            return True
            
        except Exception as e:
            print(f"APRS: Error sending message: {e}")
            return False
    
    def get_stats(self):
        """Get connection statistics"""
        return {
            'connected': self.connected,
            'packets_received': self.packets_received,
            'beacons_sent': self.beacons_sent,
            'last_beacon': self.last_beacon_time,
            'stations_seen': len(self.seen_stations)
        }
