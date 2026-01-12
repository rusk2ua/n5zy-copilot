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
    def __init__(self, wsjt_instances, callback):
        """
        Initialize log monitor
        
        Args:
            wsjt_instances: List of WSJT-X instance configs with log paths
            callback: Function to call with (band, callsign, grid, is_new_grid, is_calling_me)
        """
        self.wsjt_instances = wsjt_instances
        self.callback = callback
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
            
            # Only read if file grew
            if current_size > last_position:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(last_position)
                    new_lines = f.readlines()
                    self.file_positions[str(filepath)] = f.tell()
                
                # Process new lines
                for line in new_lines:
                    self._process_decode_line(line, band_name)
            
            # Handle file rotation (file got smaller)
            elif current_size < last_position:
                self.file_positions[str(filepath)] = 0
        
        except Exception as e:
            print(f"LogMonitor: Error reading {filepath}: {e}")
    
    def _process_decode_line(self, line, band_name):
        """Process a single decode line from ALL.TXT"""
        try:
            # ALL.TXT format:
            # YYMMDD HHMMSS  -## ~#.## #### @@ Msg text
            # Example: 250110 143000 -15  0.3 1234 @ CQ K5ABC EM04
            
            # Parse the line
            parts = line.strip().split()
            if len(parts) < 6:
                return
            
            # Extract message text (everything after frequency offset)
            # Find the @ symbol which marks start of message
            at_index = line.find('@')
            if at_index == -1:
                return
            
            message = line[at_index+1:].strip()
            
            # Look for callsign and grid patterns
            # Common patterns: "CQ CALL GRID", "CALL GRID", "CALL CALL GRID"
            
            # Extract grid square (4 characters: 2 letters + 2 digits)
            grid_match = re.search(r'\b([A-R]{2}\d{2})\b', message, re.IGNORECASE)
            if not grid_match:
                return
            
            grid = grid_match.group(1).upper()
            
            # Extract callsign (before the grid)
            # Callsign pattern: alphanumeric with at least one digit
            call_pattern = r'\b([A-Z0-9]{3,})\b'
            calls = re.findall(call_pattern, message[:grid_match.start()], re.IGNORECASE)
            
            if not calls:
                return
            
            # Last callsign before grid is usually the station
            callsign = calls[-1].upper()
            
            # Check if calling me (message contains my call or directed to me)
            is_calling_me = False
            if 'N5ZY' in message.upper():
                # More sophisticated check: is it "CALL N5ZY" pattern?
                if re.search(r'\b\w+\s+N5ZY\b', message, re.IGNORECASE):
                    is_calling_me = True
            
            # Check if new grid on this band
            is_new_grid = False
            if band_name not in self.worked_grids:
                self.worked_grids[band_name] = set()
            
            if grid not in self.worked_grids[band_name]:
                is_new_grid = True
                self.worked_grids[band_name].add(grid)
            
            # Callback with decode info
            if is_new_grid or is_calling_me:
                # Extract band frequency from band name
                band_freq = self._extract_band_freq(band_name)
                self.callback(band_freq, callsign, grid, is_new_grid, is_calling_me)
        
        except Exception as e:
            print(f"LogMonitor: Error processing line '{line}': {e}")
    
    def _extract_band_freq(self, band_name):
        """Extract frequency from band name"""
        # Map band names to frequencies
        band_map = {
            '6m': '50',
            '2m': '144',
            '222': '222',
            '432': '432',
            '70cm': '432',
            '902': '902',
            '1296': '1296',
            '23cm': '1296'
        }
        
        for key, freq in band_map.items():
            if key in band_name.lower():
                return freq
        
        return band_name
    
    def get_worked_grids(self, band):
        """Get list of worked grids for a band"""
        return self.worked_grids.get(band, set())
