"""
QSY Advisor Module
Tracks which bands stations have operated in past VHF contests.
Suggests QSY opportunities when you work a multi-band capable station.

Data sources:
- 3830scores.com band breakdowns
- Your own past contest logs
- Manual additions
"""

import json
import os
from pathlib import Path
from datetime import datetime

class QSYAdvisor:
    """
    Tracks station band capabilities from past contests.
    Alerts when you work someone who has other bands available.
    """
    
    # Standard VHF contest bands
    BANDS = ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10368']
    
    # Band display names
    BAND_NAMES = {
        '50': '6m',
        '144': '2m', 
        '222': '1.25m',
        '432': '70cm',
        '902': '33cm',
        '1296': '23cm',
        '2304': '13cm',
        '3456': '9cm',
        '5760': '6cm',
        '10368': '3cm'
    }
    
    def __init__(self, data_dir=None):
        """
        Initialize QSY Advisor
        
        Args:
            data_dir: Directory containing station database (default: data/)
        """
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / 'data'
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        # Station database: {callsign: {bands: set(), grids: set(), last_seen: date, contests: list}}
        self.stations = {}
        
        # Current contest worked stations: {callsign: set(bands_worked)}
        self.current_contest = {}
        
        # Callback for QSY alerts
        self.qsy_callback = None
        
        # Load station database
        self._load_database()
    
    def _load_database(self):
        """Load station database from JSON file"""
        db_path = self.data_dir / 'station_bands.json'
        
        if db_path.exists():
            try:
                with open(db_path, 'r') as f:
                    data = json.load(f)
                
                # Convert lists back to sets
                for call, info in data.items():
                    self.stations[call] = {
                        'bands': set(info.get('bands', [])),
                        'grids': set(info.get('grids', [])),
                        'last_seen': info.get('last_seen', ''),
                        'contests': info.get('contests', [])
                    }
                
                print(f"QSY Advisor: Loaded {len(self.stations)} stations from database")
                
            except Exception as e:
                print(f"QSY Advisor: Error loading database: {e}")
                self.stations = {}
        else:
            print("QSY Advisor: No station database found, starting fresh")
            self._create_sample_database()
    
    def _save_database(self):
        """Save station database to JSON file"""
        db_path = self.data_dir / 'station_bands.json'
        
        try:
            # Convert sets to lists for JSON serialization
            data = {}
            for call, info in self.stations.items():
                data[call] = {
                    'bands': list(info['bands']),
                    'grids': list(info['grids']),
                    'last_seen': info['last_seen'],
                    'contests': info['contests']
                }
            
            with open(db_path, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            print(f"QSY Advisor: Error saving database: {e}")
    
    def _create_sample_database(self):
        """Create sample database with some known multi-band stations"""
        # This is a starter - you'll want to populate from actual contest results
        # Format: callsign -> bands they've operated
        
        sample_stations = {
            # Example multi-band stations (these are fictional examples)
            # Replace with real data from 3830scores.com
            'W5ZN': {'bands': {'50', '144', '432', '1296'}, 'grids': {'EM35'}, 'last_seen': '2025-09', 'contests': ['Sep 2025']},
            'N5DG': {'bands': {'50', '144', '222', '432'}, 'grids': {'EM12'}, 'last_seen': '2025-09', 'contests': ['Sep 2025']},
            'K5QE': {'bands': {'50', '144', '222', '432', '902', '1296'}, 'grids': {'EM31'}, 'last_seen': '2025-09', 'contests': ['Sep 2025']},
            'W5LUA': {'bands': {'50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10368'}, 'grids': {'EM13'}, 'last_seen': '2025-09', 'contests': ['Sep 2025']},
        }
        
        for call, info in sample_stations.items():
            self.stations[call] = info
        
        self._save_database()
        print(f"QSY Advisor: Created sample database with {len(self.stations)} stations")
    
    def set_qsy_callback(self, callback):
        """
        Set callback for QSY alerts
        
        Args:
            callback: Function(callsign, worked_band, available_bands, message)
        """
        self.qsy_callback = callback
    
    def start_contest(self):
        """Start a new contest - clear current worked stations"""
        self.current_contest = {}
        print("QSY Advisor: New contest started, cleared worked stations")
    
    def add_station(self, callsign, bands, grid=None, contest=None):
        """
        Add or update a station in the database
        
        Args:
            callsign: Station callsign (will be uppercased, /R etc stripped for lookup)
            bands: List of bands the station operates (e.g., ['50', '144', '432'])
            grid: Grid square (optional)
            contest: Contest name (optional)
        """
        # Normalize callsign (remove /R, /P, etc. for base call)
        base_call = self._normalize_call(callsign)
        
        if base_call not in self.stations:
            self.stations[base_call] = {
                'bands': set(),
                'grids': set(),
                'last_seen': '',
                'contests': []
            }
        
        # Add bands
        self.stations[base_call]['bands'].update(bands)
        
        # Add grid if provided
        if grid:
            self.stations[base_call]['grids'].add(grid[:4].upper())  # Store 4-char
        
        # Update last seen
        self.stations[base_call]['last_seen'] = datetime.now().strftime('%Y-%m')
        
        # Add contest if provided
        if contest and contest not in self.stations[base_call]['contests']:
            self.stations[base_call]['contests'].append(contest)
        
        self._save_database()
    
    def _normalize_call(self, callsign):
        """Normalize callsign - remove /R, /P, -# SSID, etc."""
        call = callsign.upper().strip()
        
        # Remove common suffixes
        for suffix in ['/R', '/P', '/M', '/QRP', '/MM', '/AM']:
            if call.endswith(suffix):
                call = call[:-len(suffix)]
        
        # Remove prefix like W5/ 
        if '/' in call:
            parts = call.split('/')
            # Take the longest part (usually the actual call)
            call = max(parts, key=len)
        
        return call
    
    def log_qso(self, callsign, band, grid=None):
        """
        Log a QSO and check for QSY opportunities
        
        Args:
            callsign: Worked station callsign
            band: Band worked (e.g., '144' or '2m')
            grid: Their grid (optional)
            
        Returns:
            dict with QSY info or None if no suggestion
        """
        # Normalize band to MHz
        band = self._normalize_band(band)
        
        # Normalize callsign
        base_call = self._normalize_call(callsign)
        
        # Track this QSO
        if base_call not in self.current_contest:
            self.current_contest[base_call] = set()
        
        self.current_contest[base_call].add(band)
        
        # Check if we know this station
        if base_call in self.stations:
            station_info = self.stations[base_call]
            known_bands = station_info['bands']
            worked_bands = self.current_contest[base_call]
            
            # Find bands they have that we haven't worked them on yet
            available_bands = known_bands - worked_bands
            
            if available_bands:
                # Sort by frequency
                available_sorted = sorted(available_bands, key=lambda b: int(b) if b.isdigit() else 0)
                
                # Build message
                available_names = [self.BAND_NAMES.get(b, b) for b in available_sorted]
                message = f"{callsign} also operates on: {', '.join(available_names)}"
                
                result = {
                    'callsign': callsign,
                    'base_call': base_call,
                    'worked_band': band,
                    'available_bands': available_sorted,
                    'available_names': available_names,
                    'message': message
                }
                
                # Trigger callback
                if self.qsy_callback:
                    self.qsy_callback(
                        callsign, 
                        band, 
                        available_sorted,
                        message
                    )
                
                return result
        
        return None
    
    def _normalize_band(self, band):
        """Convert band to MHz format"""
        band = str(band).upper().strip()
        
        # Remove 'M', 'MHZ', 'CM', etc.
        band = band.replace('MHZ', '').replace('CM', '').replace('M', '').strip()
        
        # Map common names
        name_map = {
            '6': '50',
            '2': '144',
            '1.25': '222',
            '70': '432',
            '33': '902',
            '23': '1296',
            '13': '2304',
            '9': '3456',
            '5': '5760',
            '3': '10368',
        }
        
        return name_map.get(band, band)
    
    def get_station_info(self, callsign):
        """Get known info about a station"""
        base_call = self._normalize_call(callsign)
        
        if base_call in self.stations:
            info = self.stations[base_call]
            bands = sorted(info['bands'], key=lambda b: int(b) if b.isdigit() else 0)
            band_names = [self.BAND_NAMES.get(b, b) for b in bands]
            
            return {
                'callsign': base_call,
                'bands': bands,
                'band_names': band_names,
                'grids': list(info['grids']),
                'last_seen': info['last_seen'],
                'contests': info['contests']
            }
        
        return None
    
    def get_unworked_bands(self, callsign):
        """Get bands a station has that we haven't worked them on yet"""
        base_call = self._normalize_call(callsign)
        
        if base_call not in self.stations:
            return []
        
        known_bands = self.stations[base_call]['bands']
        worked_bands = self.current_contest.get(base_call, set())
        
        available = known_bands - worked_bands
        return sorted(available, key=lambda b: int(b) if b.isdigit() else 0)
    
    def import_from_cabrillo(self, filepath, contest_name=None):
        """
        Import station band data from a Cabrillo log file
        
        Args:
            filepath: Path to Cabrillo file
            contest_name: Name to record (default: derived from file)
        """
        try:
            with open(filepath, 'r') as f:
                lines = f.readlines()
            
            if contest_name is None:
                contest_name = Path(filepath).stem
            
            stations_found = {}
            
            for line in lines:
                if line.startswith('QSO:'):
                    parts = line.split()
                    if len(parts) >= 8:
                        # QSO: freq mode date time mycall mygrid theircall theirgrid
                        freq = parts[1]
                        their_call = parts[7] if len(parts) > 7 else parts[6]
                        their_grid = parts[8] if len(parts) > 8 else None
                        
                        # Convert freq to band
                        try:
                            freq_mhz = float(freq) if '.' in freq else float(freq) / 1000
                            band = self._freq_to_band(freq_mhz)
                        except:
                            continue
                        
                        base_call = self._normalize_call(their_call)
                        
                        if base_call not in stations_found:
                            stations_found[base_call] = {'bands': set(), 'grid': None}
                        
                        stations_found[base_call]['bands'].add(band)
                        if their_grid and len(their_grid) >= 4:
                            stations_found[base_call]['grid'] = their_grid[:4]
            
            # Add to database
            for call, info in stations_found.items():
                self.add_station(call, list(info['bands']), info['grid'], contest_name)
            
            print(f"QSY Advisor: Imported {len(stations_found)} stations from {filepath}")
            
        except Exception as e:
            print(f"QSY Advisor: Error importing Cabrillo: {e}")
    
    def _freq_to_band(self, freq_mhz):
        """Convert frequency in MHz to band"""
        if 50 <= freq_mhz < 54:
            return '50'
        elif 144 <= freq_mhz < 148:
            return '144'
        elif 222 <= freq_mhz < 225:
            return '222'
        elif 420 <= freq_mhz < 450:
            return '432'
        elif 902 <= freq_mhz < 928:
            return '902'
        elif 1240 <= freq_mhz < 1300:
            return '1296'
        elif 2300 <= freq_mhz < 2450:
            return '2304'
        elif 3300 <= freq_mhz < 3500:
            return '3456'
        elif 5650 <= freq_mhz < 5925:
            return '5760'
        elif 10000 <= freq_mhz < 10500:
            return '10368'
        else:
            return str(int(freq_mhz))
    
    def import_from_3830(self, text_data, contest_name):
        """
        Import from 3830scores.com breakdown format
        
        Args:
            text_data: Text copied from 3830 band breakdown page
            contest_name: Contest name (e.g., "Jan 2025 VHF")
        """
        # This is a placeholder - 3830 format varies
        # You would parse the specific format from their pages
        pass
    
    def get_stats(self):
        """Get database statistics"""
        total_stations = len(self.stations)
        
        # Count stations by number of bands
        band_counts = {}
        for info in self.stations.values():
            num_bands = len(info['bands'])
            band_counts[num_bands] = band_counts.get(num_bands, 0) + 1
        
        # Count current contest
        current_qsos = sum(len(bands) for bands in self.current_contest.values())
        
        return {
            'total_stations': total_stations,
            'band_distribution': band_counts,
            'current_contest_stations': len(self.current_contest),
            'current_contest_qsos': current_qsos
        }


def parse_3830_scores(url_or_file):
    """
    Helper to parse 3830scores.com data
    
    This would need to be customized based on actual page format.
    For now, returns instructions on manual data entry.
    """
    instructions = """
    To populate the station database from 3830scores.com:
    
    1. Go to https://www.3830scores.com/contests.php
    2. Click on "ARRL January VHF Contest" (or June/September)
    3. Select a recent year
    4. Click "Band Breakdowns" link
    5. Copy the data showing callsigns and their band QSOs
    
    Then manually add stations using:
    
        advisor = QSYAdvisor()
        advisor.add_station('W5XYZ', ['50', '144', '432'], 'EM12', 'Jan 2025')
    
    Or import from your own Cabrillo logs:
    
        advisor.import_from_cabrillo('my_contest.log', 'Jan 2025')
    """
    return instructions
