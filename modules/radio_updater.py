"""
Radio Updater Module
Sends grid square updates to WSJT-X instances and contest loggers (N1MM+ or N3FJP) 

Implements WSJT-X NetworkMessage protocol without external dependencies
"""

import socket
import struct
import datetime
import threading
import time

class RadioUpdater:
    # WSJT-X Protocol Constants
    MAGIC = 0xADBCCBDA
    SCHEMA = 3  # Schema 3 for WSJT-X 2.x (Qt 5.4+)
    
    # Message Types
    MSG_HEARTBEAT = 0
    MSG_STATUS = 1
    MSG_DECODE = 2
    MSG_CLEAR = 3
    MSG_REPLY = 4
    MSG_QSO_LOGGED = 5
    MSG_CLOSE = 6
    MSG_REPLAY = 7
    MSG_HALT_TX = 8
    MSG_FREE_TEXT = 9
    MSG_WSPR_DECODE = 10
    MSG_LOCATION = 11  # LocationChange message
    MSG_LOGGED_ADIF = 12
    MSG_HIGHLIGHT_CALLSIGN = 13
    
    @staticmethod
    def to_adif_latitude(latitude):
        """
        Convert decimal degrees to ADIF latitude format.
        Format: N/S DDD MM.MMM (e.g., "N035 28.056")
        """
        hemisphere = "N" if latitude >= 0 else "S"
        abs_lat = abs(latitude)
        degrees = int(abs_lat)
        minutes = (abs_lat - degrees) * 60
        return f"{hemisphere}{degrees:03d} {minutes:06.3f}"
    
    @staticmethod
    def to_adif_longitude(longitude):
        """
        Convert decimal degrees to ADIF longitude format.
        Format: E/W DDD MM.MMM (e.g., "W097 30.984")
        """
        hemisphere = "E" if longitude >= 0 else "W"
        abs_lon = abs(longitude)
        degrees = int(abs_lon)
        minutes = (abs_lon - degrees) * 60
        return f"{hemisphere}{degrees:03d} {minutes:06.3f}"
    
    @staticmethod
    def map_contest_id(contest_name):
        """
        Map N1MM contest name to ADIF CONTEST_ID.
        N1MM uses various names, map to ADIF standard IDs.
        """
        if not contest_name:
            return ""
        
        name = contest_name.upper()
        
        if "VHF" in name and "JAN" in name:
            return "ARRL-VHF-JAN"
        if "VHF" in name and "JUN" in name:
            return "ARRL-VHF-JUN"
        if "VHF" in name and "SEP" in name:
            return "ARRL-VHF-SEP"
        if "222" in name:
            return "ARRL-222"
        if "UHF" in name:
            return "ARRL-UHF-AUG"
        if "SPRINT" in name:
            return "CSVHF-SPRINT"
        if "OK" in name and "QSO" in name:
            return "OK-QSO-PARTY"
        if "CQ" in name and "VHF" in name:
            return "CQ-VHF"
        
        # Return original if no mapping found
        return contest_name
    
    @staticmethod
    def is_rover_call(callsign):
        """Check if callsign has /R rover suffix"""
        return callsign.upper().endswith("/R")
    
    def __init__(self, wsjt_instances, n1mm_host='127.0.0.1', n1mm_port=52001, 
                 n3fjp_host='127.0.0.1', n3fjp_port=1100, contest_logger='n1mm',
                 qso_callback=None, location_stamper=None):
        """
        Initialize radio updater
        
        Args:
            wsjt_instances: List of WSJT-X instance configs
            n1mm_host: N1MM+ TCP host
            n1mm_port: N1MM+ JTDX TCP port (default 52001)
            n3fjp_host: N3FJP API host
            n3fjp_port: N3FJP API TCP port (default 1100)
            contest_logger: 'n1mm' or 'n3fjp'
            qso_callback: Function to call when QSO is logged (qso_data dict)
            location_stamper: Function to stamp GPS location onto QSO for ADIF (qso_data) -> qso_data
        """
        self.wsjt_instances = wsjt_instances
        self.n1mm_host = n1mm_host
        self.n1mm_port = n1mm_port
        self.n3fjp_host = n3fjp_host
        self.n3fjp_port = n3fjp_port
        self.contest_logger = contest_logger
        self.qso_callback = qso_callback
        self.location_stamper = location_stamper  # For GPS-stamping ADIF records
        
        # Track WSJT-X instance IDs (learned from HeartBeat packets)
        self.wsjtx_ids = {}  # {port: (wsjtx_id, last_seen)}
        
        # Track logged QSOs to avoid duplicates
        self.logged_qsos = set()  # Set of (datetime, callsign, band) tuples
        
        # QSO relay queue - thread-safe buffer for logger relay
        import queue
        self.qso_queue = queue.Queue()
        self.relay_thread = None
        
        # UDP sockets for listening to WSJT-X (one per configured port)
        self.listen_socks = []
        self.running = False
        self.listen_threads = []
        
        # Current grid (for resending after jt9.exe restart)
        self.current_grid = None
        
        # jt9.exe process monitoring
        self.jt9_pids = set()  # Track known jt9.exe PIDs
        self.jt9_monitor_thread = None
        
        # Start listening for WSJT-X packets on ALL configured ports
        self.start_listener()
        
        # Start the N1MM+ relay thread
        self._start_relay_thread()
        
        # Start jt9.exe process monitor
        self._start_jt9_monitor()
    
    def _start_relay_thread(self):
        """Start the QSO relay thread"""
        self.relay_thread = threading.Thread(target=self._relay_loop, daemon=True)
        self.relay_thread.start()
        logger_name = "N3FJP" if self.contest_logger == 'n3fjp' else "N1MM+"
        print(f"Radio Update: Started {logger_name} QSO relay thread")
    
    def _relay_loop(self):
        """
        Relay thread - sends QSOs to contest logger one at a time with delays.
        This prevents race conditions when multiple WSJT-X instances
        log QSOs at nearly the same moment.
        """
        qso_offset = 0  # Offset counter to differentiate same-callsign QSOs
        
        while self.running:
            try:
                # Wait for a QSO (with timeout so we can check self.running)
                try:
                    qso_data = self.qso_queue.get(timeout=1.0)
                except:
                    continue
                
                # Add offset to differentiate QSOs with same callsign
                qso_data['_time_offset'] = qso_offset
                qso_offset += 1
                if qso_offset > 59:  # Reset after 60 seconds
                    qso_offset = 0
                
                # Send to appropriate logger based on configuration
                if self.contest_logger == 'n3fjp':
                    success = self._send_qso_to_n3fjp(qso_data)
                else:
                    success = self._send_qso_to_n1mm(qso_data)
                
                # Mark as done
                self.qso_queue.task_done()
                
                # CRITICAL: Wait before sending next QSO
                # This gives the logger time to process each QSO
                remaining = self.qso_queue.qsize()
                if remaining > 0:
                    print(f"Radio Update: Waiting 500ms before next QSO ({remaining} remaining in queue)")
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Radio Update: Relay thread error: {e}")
                time.sleep(1)

    
    def start_listener(self):
        """Start listening for WSJT-X HeartBeat packets on all configured ports"""
        self.running = True
        
        # Get unique ports from configured instances
        ports = set()
        for instance in self.wsjt_instances:
            port = instance.get('udp_port', 2237)
            ports.add(port)
            print(f"Radio Update: Config has '{instance.get('name', 'Unknown')}' on UDP port {port}")
        
        # Start a listener thread for each port
        for port in ports:
            thread = threading.Thread(target=self._listen_loop, args=(port,), daemon=True)
            thread.start()
            self.listen_threads.append(thread)
            print(f"Radio Update: Started listener thread for port {port}")
    
    def stop_listener(self):
        """Stop the listener threads"""
        self.running = False
        for thread in self.listen_threads:
            if thread:
                thread.join(timeout=2)
        for sock in self.listen_socks:
            if sock:
                sock.close()
    
    def _start_jt9_monitor(self):
        """Start monitoring jt9.exe processes for restarts"""
        self.jt9_monitor_thread = threading.Thread(target=self._jt9_monitor_loop, daemon=True)
        self.jt9_monitor_thread.start()
        print("Radio Update: Started jt9.exe process monitor")
    
    def _get_jt9_pids(self):
        """Get set of current jt9.exe process IDs"""
        pids = set()
        try:
            import subprocess
            # Use tasklist on Windows to find jt9.exe processes
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq jt9.exe', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split('\n'):
                if line and 'jt9.exe' in line.lower():
                    # CSV format: "jt9.exe","1234","Console","1","12,345 K"
                    parts = line.split(',')
                    if len(parts) >= 2:
                        try:
                            pid = int(parts[1].strip().strip('"'))
                            pids.add(pid)
                        except ValueError:
                            pass
        except Exception as e:
            # Silently fail - might not be on Windows or tasklist unavailable
            pass
        return pids
    
    def _jt9_monitor_loop(self):
        """Monitor jt9.exe processes and resend grid if they restart"""
        import time
        
        # Initial scan
        self.jt9_pids = self._get_jt9_pids()
        if self.jt9_pids:
            print(f"Radio Update: Found {len(self.jt9_pids)} jt9.exe process(es): {self.jt9_pids}")
        
        while self.running:
            try:
                time.sleep(3)  # Check every 3 seconds
                
                current_pids = self._get_jt9_pids()
                
                # Check for new PIDs (process restarted)
                new_pids = current_pids - self.jt9_pids
                gone_pids = self.jt9_pids - current_pids
                
                if new_pids and self.current_grid:
                    print(f"Radio Update: jt9.exe restarted! New PID(s): {new_pids}, Gone: {gone_pids}")
                    print(f"Radio Update: Resending grid {self.current_grid} to all WSJT-X instances...")
                    
                    # Wait a moment for WSJT-X to stabilize after mode change
                    time.sleep(2)
                    
                    # Resend grid to all instances
                    self._resend_grid_to_all()
                
                self.jt9_pids = current_pids
                
            except Exception as e:
                print(f"Radio Update: jt9 monitor error: {e}")
                time.sleep(5)
    
    def _resend_grid_to_all(self):
        """Resend current grid to all discovered WSJT-X instances"""
        if not self.current_grid:
            return
        
        for source_port, (wsjtx_id, last_seen) in self.wsjtx_ids.items():
            try:
                self._send_wsjt_location(wsjtx_id, source_port, self.current_grid)
            except Exception as e:
                print(f"Radio Update: Error resending to '{wsjtx_id}': {e}")
    
    def _listen_loop(self, port):
        """Listen for WSJT-X HeartBeat packets on a specific port"""
        try:
            listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # On Windows, also set SO_BROADCAST and SO_EXCLUSIVEADDRUSE=0
            try:
                listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except:
                pass  # Not all platforms support this
            
            # Bind to all interfaces on the specified port
            listen_sock.bind(('', port))
            listen_sock.settimeout(1.0)
            self.listen_socks.append(listen_sock)
            
            print(f"Radio Update: Listening for WSJT-X broadcasts on port {port}")
            
            while self.running:
                try:
                    data, addr = listen_sock.recvfrom(4096)
                    
                    # Extract source port - THIS is where WSJT-X is listening!
                    source_ip, source_port = addr
                    
                    # Try to parse packet
                    try:
                        msg_type, wsjtx_id = self._parse_packet_header(data)
                        
                        # If it's a HeartBeat, save the ID AND source port
                        if msg_type == self.MSG_HEARTBEAT:
                            # Match this to a configured instance by port
                            # For now, just save it with the source port as key
                            if source_port not in self.wsjtx_ids:
                                print(f"Radio Update: Discovered WSJT-X instance '{wsjtx_id}' listening on port {source_port}")
                            
                            self.wsjtx_ids[source_port] = (wsjtx_id, time.time())
                        
                        # If it's a QSO Logged message, parse and relay
                        elif msg_type == self.MSG_QSO_LOGGED:
                            try:
                                qso_data = self._parse_qso_logged(data)
                                if qso_data:
                                    self._handle_qso_logged(qso_data, wsjtx_id)
                            except Exception as e:
                                print(f"Radio Update: Error parsing QSO Logged: {e}")
                        
                        # If it's an ADIF Logged message, also handle it
                        elif msg_type == self.MSG_LOGGED_ADIF:
                            try:
                                adif_data = self._parse_adif_logged(data)
                                if adif_data:
                                    self._handle_adif_logged(adif_data, wsjtx_id)
                            except Exception as e:
                                print(f"Radio Update: Error parsing ADIF Logged: {e}")
                    
                    except Exception as e:
                        # Ignore packets we can't parse
                        pass
                
                except socket.timeout:
                    # No data, continue
                    pass
                except Exception as e:
                    if self.running:  # Only print errors if we're still supposed to be running
                        print(f"Radio Update: Error in listener on port {port}: {e}")
                        time.sleep(1)
        
        except Exception as e:
            print(f"Radio Update: Could not start listener on port {port}: {e}")
    
    def _parse_packet_header(self, data):
        """
        Parse WSJT-X packet header to get message type and ID
        
        Returns:
            (msg_type, wsjtx_id)
        """
        if len(data) < 12:
            raise ValueError("Packet too short")
        
        # Parse header: Magic (4) + Schema (4) + Type (4)
        magic, schema, msg_type = struct.unpack('>III', data[0:12])
        
        if magic != self.MAGIC:
            raise ValueError("Invalid magic number")
        
        # Parse ID (QString)
        wsjtx_id, _ = self._decode_qstring(data, 12)
        
        return msg_type, wsjtx_id
    
    def update_grid(self, grid_square):
        """
        Update grid square in all WSJT-X instances and contest logger (N1MM+ or N3FJP)
        
        Args:
            grid_square: 4 or 6-character Maidenhead grid square
        """
        # Save for resending after jt9.exe restart
        self.current_grid = grid_square
        
        print(f"Radio Update: Setting grid to {grid_square}")
        print(f"Radio Update: Currently discovered instances: {len(self.wsjtx_ids)}")
        for port, (wsjtx_id, last_seen) in self.wsjtx_ids.items():
            print(f"  - '{wsjtx_id}' on port {port}")
        
        # Update each discovered WSJT-X instance
        if not self.wsjtx_ids:
            print("Radio Update: WARNING - No WSJT-X instances discovered yet!")
            print("              Make sure WSJT-X is running and broadcasting heartbeats")
        else:
            for source_port, (wsjtx_id, last_seen) in self.wsjtx_ids.items():
                try:
                    # Send to the port where WSJT-X is listening (source port of heartbeat)
                    self._send_wsjt_location(wsjtx_id, source_port, grid_square)
                    
                except Exception as e:
                    print(f"Radio Update: Error updating '{wsjtx_id}' on port {source_port}: {e}")
        
        # Update contest logger (N1MM+ or N3FJP) - always, even if no WSJT-X instances
        try:
            self.send_logger_grid(grid_square)
        except Exception as e:
            print(f"Radio Update: Error updating {self.contest_logger.upper()}: {e}")
    
    def _send_wsjt_location(self, wsjtx_id, port, grid_square):
        """
        Send location update to WSJT-X via UDP
        
        Args:
            wsjtx_id: WSJT-X instance identifier
            port: UDP port to send to
            grid_square: Grid square to set (4 or 6 characters)
        """
        # Build LocationChange message
        # Format: Magic + Schema + Type + ID + Location
        
        # Send with plain grid square
        message = struct.pack('>I', self.MAGIC)  # Magic number
        message += struct.pack('>I', self.SCHEMA)  # Schema version
        message += struct.pack('>I', self.MSG_LOCATION)  # Message type: LocationChange
        message += self._encode_qstring(wsjtx_id)  # Application ID
        message += self._encode_qstring(grid_square)  # Grid square (location)
        
        # Send to the port where WSJT-X is listening
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(message, ('127.0.0.1', port))
        sock.close()
        
        print(f"  WSJT-X: Sent LocationChange to '{wsjtx_id}' on port {port} with grid '{grid_square}'")
    
    def _encode_qstring(self, text):
        """
        Encode a string as QString for WSJT-X protocol
        
        QString/utf8 format: 32-bit length (in bytes) + UTF-8 encoded string
        Special case: 0xFFFFFFFF for null string
        """
        if text is None or text == '':
            # Null string
            return struct.pack('>I', 0xFFFFFFFF)
        
        # Encode as UTF-8
        encoded = text.encode('utf-8')
        length = len(encoded)  # Length in BYTES
        
        return struct.pack('>I', length) + encoded
    
    def _decode_qstring(self, data, offset):
        """
        Decode a QString from WSJT-X packet
        
        Returns:
            (string, new_offset)
        """
        if len(data) < offset + 4:
            raise ValueError("Not enough data for QString length")
        
        length = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4
        
        if length == 0xFFFFFFFF:
            # Null string
            return '', offset
        
        if len(data) < offset + length:
            raise ValueError("Not enough data for QString content")
        
        # Decode as UTF-8
        string = data[offset:offset+length].decode('utf-8')
        offset += length
        
        return string, offset
    
    def _send_n1mm_roverqth(self, grid_square):
        """
        Send ROVERQTH update to N1MM+ via UDP
        
        N1MM+ v1.0.11082+ accepts RoverQTH updates on port 13064
        Format: std XML header + <RoverQTH>grid</RoverQTH>
        """
        # N1MM+ experimental RoverQTH support (v1.0.11082+)
        # Port 13064, XML format with RoverQTH tag
        N1MM_ROVERQTH_PORT = 13064
        
        # Per N1MM+ developer: "std xml header and <RoverQTH>FN31</RoverQTH>"
        xml_message = (
            f'<?xml version="1.0" encoding="utf-8"?>'
            f'<RoverQTH>{grid_square}</RoverQTH>'
        )
        
        try:
            # Create socket and send to the new RoverQTH port
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(xml_message.encode('utf-8'), (self.n1mm_host, N1MM_ROVERQTH_PORT))
            sock.close()
            
            print(f"  N1MM+: Sent RoverQTH '{grid_square}' to UDP port {N1MM_ROVERQTH_PORT}")
        except Exception as e:
            print(f"  N1MM+: Error sending RoverQTH: {e}")
    
    def send_n1mm_roverqth_county(self, county_abbrev):
        """
        Send county abbreviation to N1MM+ for State QSO Parties
        
        N1MM+ accepts county abbreviations from QSOParty.sec for QSO parties.
        Format: std XML header + <RoverQTH>COUNTY</RoverQTH>
        
        Args:
            county_abbrev: County abbreviation (e.g., 'CAN' for Canadian County, OK)
        """
        N1MM_ROVERQTH_PORT = 13064
        
        xml_message = (
            f'<?xml version="1.0" encoding="utf-8"?>'
            f'<RoverQTH>{county_abbrev}</RoverQTH>'
        )
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(xml_message.encode('utf-8'), (self.n1mm_host, N1MM_ROVERQTH_PORT))
            sock.close()
            
            print(f"  N1MM+: Sent county '{county_abbrev}' to UDP port {N1MM_ROVERQTH_PORT}")
        except Exception as e:
            print(f"  N1MM+: Error sending county: {e}")
    
    # ==================== N3FJP Methods ====================
    
    def set_logger(self, logger):
        """Change the contest logger ('n1mm' or 'n3fjp')"""
        self.contest_logger = logger
        print(f"RadioUpdater: Contest logger set to {logger}")
    
    def _send_n3fjp_command(self, command):
        """
        Send a command to N3FJP via TCP
        
        N3FJP uses a TCP connection on port 1100 (default)
        Commands are XML-like: <CMD>...</CMD>
        
        Args:
            command: The command string (without outer <CMD> tags)
        
        Returns:
            True on success, False on error
        """
        full_command = f"<CMD>{command}</CMD>"
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((self.n3fjp_host, self.n3fjp_port))
            sock.sendall(full_command.encode('utf-8'))
            
            # Try to read response (may not always get one)
            try:
                response = sock.recv(4096).decode('utf-8')
                if response:
                    print(f"  N3FJP: Response: {response[:100]}")  # First 100 chars
            except socket.timeout:
                pass  # No response is OK for some commands
            
            sock.close()
            return True  # Success - we sent the command
            
        except ConnectionRefusedError:
            print(f"  N3FJP: Connection refused on port {self.n3fjp_port} - is N3FJP running with API enabled?")
            return False
        except Exception as e:
            print(f"  N3FJP: Error sending command: {e}")
            return False
    
    def _send_n3fjp_grid(self, grid_square):
        """
        Send grid square to N3FJP using SETOPINFO command
        
        SETOPINFO requires lat/long to match the grid for it to update.
        We calculate the center point of the grid square.
        """
        # Calculate center lat/long from grid square
        lat, lon = self._grid_to_latlon(grid_square)
        
        if lat is None or lon is None:
            print(f"  N3FJP: Invalid grid square '{grid_square}'")
            return
        
        # SETOPINFO with grid AND matching lat/long
        command = f"<SETOPINFO><GRID>{grid_square}</GRID><LAT>{lat:.1f}</LAT><LONG>{lon:.1f}</LONG></SETOPINFO>"
        success = self._send_n3fjp_command(command)
        
        if success:
            print(f"  N3FJP: Sent grid '{grid_square}' (lat={lat:.1f}, lon={lon:.1f}) via SETOPINFO")
    
    def _grid_to_latlon(self, grid):
        """
        Convert Maidenhead grid square to lat/lon (center of square)
        
        Args:
            grid: 4 or 6 character grid square (e.g., EM15 or EM15fp)
            
        Returns:
            (lat, lon) tuple or (None, None) if invalid
        """
        grid = grid.upper().strip()
        
        if len(grid) < 4:
            return None, None
        
        try:
            # First two characters: field (A-R)
            lon = (ord(grid[0]) - ord('A')) * 20 - 180
            lat = (ord(grid[1]) - ord('A')) * 10 - 90
            
            # Next two characters: square (0-9)
            lon += int(grid[2]) * 2
            lat += int(grid[3]) * 1
            
            # If 6-char grid, add subsquare
            if len(grid) >= 6:
                lon += (ord(grid[4]) - ord('A')) * (2/24)
                lat += (ord(grid[5]) - ord('A')) * (1/24)
                # Center of subsquare
                lon += (2/24) / 2
                lat += (1/24) / 2
            else:
                # Center of 4-char square
                lon += 1  # Half of 2 degrees
                lat += 0.5  # Half of 1 degree
            
            return lat, lon
        except (IndexError, ValueError):
            return None, None
    
    def send_n3fjp_qso(self, callsign, band, mode, freq, rst_sent, rst_rcvd, grid, 
                       date_str=None, time_on=None, time_off=None):
        """
        Log a QSO to N3FJP using the UPDATEANDLOG command
        
        This is designed for WSJT-X style logging
        
        Args:
            callsign: Remote station callsign
            band: Band (e.g., '2' for 2m, '70cm', etc.)
            mode: Mode (FT8, SSB, CW, etc.)
            freq: Frequency in MHz (e.g., 144.174)
            rst_sent: RST/signal report sent
            rst_rcvd: RST/signal report received
            grid: Remote station grid square
            date_str: Date in YYYY/MM/DD format (default: today UTC)
            time_on: Time on in HH:MM format (default: now UTC)
            time_off: Time off in HH:MM format (default: now UTC)
        """
        now = datetime.datetime.utcnow()
        if date_str is None:
            date_str = now.strftime("%Y/%m/%d")
        if time_on is None:
            time_on = now.strftime("%H:%M")
        if time_off is None:
            time_off = now.strftime("%H:%M")
        
        # Build the command
        command = (
            f"<UPDATEANDLOG>"
            f"<CALL>{callsign}</CALL>"
            f"<BAND>{band}</BAND>"
            f"<MODE>{mode}</MODE>"
            f"<FREQ>{freq}</FREQ>"
            f"<RSTS>{rst_sent}</RSTS>"
            f"<RSTR>{rst_rcvd}</RSTR>"
            f"<GRID>{grid}</GRID>"
            f"<DATE>{date_str}</DATE>"
            f"<TIMEON>{time_on}</TIMEON>"
            f"<TIMEOFF>{time_off}</TIMEOFF>"
            f"</UPDATEANDLOG>"
        )
        
        result = self._send_n3fjp_command(command)
        if result is not None:
            print(f"  N3FJP: Logged QSO with {callsign} on {band} {mode}")
        return result
    
    def send_logger_grid(self, grid_square):
        """
        Send grid to the configured contest logger (N1MM+ or N3FJP)
        """
        if self.contest_logger == 'n3fjp':
            self._send_n3fjp_grid(grid_square)
        else:
            # N1MM+ uses RoverQTH
            self._send_n1mm_roverqth(grid_square)
    
    def send_n1mm_contact(self, band, freq, callsign, grid, mode='SSB'):
        """
        Send a contact to N1MM+ for logging
        
        Args:
            band: Band in MHz (e.g., '144')
            freq: Frequency in kHz
            callsign: Remote station callsign
            grid: Remote station grid square
            mode: Mode (SSB, CW, FM, etc.)
        """
        timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        xml_message = (
            f'<?xml version="1.0" encoding="utf-8"?>'
            f'<contactinfo>'
            f'<app>N5ZY-CoPilot</app>'
            f'<contestname>ARRL-VHF</contestname>'
            f'<timestamp>{timestamp}</timestamp>'
            f'<mycall>N5ZY</mycall>'
            f'<band>{band}</band>'
            f'<rxfreq>{freq}</rxfreq>'
            f'<txfreq>{freq}</txfreq>'
            f'<operator>N5ZY</operator>'
            f'<mode>{mode}</mode>'
            f'<call>{callsign}</call>'
            f'<countryprefix>K</countryprefix>'
            f'<wpxprefix></wpxprefix>'
            f'<stationprefix></stationprefix>'
            f'<continent></continent>'
            f'<snt>59</snt>'
            f'<rcv>59</rcv>'
            f'<gridsquare>{grid}</gridsquare>'
            f'<exchange1></exchange1>'
            f'<section></section>'
            f'<comment></comment>'
            f'<qth></qth>'
            f'<n></n>'
            f'<power></power>'
            f'<misctext></misctext>'
            f'<zone></zone>'
            f'<prec></prec>'
            f'<ck></ck>'
            f'<ismultiplier1>0</ismultiplier1>'
            f'<ismultiplier2>0</ismultiplier2>'
            f'<ismultiplier3>0</ismultiplier3>'
            f'<points>0</points>'
            f'<radionr>1</radionr>'
            f'<RUN1RUN2></RUN1RUN2>'
            f'<ContactType></ContactType>'
            f'<StationName></StationName>'
            f'<ID></ID>'
            f'<IsOriginal>True</IsOriginal>'
            f'<NetBiosName></NetBiosName>'
            f'<IsRunQSO>0</IsRunQSO>'
            f'<StationName></StationName>'
            f'<RadioInterfaced>0</RadioInterfaced>'
            f'<NetworkedCompNr>0</NetworkedCompNr>'
            f'<IsMultiplier1>0</IsMultiplier1>'
            f'<IsMultiplier2>0</IsMultiplier2>'
            f'<IsMultiplier3>0</IsMultiplier3>'
            f'<ReservedForFutureUse></ReservedForFutureUse>'
            f'</contactinfo>'
        )
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(xml_message.encode('utf-8'), (self.n1mm_host, self.n1mm_port))
        sock.close()
        
        print(f"  N1MM+: Logged contact with {callsign} on {band}MHz")
    
    def _parse_qso_logged(self, data):
        """
        Parse QSO Logged message (type 5) from WSJT-X
        
        Returns dict with QSO data or None if parsing fails
        """
        try:
            offset = 12  # Skip Magic (4) + Schema (4) + Type (4)
            
            # Parse ID (QString)
            wsjtx_id, offset = self._decode_qstring(data, offset)
            
            # Parse Date/Time Off (QDateTime)
            datetime_off, offset = self._decode_qdatetime(data, offset)
            
            # Parse DX call (QString)
            dx_call, offset = self._decode_qstring(data, offset)
            
            # Parse DX grid (QString)
            dx_grid, offset = self._decode_qstring(data, offset)
            
            # Parse Tx frequency (quint64) - 8 bytes
            tx_freq = struct.unpack('>Q', data[offset:offset+8])[0]
            offset += 8
            
            # Parse Mode (QString)
            mode, offset = self._decode_qstring(data, offset)
            
            # Parse Report sent (QString)
            report_sent, offset = self._decode_qstring(data, offset)
            
            # Parse Report received (QString)
            report_rcvd, offset = self._decode_qstring(data, offset)
            
            # Parse Tx power (QString)
            tx_power, offset = self._decode_qstring(data, offset)
            
            # Parse Comments (QString)
            comments, offset = self._decode_qstring(data, offset)
            
            # Parse Name (QString)
            name, offset = self._decode_qstring(data, offset)
            
            # Parse Date/Time On (QDateTime)
            datetime_on, offset = self._decode_qdatetime(data, offset)
            
            # Parse Operator call (QString)
            operator_call, offset = self._decode_qstring(data, offset)
            
            # Parse My call (QString)
            my_call, offset = self._decode_qstring(data, offset)
            
            # Parse My grid (QString)
            my_grid, offset = self._decode_qstring(data, offset)
            
            # Parse Exchange sent (QString)
            exchange_sent, offset = self._decode_qstring(data, offset)
            
            # Parse Exchange received (QString)
            exchange_rcvd, offset = self._decode_qstring(data, offset)
            
            # Parse ADIF propagation mode (QString) - may not be present in older versions
            adif_prop_mode = ''
            try:
                adif_prop_mode, offset = self._decode_qstring(data, offset)
            except:
                pass
            
            # Convert frequency to MHz and determine band
            freq_mhz = tx_freq / 1_000_000
            band = self._freq_to_band(freq_mhz)
            
            return {
                'wsjtx_id': wsjtx_id,
                'datetime_off': datetime_off,
                'datetime_on': datetime_on,
                'dx_call': dx_call,
                'dx_grid': dx_grid,
                'freq_hz': tx_freq,
                'freq_mhz': freq_mhz,
                'band': band,
                'mode': mode,
                'report_sent': report_sent,
                'report_rcvd': report_rcvd,
                'tx_power': tx_power,
                'comments': comments,
                'name': name,
                'operator_call': operator_call,
                'my_call': my_call,
                'my_grid': my_grid,
                'exchange_sent': exchange_sent,
                'exchange_rcvd': exchange_rcvd,
                'adif_prop_mode': adif_prop_mode
            }
            
        except Exception as e:
            print(f"Radio Update: Error parsing QSO Logged message: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _decode_qdatetime(self, data, offset):
        """
        Decode QDateTime from WSJT-X packet
        
        QDateTime format:
        - quint64: Julian day number
        - quint32: Milliseconds since midnight
        - quint8: Time spec (0=local, 1=UTC)
        
        Returns:
            (datetime, new_offset)
        """
        if len(data) < offset + 13:
            raise ValueError("Not enough data for QDateTime")
        
        # Parse components
        julian_day = struct.unpack('>Q', data[offset:offset+8])[0]
        offset += 8
        
        msecs = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4
        
        time_spec = struct.unpack('>B', data[offset:offset+1])[0]
        offset += 1
        
        # Convert Julian day to datetime
        # Julian day 2440588 = 1970-01-01
        # Julian day 2451545 = 2000-01-01
        days_since_epoch = julian_day - 2440588
        
        # Calculate datetime
        dt = datetime.datetime(1970, 1, 1) + datetime.timedelta(
            days=days_since_epoch,
            milliseconds=msecs
        )
        
        return dt, offset
    
    def _freq_to_band(self, freq_mhz):
        """Convert frequency in MHz to band string"""
        # HF bands
        if 1.8 <= freq_mhz <= 2.0:
            return '160m'
        elif 3.5 <= freq_mhz <= 4.0:
            return '80m'
        elif 5.3 <= freq_mhz <= 5.4:
            return '60m'
        elif 7.0 <= freq_mhz <= 7.3:
            return '40m'
        elif 10.1 <= freq_mhz <= 10.15:
            return '30m'
        elif 14.0 <= freq_mhz <= 14.35:
            return '20m'
        elif 18.068 <= freq_mhz <= 18.168:
            return '17m'
        elif 21.0 <= freq_mhz <= 21.45:
            return '15m'
        elif 24.89 <= freq_mhz <= 24.99:
            return '12m'
        elif 28.0 <= freq_mhz <= 29.7:
            return '10m'
        # VHF/UHF/Microwave bands
        elif 50 <= freq_mhz <= 54:
            return '6m'
        elif 144 <= freq_mhz <= 148:
            return '2m'
        elif 222 <= freq_mhz <= 225:
            return '1.25m'
        elif 420 <= freq_mhz <= 450:
            return '70cm'
        elif 902 <= freq_mhz <= 928:
            return '33cm'
        elif 1240 <= freq_mhz <= 1300:
            return '23cm'
        elif 2300 <= freq_mhz <= 2450:
            return '13cm'
        elif 3300 <= freq_mhz <= 3500:
            return '9cm'
        elif 5650 <= freq_mhz <= 5925:
            return '6cm'
        elif 10000 <= freq_mhz <= 10500:
            return '3cm'
        elif 24000 <= freq_mhz <= 24250:
            return '1.25cm'
        else:
            return f'{freq_mhz:.3f}MHz'
    
    def _handle_qso_logged(self, qso_data, wsjtx_id):
        """
        Handle a QSO Logged message from WSJT-X
        
        - Checks for duplicates
        - Writes to ADIF file
        - Notifies callback
        - Queues for N1MM+ relay (sent one at a time with delays)
        """
        # Create unique key for duplicate detection
        qso_key = (
            qso_data['datetime_off'].strftime('%Y%m%d%H%M%S') if qso_data['datetime_off'] else '',
            qso_data['dx_call'],
            qso_data['band']
        )
        
        if qso_key in self.logged_qsos:
            print(f"Radio Update: Duplicate QSO ignored: {qso_data['dx_call']} on {qso_data['band']}")
            return
        
        self.logged_qsos.add(qso_key)
        
        # Log to console
        print(f"\n{'='*60}")
        print(f"QSO LOGGED from {wsjtx_id}:")
        print(f"  Call: {qso_data['dx_call']}")
        print(f"  Grid: {qso_data['dx_grid']}")
        print(f"  Band: {qso_data['band']} ({qso_data['freq_mhz']:.6f} MHz)")
        print(f"  Mode: {qso_data['mode']}")
        print(f"  RST:  {qso_data['report_sent']} / {qso_data['report_rcvd']}")
        print(f"  Time: {qso_data['datetime_off']}")
        print(f"{'='*60}\n")
        
        # Stamp GPS location data for LoTW (if stamper provided)
        if self.location_stamper:
            try:
                qso_data = self.location_stamper(qso_data)
            except Exception as e:
                print(f"Radio Update: Error stamping location: {e}")
        
        # Write to ADIF file (immediate - this is the backup)
        self._write_qso_to_adif(qso_data)
        
        # Queue for N1MM+ relay (will be sent with delays to prevent race conditions)
        self.qso_queue.put(qso_data)
        queue_size = self.qso_queue.qsize()
        print(f"Radio Update: QSO queued for N1MM+ relay (queue size: {queue_size})")
        
        # Notify callback (for UI updates)
        if self.qso_callback:
            try:
                self.qso_callback(qso_data)
            except Exception as e:
                print(f"Radio Update: Error in QSO callback: {e}")
    
    def _write_qso_to_adif(self, qso_data):
        """Write QSO to ADIF file for backup/import"""
        try:
            import os
            
            # Get log directory
            log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            
            # Generate filename with date
            today = datetime.datetime.now().strftime('%Y%m%d')
            adif_path = os.path.join(log_dir, f'n5zy_copilot_{today}.adi')
            
            # Check if file exists (need header?)
            write_header = not os.path.exists(adif_path)
            
            with open(adif_path, 'a') as f:
                if write_header:
                    # Write ADIF 3.1.4 header
                    f.write(f"#++++++++++++++++++++++++++++++++++++\n")
                    f.write(f"#   N5ZY Co-Pilot GPS-stamped log\n")
                    f.write(f"#   Created: {datetime.datetime.now().strftime('%A, %B %d, %Y')}\n")
                    f.write(f"#   For LoTW upload via Log4OM\n")
                    f.write(f"#++++++++++++++++++++++++++++++++++++\n\n")
                    f.write(f"<adif_ver:5>3.1.4\n")
                    f.write(f"<programid:12>N5ZY-CoPilot\n")
                    f.write(f"<programversion:6>1.8.32\n")
                    f.write(f"<eoh>\n\n")
                
                # Build ADIF record
                record = self._build_adif_record(qso_data)
                f.write(record + "\n")
            
            print(f"Radio Update: QSO written to {adif_path}")
            
        except Exception as e:
            print(f"Radio Update: Error writing ADIF: {e}")
    
    def queue_qso_for_relay(self, qso_data):
        """
        Public method to queue a QSO for relay to N1MM+
        Used by manual QSO entry - ADIF is written separately.
        """
        self.qso_queue.put(qso_data)
        queue_size = self.qso_queue.qsize()
        print(f"Radio Update: Manual QSO queued for N1MM+ relay (queue size: {queue_size})")
    
    def _build_adif_record(self, qso_data):
        """
        Build ADIF record string from QSO data.
        
        Matches the C# AdifWriter format for Log4OM import and LoTW upload.
        
        Includes:
        - GPS-derived MY_* fields (MY_STATE, MY_CNTY, MY_LAT, MY_LON, etc.)
        - /R Rover DXCC fix (prevents Log4OM misidentifying rovers as European Russia)
        - Contest ID mapping
        - Full station callsign fields
        """
        fields = []
        
        # === QSO Core ===
        call = qso_data['dx_call']
        fields.append(f"<call:{len(call)}>{call}")
        
        # Date/Time
        if qso_data.get('datetime_on'):
            qso_date = qso_data['datetime_on'].strftime('%Y%m%d')
            qso_time = qso_data['datetime_on'].strftime('%H%M%S')
            fields.append(f"<qso_date:8>{qso_date}")
            fields.append(f"<time_on:6>{qso_time}")
        
        if qso_data.get('datetime_off'):
            qso_date_off = qso_data['datetime_off'].strftime('%Y%m%d')
            qso_time_off = qso_data['datetime_off'].strftime('%H%M%S')
            fields.append(f"<qso_date_off:8>{qso_date_off}")
            fields.append(f"<time_off:6>{qso_time_off}")
        
        # Band and frequency
        band = qso_data.get('band', '')
        if band:
            fields.append(f"<band:{len(band)}>{band}")
        
        mode = qso_data.get('mode', '')
        if mode:
            fields.append(f"<mode:{len(mode)}>{mode}")
        
        freq = f"{qso_data.get('freq_mhz', 0):.6f}"
        fields.append(f"<freq:{len(freq)}>{freq}")
        fields.append(f"<freq_rx:{len(freq)}>{freq}")  # Same as TX for simplex
        
        # RST
        rst_sent = qso_data.get('report_sent', '')
        rst_rcvd = qso_data.get('report_rcvd', '')
        if rst_sent:
            fields.append(f"<rst_sent:{len(rst_sent)}>{rst_sent}")
        if rst_rcvd:
            fields.append(f"<rst_rcvd:{len(rst_rcvd)}>{rst_rcvd}")
        
        # Power
        power = qso_data.get('tx_power', '')
        if power:
            fields.append(f"<tx_pwr:{len(power)}>{power}")
        
        # === Contest Fields ===
        contest_id = qso_data.get('contest_id', '')
        if contest_id:
            mapped_id = self.map_contest_id(contest_id)
            fields.append(f"<contest_id:{len(mapped_id)}>{mapped_id}")
        
        # Serial numbers
        stx = qso_data.get('stx', '')
        srx = qso_data.get('srx', '')
        if stx:
            fields.append(f"<stx:{len(stx)}>{stx}")
        if srx:
            fields.append(f"<srx:{len(srx)}>{srx}")
        
        # Exchange strings
        exch_sent = qso_data.get('exchange_sent', '') or qso_data.get('stx_string', '')
        exch_rcvd = qso_data.get('exchange_rcvd', '') or qso_data.get('srx_string', '')
        if exch_sent:
            fields.append(f"<stx_string:{len(exch_sent)}>{exch_sent}")
        if exch_rcvd:
            fields.append(f"<srx_string:{len(exch_rcvd)}>{exch_rcvd}")
        
        # === Their Info ===
        grid = qso_data.get('dx_grid', '')
        if grid:
            fields.append(f"<gridsquare:{len(grid)}>{grid}")
        
        # /R Rover DXCC Fix: If contacted station is a rover (/R), add explicit
        # DXCC fields to prevent Log4OM from misinterpreting /R as European Russia
        if self.is_rover_call(call):
            # For US rovers, set explicit US DXCC info
            fields.append("<dxcc:3>291")              # United States
            fields.append("<cqz:1>4")                 # CQ Zone 4 (central US default)
            fields.append("<ituz:1>7")                # ITU Zone 7 (central US default)
            fields.append("<country:13>United States")
            # Derive prefix from callsign (e.g., N5 from N5ZY/R)
            prefix = self._derive_prefix(call)
            if prefix:
                fields.append(f"<pfx:{len(prefix)}>{prefix}")
        
        # === My Info (GPS-derived for LoTW) ===
        my_call = qso_data.get('my_call', '')
        if my_call:
            fields.append(f"<station_callsign:{len(my_call)}>{my_call}")
            fields.append(f"<operator:{len(my_call)}>{my_call}")
            # Owner callsign (without /R suffix)
            owner = my_call.split('/')[0]
            fields.append(f"<owner_callsign:{len(owner)}>{owner}")
        
        # My grid (6-char from GPS)
        my_grid = qso_data.get('my_grid', '')
        if my_grid:
            fields.append(f"<my_gridsquare:{len(my_grid)}>{my_grid}")
        
        # My state (from county lookup)
        my_state = qso_data.get('my_state', '')
        if my_state:
            fields.append(f"<my_state:{len(my_state)}>{my_state}")
        
        # My county (from county lookup - just county name for Log4OM)
        my_county = qso_data.get('my_county', '')
        if my_county:
            fields.append(f"<my_cnty:{len(my_county)}>{my_county}")
        
        # My lat/lon (ADIF format)
        my_lat = qso_data.get('my_lat', '')
        my_lon = qso_data.get('my_lon', '')
        if my_lat:
            fields.append(f"<my_lat:{len(my_lat)}>{my_lat}")
        if my_lon:
            fields.append(f"<my_lon:{len(my_lon)}>{my_lon}")
        
        # My country/zone info (US defaults)
        my_country = qso_data.get('my_country', 'United States')
        fields.append(f"<my_country:{len(my_country)}>{my_country}")
        
        my_cq_zone = qso_data.get('my_cq_zone', '4')
        fields.append(f"<my_cq_zone:{len(my_cq_zone)}>{my_cq_zone}")
        
        my_itu_zone = qso_data.get('my_itu_zone', '7')
        fields.append(f"<my_itu_zone:{len(my_itu_zone)}>{my_itu_zone}")
        
        my_dxcc = qso_data.get('my_dxcc', '291')
        fields.append(f"<my_dxcc:{len(my_dxcc)}>{my_dxcc}")
        
        # Rover QTH (4-char grid for VHF contests)
        rover_qth = qso_data.get('rover_qth', '')
        if not rover_qth and my_grid:
            rover_qth = my_grid[:4]  # Use first 4 chars of 6-char grid
        if rover_qth:
            fields.append(f"<app_n5zy_roverqth:{len(rover_qth)}>{rover_qth}")
        
        # === QSL Status (defaults for new QSOs) ===
        fields.append("<qsl_sent:1>N")
        fields.append("<qsl_rcvd:1>N")
        fields.append("<lotw_qsl_sent:1>N")
        fields.append("<lotw_qsl_rcvd:1>N")
        fields.append("<qso_complete:1>Y")
        
        # === Program ID ===
        fields.append("<programid:12>N5ZY-CoPilot")
        
        # Comment (include source - WSJT-X instance name or Manual)
        wsjtx_id = qso_data.get('wsjtx_id', 'Manual Entry')
        comment = f"Via {wsjtx_id}"
        comments = qso_data.get('comments', '')
        if comments:
            comment += f" - {comments}"
        fields.append(f"<comment:{len(comment)}>{comment}")
        
        # End of record
        fields.append("<eor>")
        
        return ' '.join(fields)
    
    @staticmethod
    def _derive_prefix(callsign):
        """
        Derive the callsign prefix (e.g., N5 from N5ZY, KF0 from KF0QQQ)
        Used for the PFX field in ADIF.
        """
        if not callsign:
            return ""
        
        # Remove /R, /P, /M suffixes
        base_call = callsign.split('/')[0].upper()
        
        # Find where the prefix ends (letters then number)
        prefix = ""
        found_digit = False
        
        for ch in base_call:
            prefix += ch
            if ch.isdigit():
                found_digit = True
                break
        
        return prefix if found_digit else base_call
    
    def _send_qso_to_n1mm(self, qso_data):
        """
        Send a single QSO to N1MM+ via TCP (JTDX protocol)
        
        Each QSO gets its own TCP connection - connect, send, close.
        The relay thread handles the 500ms delay between connections.
        """
        try:
            # Build ADIF record for this ONE QSO
            adif_record = self._build_adif_for_n1mm(qso_data)
            
            print(f"Radio Update: Sending to N1MM+ TCP:{self.n1mm_port}:")
            print(f"              {adif_record.strip()}")
            
            # Connect via TCP
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            
            try:
                sock.connect((self.n1mm_host, self.n1mm_port))
                sock.sendall(adif_record.encode('utf-8'))
                
                # Brief pause before closing
                time.sleep(0.1)
                
                print(f"Radio Update: ✅ Sent QSO to N1MM+ ({qso_data['dx_call']} on {qso_data['band']})")
                return True
                
            except ConnectionRefusedError:
                print(f"Radio Update: ❌ N1MM+ not listening on TCP port {self.n1mm_port}")
                return False
            except socket.timeout:
                print(f"Radio Update: ❌ Timeout connecting to N1MM+")
                return False
            finally:
                sock.close()
            
        except Exception as e:
            print(f"Radio Update: ❌ Error sending to N1MM+: {e}")
            return False
    
    def _send_qso_to_n3fjp(self, qso_data):
        """
        Send a single QSO to N3FJP via TCP using UPDATEANDLOG command
        
        This is the WSJT-X style logging command that N3FJP supports.
        """
        try:
            # Extract QSO data
            call = qso_data['dx_call']
            grid = qso_data.get('dx_grid', '') or ''
            mode = qso_data['mode']
            freq_mhz = qso_data['freq_mhz']
            rst_sent = qso_data.get('report_sent', '-10') or '-10'
            rst_rcvd = qso_data.get('report_rcvd', '-10') or '-10'
            
            # Convert band to N3FJP format (just the number, e.g., "2" for 2m)
            band = qso_data['band']
            # Remove 'MHz' or 'M' suffix if present
            band_num = band.replace('MHz', '').replace('M', '').replace('m', '').strip()
            
            # Format date and time
            if qso_data.get('datetime_on'):
                date_str = qso_data['datetime_on'].strftime('%Y/%m/%d')
                time_on = qso_data['datetime_on'].strftime('%H:%M')
            else:
                import datetime
                now = datetime.datetime.utcnow()
                date_str = now.strftime('%Y/%m/%d')
                time_on = now.strftime('%H:%M')
            
            if qso_data.get('datetime_off'):
                time_off = qso_data['datetime_off'].strftime('%H:%M')
            else:
                time_off = time_on
            
            # Build UPDATEANDLOG command
            command = (
                f"<UPDATEANDLOG>"
                f"<CALL>{call}</CALL>"
                f"<BAND>{band_num}</BAND>"
                f"<MODE>{mode}</MODE>"
                f"<FREQ>{freq_mhz:.6f}</FREQ>"
                f"<RSTS>{rst_sent}</RSTS>"
                f"<RSTR>{rst_rcvd}</RSTR>"
                f"<GRID>{grid}</GRID>"
                f"<DATE>{date_str}</DATE>"
                f"<TIMEON>{time_on}</TIMEON>"
                f"<TIMEOFF>{time_off}</TIMEOFF>"
                f"</UPDATEANDLOG>"
            )
            
            print(f"Radio Update: Sending to N3FJP TCP:{self.n3fjp_port}:")
            print(f"              <CMD>{command}</CMD>")
            
            success = self._send_n3fjp_command(command)
            
            if success:
                print(f"Radio Update: ✅ Sent QSO to N3FJP ({call} on {band})")
                return True
            else:
                print(f"Radio Update: ❌ Failed to send QSO to N3FJP")
                return False
                
        except Exception as e:
            print(f"Radio Update: ❌ Error sending to N3FJP: {e}")
            return False
    
    def _build_adif_for_n1mm(self, qso_data):
        """
        Build a single ADIF record for one QSO
        
        Uses time offset to ensure QSOs with same callsign on different bands
        have unique timestamps so N1MM+ treats them as separate entries.
        """
        fields = []
        
        # Essential fields
        call = qso_data['dx_call']
        fields.append(f"<call:{len(call)}>{call}")
        
        grid = qso_data['dx_grid'] or ''
        if grid:
            fields.append(f"<gridsquare:{len(grid)}>{grid}")
        
        mode = qso_data['mode']
        fields.append(f"<mode:{len(mode)}>{mode}")
        
        rst_sent = qso_data['report_sent'] or '-10'
        rst_rcvd = qso_data['report_rcvd'] or '-10'
        fields.append(f"<rst_sent:{len(rst_sent)}>{rst_sent}")
        fields.append(f"<rst_rcvd:{len(rst_rcvd)}>{rst_rcvd}")
        
        freq = f"{qso_data['freq_mhz']:.6f}"
        fields.append(f"<freq:{len(freq)}>{freq}")
        
        band = self._band_to_n1mm_format(qso_data['band'])
        fields.append(f"<band:{len(band)}>{band}")
        
        # Date/Time - add offset to make each QSO unique
        if qso_data['datetime_off']:
            base_time = qso_data['datetime_off']
            # Add offset seconds to differentiate same-callsign QSOs
            offset = qso_data.get('_time_offset', 0)
            adjusted_time = base_time + datetime.timedelta(seconds=offset)
            
            qso_date = adjusted_time.strftime('%Y%m%d')
            qso_time = adjusted_time.strftime('%H%M%S')
            fields.append(f"<qso_date:8>{qso_date}")
            fields.append(f"<time_on:6>{qso_time}")
            fields.append(f"<time_off:6>{qso_time}")
        
        fields.append("<eor>")
        
        return ' '.join(fields) + '\r\n'
    
    def _band_to_n1mm_format(self, band):
        """Convert band to N1MM+ expected format"""
        # N1MM+ expects bands like "6M", "2M", "70CM", etc.
        band_map = {
            # HF
            '160m': '160M',
            '80m': '80M',
            '60m': '60M',
            '40m': '40M',
            '30m': '30M',
            '20m': '20M',
            '17m': '17M',
            '15m': '15M',
            '12m': '12M',
            '10m': '10M',
            # VHF/UHF/Microwave
            '6m': '6M',
            '2m': '2M', 
            '1.25m': '1.25M',
            '70cm': '70CM',
            '33cm': '33CM',
            '23cm': '23CM',
            '13cm': '13CM',
            '9cm': '9CM',
            '6cm': '6CM',
            '3cm': '3CM',
            '1.25cm': '1.25CM',
        }
        return band_map.get(band, band.upper())

    
    def _parse_adif_logged(self, data):
        """
        Parse ADIF Logged message (type 12) from WSJT-X
        
        This message contains the raw ADIF record string
        """
        try:
            offset = 12  # Skip Magic (4) + Schema (4) + Type (4)
            
            # Parse ID (QString)
            wsjtx_id, offset = self._decode_qstring(data, offset)
            
            # Parse ADIF record (QString)
            adif_record, offset = self._decode_qstring(data, offset)
            
            return {
                'wsjtx_id': wsjtx_id,
                'adif_record': adif_record
            }
            
        except Exception as e:
            print(f"Radio Update: Error parsing ADIF Logged message: {e}")
            return None
    
    def _handle_adif_logged(self, adif_data, wsjtx_id):
        """
        Handle ADIF Logged message from WSJT-X
        
        This is an alternative to QSO Logged - contains raw ADIF string
        """
        print(f"Radio Update: Received ADIF from {wsjtx_id}:")
        print(f"  {adif_data['adif_record'][:100]}...")
        
        # Append to daily ADIF file
        try:
            import os
            log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            
            today = datetime.datetime.now().strftime('%Y%m%d')
            adif_path = os.path.join(log_dir, f'n5zy_copilot_{today}.adi')
            
            with open(adif_path, 'a') as f:
                f.write(adif_data['adif_record'] + "\n")
            
            print(f"Radio Update: ADIF appended to {adif_path}")
            
        except Exception as e:
            print(f"Radio Update: Error writing ADIF: {e}")

