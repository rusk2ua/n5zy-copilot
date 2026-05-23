"""
RBN (Reverse Beacon Network) Telnet Client

Connects to the RBN telnet server, parses spot lines, and delivers
parsed spot dicts to the application via a callback.

Spot line format (typical):
DX de KM3T-#:    14040.1  W1AW         CW    18 dB  25 WPM  CQ      1042Z

Parsed into:
{
    'spotter': 'KM3T',
    'freq_khz': 14040.1,
    'spotted_call': 'W1AW',
    'mode': 'CW',
    'snr_db': 18,
    'speed_wpm': 25,
    'info': 'CQ',
    'time_utc': '1042Z',
    'band': '20m',
}
"""

import socket
import threading
import time
import re
from datetime import datetime


# Frequency (kHz) to band mapping
FREQ_TO_BAND = [
    (1800, 2000, '160m'),
    (3500, 4000, '80m'),
    (7000, 7300, '40m'),
    (10100, 10150, '30m'),
    (14000, 14350, '20m'),
    (18068, 18168, '17m'),
    (21000, 21450, '15m'),
    (24890, 24990, '12m'),
    (28000, 29700, '10m'),
    (50000, 54000, '6m'),
    (144000, 148000, '2m'),
]


def freq_to_band(freq_khz):
    """Convert frequency in kHz to band string."""
    for low, high, band in FREQ_TO_BAND:
        if low <= freq_khz <= high:
            return band
    return ''


# Regex to parse RBN spot lines
# Example: "DX de KM3T-#:    14040.1  W1AW         CW    18 dB  25 WPM  CQ      1042Z"
SPOT_RE = re.compile(
    r'DX de\s+([A-Z0-9/]+-?#?):\s+'      # spotter (with optional -# suffix)
    r'(\d+\.?\d*)\s+'                      # frequency in kHz
    r'([A-Z0-9/]+)\s+'                     # spotted callsign
    r'(CW|RTTY|FT8|FT4|PSK31|PSK63)\s+'   # mode
    r'(\d+)\s*dB\s+'                       # SNR
    r'(\d+)\s*WPM\s+'                      # speed
    r'(.*?)\s+'                            # info (CQ, NCDXF, etc.)
    r'(\d{4}Z)',                           # UTC time
    re.IGNORECASE
)


class RBNClient:
    """Telnet client for the Reverse Beacon Network."""

    def __init__(self, server='telnet.reversebeacon.net', port=7000,
                 my_call='N0CALL', spot_callback=None):
        """
        Args:
            server: RBN telnet server hostname
            port: RBN telnet server port
            my_call: Your callsign (sent as login)
            spot_callback: function(spot_dict) called for each parsed spot
        """
        self.server = server
        self.port = port
        self.my_call = my_call.upper().strip() or 'N0CALL'
        self.spot_callback = spot_callback

        self.running = False
        self.connected = False
        self._thread = None
        self._socket = None
        self._reconnect_delay = 5  # seconds between reconnection attempts
        self._stats = {
            'spots_received': 0,
            'connect_time': None,
            'last_spot_time': None,
        }

    def start(self):
        """Start the RBN client in a background thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                         name='RBN-Client')
        self._thread.start()
        print(f"RBN: Starting connection to {self.server}:{self.port} as {self.my_call}")

    def stop(self):
        """Stop the RBN client and close the connection."""
        self.running = False
        self.connected = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        print("RBN: Stopped")

    def set_server(self, server, port):
        """Update server/port (will reconnect on next cycle)."""
        if server != self.server or port != self.port:
            self.server = server
            self.port = port
            # Force reconnect
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None
            self.connected = False

    def get_stats(self):
        """Return connection statistics."""
        return dict(self._stats)

    def _run_loop(self):
        """Main loop: connect, read lines, parse, deliver."""
        while self.running:
            try:
                self._connect()
                if not self.connected:
                    time.sleep(self._reconnect_delay)
                    continue

                self._read_spots()

            except Exception as e:
                print(f"RBN: Connection error: {e}")
                self.connected = False
                if self._socket:
                    try:
                        self._socket.close()
                    except Exception:
                        pass
                    self._socket = None

                if self.running:
                    time.sleep(self._reconnect_delay)

    def _connect(self):
        """Establish telnet connection to RBN server."""
        if self.connected:
            return

        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(30)
            self._socket.connect((self.server, self.port))

            # Read the login prompt (usually "Please enter your call:")
            # and send our callsign
            time.sleep(1)
            # Drain any initial data
            self._socket.settimeout(5)
            try:
                initial = self._socket.recv(4096).decode('ascii', errors='ignore')
                print(f"RBN: Server greeting: {initial.strip()[:100]}")
            except socket.timeout:
                pass

            # Send callsign as login
            login = f"{self.my_call}\r\n"
            self._socket.sendall(login.encode('ascii'))
            print(f"RBN: Sent login: {self.my_call}")

            # Wait briefly for response
            time.sleep(1)
            try:
                response = self._socket.recv(4096).decode('ascii', errors='ignore')
                if response:
                    print(f"RBN: Login response: {response.strip()[:100]}")
            except socket.timeout:
                pass

            self.connected = True
            self._stats['connect_time'] = datetime.utcnow()
            self._socket.settimeout(60)  # Longer timeout for spot reading
            print(f"RBN: Connected to {self.server}:{self.port}")

        except Exception as e:
            print(f"RBN: Failed to connect to {self.server}:{self.port}: {e}")
            self.connected = False
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None

    def _read_spots(self):
        """Read lines from the socket and parse spots."""
        buffer = ''

        while self.running and self.connected:
            try:
                data = self._socket.recv(4096)
                if not data:
                    # Connection closed by server
                    print("RBN: Connection closed by server")
                    self.connected = False
                    return

                buffer += data.decode('ascii', errors='ignore')

                # Process complete lines
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if line:
                        self._process_line(line)

            except socket.timeout:
                # No data received within timeout — send keepalive
                continue
            except OSError:
                # Socket closed
                self.connected = False
                return

    def _process_line(self, line):
        """Parse a single line and deliver spot if valid."""
        match = SPOT_RE.search(line)
        if not match:
            return

        try:
            spotter_raw = match.group(1)
            freq_khz = float(match.group(2))
            spotted_call = match.group(3).upper()
            mode = match.group(4).upper()
            snr_db = int(match.group(5))
            speed_wpm = int(match.group(6))
            info = match.group(7).strip()
            time_utc = match.group(8)

            # Clean up spotter (remove -# suffix from skimmer nodes)
            spotter = re.sub(r'-#$', '', spotter_raw).upper()

            # Determine band from frequency
            band = freq_to_band(freq_khz)

            spot = {
                'spotter': spotter,
                'freq_khz': freq_khz,
                'spotted_call': spotted_call,
                'mode': mode,
                'snr_db': snr_db,
                'speed_wpm': speed_wpm,
                'info': info,
                'time_utc': time_utc,
                'band': band,
                'timestamp': datetime.utcnow(),
            }

            self._stats['spots_received'] += 1
            self._stats['last_spot_time'] = spot['timestamp']

            # Deliver to callback
            if self.spot_callback:
                self.spot_callback(spot)

        except (ValueError, IndexError) as e:
            # Malformed line — skip silently
            pass
