"""
Log Monitor Module
Monitors WSJT-X log files for new decodes and worked grids
"""

import os
import threading
import time
import re
from pathlib import Path

class LogMonitor:
    def __init__(self, wsjt_instances, callback, contest_mode='vhf', priority_callback=None):
        """
        Initialize log monitor

        Args:
            wsjt_instances: List of WSJT-X instance configs with log paths
            callback: Function to call with (band, callsign, grid, is_new_grid, is_calling_me)
            contest_mode: Contest mode ('vhf', 'daily_dx', etc.)
            priority_callback: Function to call with (band, callsign, freq_mhz) for priority stations
        """
        self.wsjt_instances = wsjt_instances
        self.callback = callback
        self.contest_mode = contest_mode
        self.priority_callback = priority_callback
        self.priority_stations = set()  # Set externally
        self.decode_check_callback = None  # Called for every decode (DX2/DX3 dynamic checks)
        self.running = False
        self.thread = None

        # Track worked grids per band
        self.worked_grids = {}  # {band: set(grids)}

        # Track last file positions
        self.file_positions = {}  # {filepath: position}
    
    def start(self):
        """Start log monitoring thread"""
        # Load existing logs first
        self.reload_logs()
        
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        print("LogMonitor: Started monitoring thread")
        
        # Debug: show what we're monitoring
        for instance in self.wsjt_instances:
            log_path = instance.get('log_path', '')
            band_name = instance.get('name', 'Unknown')
            if log_path:
                all_txt = Path(log_path) / 'ALL.TXT'
                if all_txt.exists():
                    print(f"LogMonitor: Will monitor {band_name}: {all_txt}")
                else:
                    print(f"LogMonitor: {band_name}: ALL.TXT not found at {all_txt}")
            else:
                print(f"LogMonitor: {band_name}: No log path configured")
    
    def stop(self):
        """Stop log monitoring"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
    
    def reload_logs(self):
        """Reload all WSJT-X logs to populate worked grids"""
        print("LogMonitor: Reloading contest logs...")
        
        for instance in self.wsjt_instances:
            log_path = instance.get('log_path', '')
            band_name = instance.get('name', 'Unknown')
            
            if not log_path or not os.path.exists(log_path):
                continue
            
            # Find the latest wsjtx_log.adi file
            adi_files = list(Path(log_path).glob('wsjtx_log.adi'))
            if not adi_files:
                continue
            
            latest_log = max(adi_files, key=os.path.getmtime)
            
            # Parse ADI log file
            worked = self._parse_adi_file(latest_log)
            self.worked_grids[band_name] = worked
            
            print(f"  {band_name}: Loaded {len(worked)} worked grids")
    
    def _parse_adi_file(self, filepath):
        """Parse ADIF log file and extract worked grids"""
        worked = set()
        
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
                # Find all GRIDSQUARE fields in ADIF format
                # Format: <GRIDSQUARE:4>FN31 or <GRIDSQUARE:6>FN31pr
                grid_pattern = r'<GRIDSQUARE:(\d+)>([A-R]{2}\d{2}[a-x]{2})'
                matches = re.findall(grid_pattern, content, re.IGNORECASE)
                
                for length, grid in matches:
                    # Normalize to 4-character grid
                    grid_4char = grid[:4].upper()
                    worked.add(grid_4char)
        
        except Exception as e:
            print(f"LogMonitor: Error parsing {filepath}: {e}")
        
        return worked
    
    def _monitor_loop(self):
        """Main monitoring loop (runs in separate thread)"""
        while self.running:
            try:
                for instance in self.wsjt_instances:
                    log_path = instance.get('log_path', '')
                    band_name = instance.get('name', 'Unknown')
                    
                    if not log_path or not os.path.exists(log_path):
                        continue
                    
                    # Monitor ALL.TXT file for real-time decodes
                    all_txt = Path(log_path) / 'ALL.TXT'
                    if all_txt.exists():
                        self._check_file(all_txt, band_name)
                
                # Check every 2 seconds
                time.sleep(2)
            
            except Exception as e:
                print(f"LogMonitor: Error in monitor loop: {e}")
                time.sleep(5)
    
    def _check_file(self, filepath, band_name):
        """Check file for new lines since last check"""
        try:
            # Get current file size
            current_size = os.path.getsize(filepath)
            last_position = self.file_positions.get(str(filepath), 0)
            
            # First time seeing this file? Jump to end to avoid processing old data
            if str(filepath) not in self.file_positions:
                self.file_positions[str(filepath)] = current_size
                print(f"LogMonitor: {band_name}: Initialized at position {current_size}")
                return
            
            # Only read if file grew
            if current_size > last_position:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(last_position)
                    new_lines = f.readlines()
                    self.file_positions[str(filepath)] = f.tell()
                
                if new_lines:
                    print(f"LogMonitor: {band_name}: Processing {len(new_lines)} new decode(s)")
                
                # Process new lines
                for line in new_lines:
                    self._process_decode_line(line, band_name)
            
            # Handle file rotation (file got smaller)
            elif current_size < last_position:
                self.file_positions[str(filepath)] = 0
                print(f"LogMonitor: {band_name}: File rotated, resetting position")
        
        except Exception as e:
            print(f"LogMonitor: Error reading {filepath}: {e}")
    
    def _process_decode_line(self, line, band_name):
        """Process a single decode line from ALL.TXT"""
        try:
            # WSJT-X ALL.TXT format:
            # 210117_235900    28.076 Rx FT8    -10  0.3 1500 CQ K5ABC EM04
            # 210117_235915    28.076 Tx FT8     0  0.0 1500 CQ N5ZY EM15
            # YYMMDD_HHMMSS    FREQ   Rx/Tx MODE   SNR  DT  FREQ MESSAGE
            
            line = line.strip()
            if not line:
                return
            
            # Skip our own transmissions (Tx lines)
            if ' Tx ' in line or '\tTx\t' in line or '\tTx ' in line or ' Tx\t' in line:
                return
            
            # Split and find where the message starts
            parts = line.split()
            if len(parts) < 7:
                return
            
            # Extract actual frequency from line (usually 2nd field like "432.174" or "1296.174")
            actual_freq_mhz = None
            for part in parts[1:5]:  # Check first few fields after timestamp
                try:
                    freq = float(part)
                    if 1.8 <= freq <= 30000:  # Valid ham frequency range in MHz
                        actual_freq_mhz = freq
                        break
                except ValueError:
                    continue
            
            # Find the message - look for CQ or a callsign-like pattern
            message = None
            for i, part in enumerate(parts):
                # Message often starts with CQ, or a callsign
                if part.upper() == 'CQ' or (len(part) >= 3 and any(c.isdigit() for c in part) and any(c.isalpha() for c in part)):
                    # Check if this looks like start of FT8 message
                    # Skip if it's clearly a number (like frequency offset)
                    if part.replace('.', '').replace('-', '').replace('+', '').isdigit():
                        continue
                    # Skip mode indicators
                    if part.upper() in ['FT8', 'FT4', 'JT65', 'JT9', 'Q65', 'MSK144', 'RX', 'TX']:
                        continue
                    message = ' '.join(parts[i:])
                    break
            
            if not message:
                return

            # Skip if this is OUR OWN message (we're calling CQ or responding)
            # Check if first callsign in message is N5ZY
            if message.upper().startswith('CQ N5ZY') or message.upper().startswith('N5ZY '):
                return

            # Parse FT8/FT4 message to extract callsigns and identify transmitter
            # FT8/FT4 message format: [TO] [FROM] [payload]
            #   "CQ W5ABC EM12"        → W5ABC is transmitting (calling CQ)
            #   "CQ DX W5ABC EM12"     → W5ABC is transmitting (directed CQ)
            #   "W5ABC K3LR EM12"      → K3LR is transmitting (calling W5ABC)
            #   "K3LR W5ABC R-15"      → W5ABC is transmitting (responding)
            # The transmitter is always the last callsign (2nd, or 1st after filtering CQ/directives)
            # Pattern covers both letter-prefix (W5ABC, AA5BB) and digit-prefix (7Z1IS, 3DA0RS, 9K2HN) calls
            call_pattern = r'\b([A-Z]{1,2}[0-9][A-Z0-9]*[A-Z]|[0-9][A-Z]{1,2}[0-9][A-Z0-9]*[A-Z])\b'
            msg_calls_raw = re.findall(call_pattern, message, re.IGNORECASE)
            non_calls = {'CQ', 'DX', 'NA', 'EU', 'AS', 'AF', 'SA', 'OC', 'AN', 'RR73', 'RR15', 'RR99'}
            msg_calls = [c.upper() for c in msg_calls_raw if c.upper() not in non_calls]
            transmitter = msg_calls[-1] if msg_calls else None
            band_display = self._freq_to_band(actual_freq_mhz) if actual_freq_mhz else self._extract_band_freq(band_name)

            # Check for pre-listed priority stations (DX! expeditions)
            dx_handled = False
            if self.priority_stations and self.priority_callback:
                for c in msg_calls:
                    if c != 'N5ZY' and c in self.priority_stations:
                        is_transmitting = (c == transmitter)
                        self.priority_callback(band_display, c, actual_freq_mhz, is_transmitting)
                        dx_handled = True
                        break  # One priority alert per decode line

            # Dynamic DX2/DX3 check: fire for the transmitter so PriorityEngine
            # can check against LoTW data (new DXCC entity / new band)
            if (not dx_handled and self.decode_check_callback
                    and transmitter and transmitter != 'N5ZY'):
                self.decode_check_callback(band_display, transmitter, actual_freq_mhz, True)

            # Extract grid square (4 characters: 2 letters + 2 digits)
            grid_match = re.search(r'\b([A-R]{2}\d{2})\b', message, re.IGNORECASE)
            if not grid_match:
                return
            
            grid = grid_match.group(1).upper()
            
            # Extract callsign (before the grid)
            # Pattern covers both letter-prefix (W5ABC) and digit-prefix (7Z1IS, 9K2HN) calls
            call_pattern = r'\b([A-Z]{1,2}[0-9][A-Z0-9]*[A-Z]|[0-9][A-Z]{1,2}[0-9][A-Z0-9]*[A-Z])\b'
            calls = re.findall(call_pattern, message[:grid_match.start()], re.IGNORECASE)
            
            if not calls:
                return
            
            # Last callsign before grid is usually the station
            callsign = calls[-1].upper()
            
            # Skip if callsign is CQ or our own call
            if callsign == 'CQ':
                if len(calls) > 1:
                    callsign = calls[-1].upper()
                else:
                    return
            
            # Skip our own callsign
            if callsign == 'N5ZY':
                return
            
            # Check if calling me (message contains my call or directed to me)
            is_calling_me = False
            if 'N5ZY' in message.upper():
                # More sophisticated check: is it "CALL N5ZY" pattern?
                if re.search(r'\b\w+\s+N5ZY\b', message, re.IGNORECASE):
                    is_calling_me = True
            
            # Check if new grid on this band/radio
            is_new_grid = False
            if band_name not in self.worked_grids:
                self.worked_grids[band_name] = set()
            
            if grid not in self.worked_grids[band_name]:
                is_new_grid = True
                self.worked_grids[band_name].add(grid)
            
            # Callback with decode info
            if is_new_grid or is_calling_me:
                # Use actual frequency from line if available, otherwise fall back to band name
                if actual_freq_mhz:
                    # Skip HF (below 50 MHz) unless in Daily DX mode
                    if actual_freq_mhz < 50 and self.contest_mode != 'daily_dx':
                        return
                    band_display = self._freq_to_band(actual_freq_mhz)
                else:
                    band_display = self._extract_band_freq(band_name)
                print(f"LogMonitor: ALERT - {callsign} in {grid} on {band_display} (freq={actual_freq_mhz}, new_grid={is_new_grid}, calling_me={is_calling_me})")
                self.callback(band_display, callsign, grid, is_new_grid, is_calling_me)
        
        except Exception as e:
            print(f"LogMonitor: Error processing line: {e}")
    
    def _freq_to_band(self, freq_mhz):
        """Convert frequency in MHz to band name for display"""
        # HF bands
        if 1.8 <= freq_mhz <= 2.0:
            return "160m"
        elif 3.5 <= freq_mhz <= 4.0:
            return "80m"
        elif 5.3 <= freq_mhz <= 5.4:
            return "60m"
        elif 7.0 <= freq_mhz <= 7.3:
            return "40m"
        elif 10.1 <= freq_mhz <= 10.15:
            return "30m"
        elif 14.0 <= freq_mhz <= 14.35:
            return "20m"
        elif 18.068 <= freq_mhz <= 18.168:
            return "17m"
        elif 21.0 <= freq_mhz <= 21.45:
            return "15m"
        elif 24.89 <= freq_mhz <= 24.99:
            return "12m"
        elif 28 <= freq_mhz <= 30:
            return "10m"
        # VHF+ bands
        elif 50 <= freq_mhz <= 54:
            return "6m"
        elif 144 <= freq_mhz <= 148:
            return "2m"
        elif 222 <= freq_mhz <= 225:
            return "1.25m"
        elif 420 <= freq_mhz <= 450:
            return "70cm"
        elif 902 <= freq_mhz <= 928:
            return "33cm"
        elif 1240 <= freq_mhz <= 1300:
            return "23cm"
        elif 2300 <= freq_mhz <= 2450:
            return "13cm"
        elif 3300 <= freq_mhz <= 3500:
            return "9cm"
        elif 5650 <= freq_mhz <= 5925:
            return "5cm"
        elif 10000 <= freq_mhz <= 10500:
            return "3cm"
        else:
            return f"{freq_mhz:.1f}MHz"
    
    def _extract_band_freq(self, band_name):
        """Extract frequency from band name for display purposes.
        
        Note: With radio-based instance names (like 'IC-9700'), we can't know
        the exact band from ALL.TXT alone. This returns the first matching band
        hint or the instance name itself.
        """
        # Map band names/hints to band display names (consistent with _freq_to_band output)
        band_map = {
            '6m': '6m',
            'hf': 'HF',
            '2m': '2m',
            '222': '1.25m',
            '1.25m': '1.25m',
            '432': '70cm',
            '70cm': '70cm',
            '902': '33cm',
            '33cm': '33cm',
            '1296': '23cm',
            '23cm': '23cm',
            '10g': '3cm',
        }
        
        name_lower = band_name.lower()
        for key, freq in band_map.items():
            if key in name_lower:
                return freq
        
        # If no band hint found, return a shortened version of the instance name
        # e.g., "IC-7610 (6m/HF)" -> "IC-7610"
        if '(' in band_name:
            return band_name.split('(')[0].strip()
        return band_name
    
    def get_worked_grids(self, band):
        """Get list of worked grids for a band"""
        return self.worked_grids.get(band, set())
