#!/usr/bin/env python3
"""
N5ZY VHF Contest Co-Pilot
Main application
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json
import os
import time
import threading
from pathlib import Path
from datetime import datetime

# Import our modules
from modules.credential_store import encrypt_config, decrypt_config, needs_migration
from modules.gps_monitor import GPSMonitor
from modules.battery_monitor import BatteryMonitor
from modules.radio_updater import RadioUpdater
from modules.log_monitor import LogMonitor
from modules.voice_alerts import VoiceAlerter
from modules.aprs_client import APRSClient
from modules.qsy_advisor import QSYAdvisor
from modules.grid_boundary import GridBoundaryMonitor
from modules.psk_monitor import PSKMonitor
from modules.qsoparty_parser import (
    get_default_qsoparty_path, 
    parse_qsoparty_file, 
    get_county_list_for_display,
    get_canonical_county
)
from modules.county_lookup import CountyLookupService, CountyInfo

# Contest mode constants
CONTEST_MODES = {
    'vhf': 'VHF Contest (4-char grid)',
    '222up': '222 MHz and Up (6-char grid)',
    'qso_party': 'State QSO Party (County)',
    'daily_dx': 'Daily DX (4-char grid)',
}

LOGGER_NAMES = {'n1mm': 'N1MM+', 'n3fjp': 'N3FJP', 'log4om': 'Log4OM'}

# HF bands (including WARC bands) for QSO parties
HF_BANDS = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']
VHF_BANDS = ['6m', '2m', '1.25m', '70cm', '33cm', '23cm', '13cm', '9cm', '5cm', '3cm']
ALL_BANDS = HF_BANDS + VHF_BANDS

class CoPilotApp:
    VERSION = "1.9.5"
    
    def __init__(self, root):
        self.root = root
        self.root.title(f"N5ZY VHF Contest Co-Pilot v{self.VERSION}")
        self.root.geometry("850x550")  # Default size
        self.root.minsize(700, 400)    # Minimum size
        
        # Config file location
        self.config_file = Path("config/settings.json")
        self.load_config()
        
        # Initialize modules
        self.gps_monitor = None
        self.battery_monitor = None
        self.radio_updater = None
        self.log_monitor = None
        self.aprs_client = None
        self.psk_monitor = None
        self.voice = VoiceAlerter()
        self.voice.load_settings(self.config)
        self.qsy_advisor = QSYAdvisor()
        self.qsy_advisor.set_qsy_callback(self.on_qsy_opportunity)
        self.grid_boundary = GridBoundaryMonitor(self.on_boundary_announcement)
        
        # County lookup (uses Census TIGER shapefile)
        self.county_lookup = None
        self.current_county_info = None  # CountyInfo object for current location
        self._last_county_name = ""      # For detecting county changes
        self._load_county_shapefile()
        
        # Current state
        self.current_grid = "----"
        self.current_county = ""  # For QSO Party mode (abbreviation sent to N1MM+)
        self.current_lat = None   # GPS latitude for ADIF stamping
        self.current_lon = None   # GPS longitude for ADIF stamping
        self.battery_voltage = 0.0
        self.battery_current = 0.0
        self.battery_soc = 100.0
        
        # SMS rate limiting
        self._sms_last_send_time = 0
        self._sms_station_cooldowns = {}  # {callsign: last_send_time} for per-station 5-min cooldown

        # APRS nearby stations for rover broadcasts: {callsign: (distance_mi, bearing, timestamp)}
        self.nearby_aprs_stations = {}

        # DX2/DX3 decode check dedup: {callsign: time.time()} — 2-min cooldown
        self._decode_check_recent = {}

        # QSO Party data (loaded from N1MM+ QSOParty.sec file)
        self.qso_parties = {}
        self._load_qsoparty_data()
        
        # Super Check Partial (SCP) callsign database
        self.scp_calls = []
        self.worked_calls = set()  # Callsigns from QSO Log for SCP matching
        self._load_scp_database()
        
        # QRZ session for fallback lookups
        self._qrz_session_key = None
        self._qrz_last_lookup = {}  # Cache: {callsign: (found, timestamp)}
        
        # Ignore list for "calling me" alerts: {callsign: expire_timestamp}
        self.ignored_stations = {}
        self.ignore_duration_minutes = 30

        # GPS Time Sync state
        self._time_sync_timer_id = None        # root.after() ID for scheduled sync
        self._time_sync_last_sync = None       # datetime of last successful sync
        self._time_sync_last_offset_ms = None  # offset at last sync
        self._time_sync_log_path = Path("logs/gps_time_sync.log")
        self._intermittent_port_closed = False  # True when port is deliberately closed
        
        # Shared PSK enabled variable (used by both Settings and PSK Monitor tabs)
        self.psk_enabled_var = tk.BooleanVar(value=self.config.get('psk_enabled', False))
        
        self.create_gui()
        self.start_monitoring()
        
        # Start PSK monitor if enabled in config
        if self.config.get('psk_enabled', False):
            self.root.after(2000, self._start_psk_monitor)  # Delay to let GPS initialize
    
    def load_config(self):
        """Load configuration from JSON file"""
        self.config_file.parent.mkdir(exist_ok=True)
        
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                raw_config = json.load(f)
            self.config = decrypt_config(raw_config)
            # Auto-migrate plain text credentials → encrypted on first load
            if needs_migration(raw_config):
                self.save_config()
        else:
            # Default configuration
            self.config = {
                'gps_port': 'COM3',
                'gps_baudrate': None,         # None = auto-detect, or 4800/9600/19200/38400
                'gps_update_rate_hz': 1,      # NMEA update rate (1, 2, 5, 10 Hz)
                'gps_time_sync_enabled': False,
                'gps_time_sync_interval_minutes': 5,
                'gps_time_sync_intermittent': False,
                'gps_time_sync_update_grid': True,
                'victron_address': '',
                'victron_key': '',
                'grid_precision': 4,  # 4-char for VHF contests, 6-char for 222 and Up
                # Contest mode settings
                'contest_mode': 'vhf',  # 'vhf', '222up', or 'qso_party'
                'qso_party_code': 'OK',  # QSO party code (e.g., OK, TX, 7QP, MAQP)
                'qso_party_county': '',  # Current county abbreviation
                'qsoparty_file': get_default_qsoparty_path(),  # N1MM+ QSOParty.sec file
                'wsjt_instances': [
                    {'name': 'IC-7610 (6m/HF)', 'log_path': '', 'udp_port': 2237},
                    {'name': 'IC-9700 (2m/70cm/23cm/10G)', 'log_path': '', 'udp_port': 2238},
                    {'name': 'IC-7300 (1.25m/33cm xvtr)', 'log_path': '', 'udp_port': 2239}
                ],
                # Contest logger settings
                'contest_logger': 'n1mm',  # 'n1mm' or 'n3fjp'
                'n1mm_udp_host': '127.0.0.1',
                'n1mm_udp_port': 52001,  # N1MM+ JTDX TCP port (Config → Configure Ports → WSJT/JTDX Setup)
                'n3fjp_host': '127.0.0.1',
                'n3fjp_port': 1100,  # N3FJP default API port
                'log4om_host': '127.0.0.1',
                'log4om_port': 2333,  # Log4OM v2 default UDP port
                'active_bands': ['50', '144', '222', '432', '902', '1296', '10368'],
                # APRS-IS settings
                'aprs_enabled': False,
                'aprs_callsign': 'N5ZY',  # Your callsign (add -9 for mobile SSID if desired)
                'aprs_beacon_interval': 600,  # 10 minutes
                'aprs_alert_radius': 10,  # miles
                'aprs_comment': 'N5ZY.ORG Rover!',  # Beacon comment
                # Priority station alerts
                'psk_priority_enabled': False,
                'psk_priority_stations': '',  # Legacy — migrated to dx_priority_stations
                'dx_priority_stations': '',   # DX! CSV (Daily DX mode)
                'ap_priority_stations': '',   # AP! CSV (VHF/QSO Party modes)
                'dx2_enabled': False,         # DX2 — new DXCC entity alerts
                'dx3_enabled': False,         # DX3 — new band/mode alerts
                'dx3_granularity': 'band',    # 'band' | 'mode' | 'band_mode'
                'lotw_username': '',
                'lotw_password': '',
                'lotw_auto_refresh': True,
                'lotw_last_refresh': '',      # ISO date
                'cty_last_update': '',        # ISO date
                # Grid boundary alerts
                'grid_boundary_alerts': False,  # Voice alerts when approaching grid edges
                # Voice alert controls
                'voice_enabled': True,
                'voice_disabled_categories': [],
                # QRZ lookup (fallback when SCP has no matches)
                'qrz_username': '',
                'qrz_password': '',
                # SMS (Twilio) notification settings
                'sms_enabled': False,
                'twilio_account_sid': '',
                'twilio_auth_token': '',
                'twilio_from_number': '',
                'twilio_to_number': '',
                'sms_on_priority': True,     # DX! priority stations
                'sms_on_dx2': True,          # New DXCC entity
                'sms_on_dx3': True,          # New DXCC on band
                'sms_on_new_grid': False,    # New grid (can be chatty)
            }
            self.save_config()
    
    def _load_qsoparty_data(self):
        """Load QSO Party data from N1MM+ QSOParty.sec file"""
        filepath = self.config.get('qsoparty_file', get_default_qsoparty_path())
        self.qso_parties = parse_qsoparty_file(filepath)
        if self.qso_parties:
            print(f"Loaded {len(self.qso_parties)} QSO parties")
    
    def _load_scp_database(self):
        """Load Super Check Partial database from master.dta file"""
        # Check common locations for master.dta
        possible_paths = [
            'data/master.dta',
            'data/MASTER.DTA',
            Path.home() / 'Documents' / 'N1MM Logger+' / 'CallHistory' / 'master.dta',
            Path.home() / 'Documents' / 'N1MM Logger+' / 'master.dta',
            self.config.get('scp_file', ''),
        ]
        
        scp_path = None
        for path in possible_paths:
            if path and Path(path).exists():
                scp_path = Path(path)
                break
        
        if not scp_path:
            print("SCP: master.dta not found - Super Check Partial disabled")
            print("  Download from http://supercheckpartial.com and place in data/master.dta")
            return
        
        try:
            with open(scp_path, 'r', encoding='utf-8', errors='ignore') as f:
                self.scp_calls = [line.strip().upper() for line in f if line.strip() and not line.startswith('#')]
            print(f"SCP: Loaded {len(self.scp_calls)} callsigns from {scp_path}")
        except Exception as e:
            print(f"SCP: Error loading master.dta: {e}")
            self.scp_calls = []
    
    def _load_county_shapefile(self):
        """Load county boundaries shapefile for GPS-based county detection"""
        shapefile_path = self.config.get('county_shapefile', 'data/us_counties_10m.shp')
        
        print(f"County Lookup: Looking for shapefile at: {shapefile_path}")
        
        if not Path(shapefile_path).exists():
            print(f"  Shapefile not found - county auto-detection disabled")
            print(f"  Download from Census Bureau and place at: {shapefile_path}")
            self.county_lookup = None
            return
        
        try:
            self.county_lookup = CountyLookupService()
            self.county_lookup.load_shapefile(shapefile_path)
            print(f"  SUCCESS: Loaded {self.county_lookup.county_count} counties")
            
            # Test lookup with Oklahoma City coordinates
            test_info = self.county_lookup.lookup(35.4676, -97.5164)
            if test_info:
                print(f"  Test lookup (OKC): {test_info.name}, {test_info.state_abbrev} ✓")
            else:
                print(f"  Test lookup (OKC): FAILED - no result")
                
        except ImportError as e:
            print(f"  ERROR: Missing dependency - {e}")
            print("  Run: pip install pyshp shapely")
            self.county_lookup = None
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.county_lookup = None
    
    def _grid_to_latlon(self, grid):
        """Convert Maidenhead grid to lat/lon (center of grid)"""
        if not grid or len(grid) < 4:
            return None, None
        
        grid = grid.upper()
        
        try:
            lon = (ord(grid[0]) - ord('A')) * 20 - 180
            lat = (ord(grid[1]) - ord('A')) * 10 - 90
            lon += int(grid[2]) * 2
            lat += int(grid[3]) * 1
            
            if len(grid) >= 6:
                lon += (ord(grid[4]) - ord('A')) * (2/24)
                lat += (ord(grid[5]) - ord('A')) * (1/24)
                lon += (2/24) / 2
                lat += (1/24) / 2
            else:
                lon += 1
                lat += 0.5
            
            return lat, lon
        except:
            return None, None
    
    def _haversine(self, lat1, lon1, lat2, lon2):
        """Calculate distance in miles between two lat/lon points"""
        import math
        R = 3959  # Earth radius in miles
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_lat/2)**2 + 
             math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def _bearing(self, lat1, lon1, lat2, lon2):
        """Calculate bearing in degrees from point 1 to point 2"""
        import math
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lon = math.radians(lon2 - lon1)
        
        x = math.sin(delta_lon) * math.cos(lat2_rad)
        y = (math.cos(lat1_rad) * math.sin(lat2_rad) - 
             math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon))
        
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360
    
    def _bearing_to_compass(self, bearing):
        """Convert bearing degrees to compass direction"""
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        idx = int((bearing + 22.5) / 45) % 8
        return directions[idx]
    
    def save_config(self):
        """Save configuration to JSON file (sensitive fields encrypted on disk)"""
        encrypted = encrypt_config(self.config)
        with open(self.config_file, 'w') as f:
            json.dump(encrypted, f, indent=2)
    
    def create_gui(self):
        """Create the main GUI"""
        
        # Top status bar
        status_frame = ttk.Frame(self.root, relief=tk.RAISED, borderwidth=2)
        status_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # GPS lock indicator (colored circle)
        self.gps_indicator = tk.Label(status_frame, text="●", font=('Arial', 16, 'bold'),
                                       fg='red')  # Starts red (no lock)
        self.gps_indicator.pack(side=tk.LEFT, padx=(5, 2))
        
        # Grid display (large) - shortened label
        ttk.Label(status_frame, text="Grid:", font=('Arial', 12)).pack(side=tk.LEFT, padx=2)
        self.grid_label = ttk.Label(status_frame, text=self.current_grid, 
                                     font=('Arial', 24, 'bold'), foreground='blue')
        self.grid_label.pack(side=tk.LEFT, padx=5)
        
        # County display (for QSO Party mode - initially hidden) - shortened label
        self.county_frame = ttk.Frame(status_frame)
        ttk.Label(self.county_frame, text="Cnty:", font=('Arial', 12)).pack(side=tk.LEFT, padx=2)
        self.county_label = ttk.Label(self.county_frame, text="----", 
                                       font=('Arial', 18, 'bold'), foreground='purple')
        self.county_label.pack(side=tk.LEFT, padx=5)
        # Will be shown/hidden by _update_contest_mode_ui()
        
        # Battery voltage (always visible) - shortened label
        ttk.Label(status_frame, text="Bat:", font=('Arial', 12)).pack(side=tk.LEFT, padx=15)
        self.voltage_label = ttk.Label(status_frame, text="--.-V", 
                                        font=('Arial', 18, 'bold'), foreground='green')
        self.voltage_label.pack(side=tk.LEFT, padx=5)
        
        # WSJT-X instance status indicators
        wsjt_frame = ttk.Frame(status_frame)
        wsjt_frame.pack(side=tk.LEFT, padx=20)
        ttk.Label(wsjt_frame, text="WSJT:", font=('Arial', 10)).pack(side=tk.LEFT)
        
        # Create status labels for each configured instance (use custom names)
        self.wsjt_status_labels = {}
        self.wsjt_last_seen = {}  # Track last heartbeat time per instance
        
        for instance in self.config.get('wsjt_instances', []):
            name = instance.get('name', '').strip()
            path = instance.get('log_path', '').strip()
            
            # Skip empty instances
            if not name and not path:
                continue
            
            # Use name if provided, otherwise extract from path or use port
            if name:
                # Use first word or short version of name
                short = name.split()[0] if ' ' in name else name
                if len(short) > 10:
                    short = short[:10]
            else:
                short = f"Radio {len(self.wsjt_status_labels) + 1}"
            
            # Create label - starts red (not connected)
            lbl = tk.Label(wsjt_frame, text=short, font=('Arial', 9, 'bold'),
                          fg='white', bg='red', padx=3, pady=1)
            lbl.pack(side=tk.LEFT, padx=2)
            
            port = instance.get('udp_port', 2237)
            self.wsjt_status_labels[port] = lbl
            self.wsjt_last_seen[port] = 0  # Never seen
        
        # If no instances configured, show placeholder
        if not self.wsjt_status_labels:
            lbl = tk.Label(wsjt_frame, text="(none)", font=('Arial', 9),
                          fg='gray')
            lbl.pack(side=tk.LEFT, padx=2)
        
        # Start watchdog timer for WSJT-X status
        self._start_wsjt_watchdog()
        
        # Logger ROVERQTH/Grid button (always visible at top)
        logger_name = LOGGER_NAMES.get(self.config.get('contest_logger', 'n1mm'), 'N1MM+')
        self.logger_button = ttk.Button(status_frame, text=f"Send to {logger_name}: ----", 
                                       command=self.send_grid_to_logger, state='disabled')
        self.logger_button.pack(side=tk.RIGHT, padx=10)
        
        # APRS enable checkbox (mirrors the one in Settings)
        self.aprs_enabled_var = tk.BooleanVar(value=self.config.get('aprs_enabled', False))
        self.aprs_checkbox = ttk.Checkbutton(status_frame, text="APRS", 
                                              variable=self.aprs_enabled_var,
                                              command=self.toggle_aprs)
        self.aprs_checkbox.pack(side=tk.RIGHT, padx=5)
        
        # PSK enable checkbox (mirrors the one in Settings/PSK Monitor tab)
        self.psk_status_checkbox = ttk.Checkbutton(status_frame, text="PSK", 
                                              variable=self.psk_enabled_var,
                                              command=self._toggle_psk_monitor)
        self.psk_status_checkbox.pack(side=tk.RIGHT, padx=5)
        
        # Configure notebook tab styling for better visibility while mobile
        style = ttk.Style()
        style.configure('TNotebook.Tab', 
                        padding=[12, 6],      # horizontal, vertical padding for spacing
                        font=('TkDefaultFont', 10))
        style.map('TNotebook.Tab',
                  font=[('selected', ('TkDefaultFont', 10, 'bold'))],  # Bold when selected
                  padding=[('selected', [12, 8])])  # Slightly taller when selected
        
        # Notebook for tabs
        self.notebook = notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Tab 1: Alerts
        self.alerts_tab = self.create_alerts_tab(notebook)
        notebook.add(self.alerts_tab, text="Alerts")
        
        # Tab 2: Manual Entry
        self.manual_tab = self.create_manual_entry_tab(notebook)
        notebook.add(self.manual_tab, text="Manual Entry")
        
        # Tab 3: QSO Log (from all WSJT-X instances)
        self.qso_log_tab = self.create_qso_log_tab(notebook)
        notebook.add(self.qso_log_tab, text="QSO Log")
        
        # Tab 4: PSK Monitor - band activity and propagation
        self.psk_tab = self.create_psk_monitor_tab(notebook)
        notebook.add(self.psk_tab, text="PSK Monitor")
        
        # Tab 5: APRS Messages - send/receive APRS messages
        self.aprs_tab = self.create_aprs_messages_tab(notebook)
        notebook.add(self.aprs_tab, text="APRS Msgs")
        
        # Tab 6: QSY Advisor - browse station database
        self.qsy_tab = self.create_qsy_advisor_tab(notebook)
        notebook.add(self.qsy_tab, text="QSY Advisor")
        
        # Tab 7: Grid Corner - rover-to-rover QSO tracker (special use at grid corners)
        self.grid_corner_tab = self.create_grid_corner_tab(notebook)
        notebook.add(self.grid_corner_tab, text="Grid Corner")
        
        # Tab 8: GPS Logger - track recording and waypoints
        self.gps_logger_tab = self.create_gps_logger_tab(notebook)
        notebook.add(self.gps_logger_tab, text="GPS Logger")
        
        # Tab 9: SMS Notifications
        self.notify_tab = self.create_notify_tab(notebook)
        notebook.add(self.notify_tab, text="Notify")

        # Tab 10: Settings
        self.settings_tab = self.create_settings_tab(notebook)
        notebook.add(self.settings_tab, text="Settings")
        
        # Tab 10: Test Mode
        self.test_tab = self.create_test_tab(notebook)
        notebook.add(self.test_tab, text="Test Mode")
        
        # Tab 11: About / Support
        self.about_tab = self.create_about_tab(notebook)
        notebook.add(self.about_tab, text="About")
        
        # Initialize bands from config after tabs are created
        self._update_manual_entry_bands()
        self._update_grid_corner_bands()
        self._update_vhf_tab_states()

        # Initialize manual entry labels based on contest mode
        self._update_manual_entry_labels()
        
        # Bottom status bar
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=5, pady=2)
        
        self.status_text = ttk.Label(control_frame, text="Starting up...", foreground='gray')
        self.status_text.pack(side=tk.LEFT, padx=5)
    
    def create_alerts_tab(self, parent):
        """Create alerts display tab"""
        frame = ttk.Frame(parent)
        
        # Scrolled text for alerts
        self.alerts_text = tk.Text(frame, height=20, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(frame, command=self.alerts_text.yview)
        self.alerts_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.alerts_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Ignore controls at bottom
        ignore_frame = ttk.Frame(frame)
        ignore_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(ignore_frame, text="Ignore station:").pack(side=tk.LEFT)
        self.ignore_call_var = tk.StringVar()
        ignore_entry = ttk.Entry(ignore_frame, textvariable=self.ignore_call_var, width=12)
        ignore_entry.pack(side=tk.LEFT, padx=5)
        ignore_entry.bind('<Return>', lambda e: self._do_ignore())
        
        ttk.Button(ignore_frame, text="Ignore 30 min", 
                   command=self._do_ignore).pack(side=tk.LEFT, padx=2)
        ttk.Button(ignore_frame, text="Ignore Last", 
                   command=self._ignore_last).pack(side=tk.LEFT, padx=2)
        ttk.Button(ignore_frame, text="Show Ignored", 
                   command=self._show_ignored).pack(side=tk.LEFT, padx=2)
        ttk.Button(ignore_frame, text="Clear All Ignores", 
                   command=self._clear_ignores).pack(side=tk.LEFT, padx=2)
        
        # Initialize last alert callsign tracker
        self.last_alert_callsign = None
        
        return frame
    
    def _do_ignore(self):
        """Ignore station from entry field"""
        call = self.ignore_call_var.get().strip()
        if call:
            self.ignore_station(call)
            self.ignore_call_var.set("")
    
    def _ignore_last(self):
        """Ignore the last station that triggered an alert"""
        if hasattr(self, 'last_alert_callsign') and self.last_alert_callsign:
            self.ignore_station(self.last_alert_callsign)
        else:
            self.add_alert("No recent station to ignore")
    
    def _show_ignored(self):
        """Show currently ignored stations"""
        import time
        self._cleanup_expired_ignores()
        if not self.ignored_stations:
            self.add_alert("No stations currently ignored")
        else:
            now = time.time()
            for call, expire in self.ignored_stations.items():
                remaining = int((expire - now) / 60)
                self.add_alert(f"  {call}: {remaining} min remaining")
    
    def _clear_ignores(self):
        """Clear all ignored stations"""
        count = len(self.ignored_stations)
        self.ignored_stations.clear()
        self.add_alert(f"Cleared {count} ignored station(s)")
    
    # ==================== SMS Notify Tab ====================

    def create_notify_tab(self, parent):
        """Create SMS notifications tab for Twilio alerts and rover status messages"""
        outer_frame = ttk.Frame(parent)

        # Scrollable canvas
        canvas = tk.Canvas(outer_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer_frame, orient=tk.VERTICAL, command=canvas.yview)
        frame = ttk.Frame(canvas)

        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Bind mouse wheel for scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel, add='+')

        # === Twilio Setup ===
        twilio_frame = ttk.LabelFrame(frame, text="Twilio Setup", padding=10)
        twilio_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(twilio_frame, text="Get credentials from twilio.com/console. Uses Twilio REST API for SMS.",
                 foreground="gray").grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 5))

        ttk.Label(twilio_frame, text="Account SID:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.twilio_sid_var = tk.StringVar(value=self.config.get('twilio_account_sid', ''))
        ttk.Entry(twilio_frame, textvariable=self.twilio_sid_var, width=45).grid(row=1, column=1, sticky=tk.W, pady=2, padx=5)

        ttk.Label(twilio_frame, text="Auth Token:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.twilio_token_var = tk.StringVar(value=self.config.get('twilio_auth_token', ''))
        ttk.Entry(twilio_frame, textvariable=self.twilio_token_var, width=45, show='*').grid(row=2, column=1, sticky=tk.W, pady=2, padx=5)

        ttk.Label(twilio_frame, text="From Number:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.twilio_from_var = tk.StringVar(value=self.config.get('twilio_from_number', ''))
        ttk.Entry(twilio_frame, textvariable=self.twilio_from_var, width=20).grid(row=3, column=1, sticky=tk.W, pady=2, padx=5)
        ttk.Label(twilio_frame, text="Your Twilio phone number (+1...)", foreground="gray").grid(row=3, column=2, sticky=tk.W)

        ttk.Label(twilio_frame, text="To Number:").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.twilio_to_var = tk.StringVar(value=self.config.get('twilio_to_number', ''))
        ttk.Entry(twilio_frame, textvariable=self.twilio_to_var, width=20).grid(row=4, column=1, sticky=tk.W, pady=2, padx=5)
        ttk.Label(twilio_frame, text="Your cell phone number (+1...)", foreground="gray").grid(row=4, column=2, sticky=tk.W)

        btn_frame = ttk.Frame(twilio_frame)
        btn_frame.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))
        ttk.Button(btn_frame, text="Test SMS", command=self._test_sms).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btn_frame, text="Save Settings", command=self._save_sms_settings).pack(side=tk.LEFT)

        # === SMS Alert Triggers ===
        alerts_frame = ttk.LabelFrame(frame, text="Automatic SMS Alert Triggers", padding=10)
        alerts_frame.pack(fill=tk.X, padx=5, pady=5)

        self.sms_enabled_var = tk.BooleanVar(value=self.config.get('sms_enabled', False))
        ttk.Checkbutton(alerts_frame, text="Enable SMS Notifications (master toggle)",
                        variable=self.sms_enabled_var).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=2)

        ttk.Label(alerts_frame, text="Send SMS when these events occur (Daily DX / HF mode):",
                 foreground="gray").grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(5, 2))

        self.sms_priority_var = tk.BooleanVar(value=self.config.get('sms_on_priority', True))
        ttk.Checkbutton(alerts_frame, text="Priority Stations (DX!)",
                        variable=self.sms_priority_var).grid(row=2, column=0, sticky=tk.W, padx=(20, 0), pady=1)

        self.sms_dx2_var = tk.BooleanVar(value=self.config.get('sms_on_dx2', True))
        ttk.Checkbutton(alerts_frame, text="New DXCC Entity (DX2)",
                        variable=self.sms_dx2_var).grid(row=3, column=0, sticky=tk.W, padx=(20, 0), pady=1)

        self.sms_dx3_var = tk.BooleanVar(value=self.config.get('sms_on_dx3', True))
        ttk.Checkbutton(alerts_frame, text="New DXCC on Band (DX3)",
                        variable=self.sms_dx3_var).grid(row=4, column=0, sticky=tk.W, padx=(20, 0), pady=1)

        self.sms_new_grid_var = tk.BooleanVar(value=self.config.get('sms_on_new_grid', False))
        ttk.Checkbutton(alerts_frame, text="New Grid",
                        variable=self.sms_new_grid_var).grid(row=5, column=0, sticky=tk.W, padx=(20, 0), pady=1)

        # === Rover Status Messages ===
        rover_frame = ttk.LabelFrame(frame, text="Rover Status Messages", padding=10)
        rover_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(rover_frame, text="Click a template to fill, edit if needed, then press Send:",
                 foreground="gray").grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 5))

        # Template buttons - row 1
        tmpl_row1 = ttk.Frame(rover_frame)
        tmpl_row1.grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=2)
        ttk.Button(tmpl_row1, text="Grid Entry",
                   command=lambda: self._fill_sms_template('grid_entry')).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(tmpl_row1, text="Hilltop",
                   command=lambda: self._fill_sms_template('hilltop')).pack(side=tk.LEFT, padx=5)
        ttk.Button(tmpl_row1, text="Departing",
                   command=lambda: self._fill_sms_template('departing')).pack(side=tk.LEFT, padx=5)

        # Template buttons - row 2
        tmpl_row2 = ttk.Frame(rover_frame)
        tmpl_row2.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=2)
        ttk.Button(tmpl_row2, text="QRT/Break",
                   command=lambda: self._fill_sms_template('qrt')).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(tmpl_row2, text="Band Change",
                   command=lambda: self._fill_sms_template('band_change')).pack(side=tk.LEFT, padx=5)

        # Editable message field
        ttk.Label(rover_frame, text="Message:").grid(row=3, column=0, sticky=tk.W, pady=(10, 2))
        self.sms_message_var = tk.StringVar()
        sms_entry = ttk.Entry(rover_frame, textvariable=self.sms_message_var, width=70)
        sms_entry.grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=2, padx=(0, 5))

        send_btns = ttk.Frame(rover_frame)
        send_btns.grid(row=5, column=0, columnspan=4, sticky=tk.W, pady=(5, 2))
        self._sms_broadcast_btn = ttk.Button(send_btns, text="Send SMS (0)",
                                              command=lambda: self._send_rover_sms())
        self._sms_broadcast_btn.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(send_btns, text="Send Slack",
                   command=lambda: self._send_rover_slack()).pack(side=tk.LEFT, padx=5)
        self._aprs_send_btn = ttk.Button(send_btns, text="Send APRS (0)",
                                          command=lambda: self._send_rover_aprs())
        self._aprs_send_btn.pack(side=tk.LEFT, padx=5)

        rover_frame.columnconfigure(0, weight=1)

        # === SMS Subscriber List ===
        sub_frame = ttk.LabelFrame(frame, text="SMS Subscriber List (Rover Broadcasts)", padding=10)
        sub_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(sub_frame, text="One per line: +15551234567 W1AW  (phone number + optional callsign). "
                  "Paste from Google Sheets.",
                 foreground="gray").pack(anchor=tk.W, pady=(0, 5))

        sub_text_frame = ttk.Frame(sub_frame)
        sub_text_frame.pack(fill=tk.X)

        self.sms_subscribers_text = tk.Text(sub_text_frame, height=6, wrap=tk.WORD,
                                             font=('Consolas', 9))
        sub_scroll = ttk.Scrollbar(sub_text_frame, orient=tk.VERTICAL,
                                    command=self.sms_subscribers_text.yview)
        self.sms_subscribers_text.configure(yscrollcommand=sub_scroll.set)
        self.sms_subscribers_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sub_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Load saved subscribers
        saved_subs = self.config.get('sms_subscribers', '')
        if saved_subs:
            self.sms_subscribers_text.insert('1.0', saved_subs)

        sub_btn_frame = ttk.Frame(sub_frame)
        sub_btn_frame.pack(anchor=tk.W, pady=(5, 0))

        self._sub_count_label = ttk.Label(sub_btn_frame, text="0 subscribers")
        self._sub_count_label.pack(side=tk.LEFT, padx=(0, 10))
        self._update_subscriber_count()

        # Update count when text changes
        self.sms_subscribers_text.bind('<<Modified>>', lambda e: self._on_subscribers_modified())
        self.sms_subscribers_text.bind('<KeyRelease>', lambda e: self._update_subscriber_count())

        # === Message Log ===
        log_frame = ttk.LabelFrame(frame, text="Notification Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.sms_log_text = tk.Text(log_frame, height=10, wrap=tk.WORD, state=tk.DISABLED,
                                     font=('Consolas', 9))
        sms_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.sms_log_text.yview)
        self.sms_log_text.configure(yscrollcommand=sms_scroll.set)

        self.sms_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sms_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Rate limiting state
        self._sms_last_send_time = 0

        return outer_frame

    def _fill_sms_template(self, key):
        """Fill the SMS message field with a rover status template"""
        my_call = self.config.get('aprs_callsign', self.config.get('my_call', 'N5ZY'))
        my_grid = self.current_grid if self.current_grid and self.current_grid != '----' else '[grid]'
        active = self.config.get('active_bands', [])
        bands = '/'.join(active) if active else '[bands]'

        templates = {
            'grid_entry':  f"{my_call} Just crossed into {my_grid}! QRV {bands}",
            'hilltop':     f"{my_call} at {my_grid} hilltop",
            'departing':   f"{my_call} departing {my_grid} for next location",
            'qrt':         f"{my_call} QRT for [meal/rest]. Back QRV at [time] in [grid]",
            'band_change': f"{my_call} band change: now QRV {bands} in {my_grid}",
        }

        msg = templates.get(key, '')
        self.sms_message_var.set(msg)

    def _parse_subscribers(self):
        """Parse subscriber list from text widget. Returns list of (phone, name) tuples."""
        if not hasattr(self, 'sms_subscribers_text'):
            return []
        text = self.sms_subscribers_text.get('1.0', tk.END).strip()
        subscribers = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Handle tab-separated (from Google Sheets paste) or space-separated
            parts = line.replace('\t', ' ').split(None, 1)
            if parts and parts[0].startswith('+'):
                phone = parts[0]
                name = parts[1] if len(parts) > 1 else ''
                subscribers.append((phone, name))
        return subscribers

    def _update_subscriber_count(self):
        """Update the subscriber count label and Send SMS button"""
        subs = self._parse_subscribers()
        count = len(subs)
        if hasattr(self, '_sub_count_label'):
            self._sub_count_label.config(text=f"{count} subscriber{'s' if count != 1 else ''}")
        if hasattr(self, '_sms_broadcast_btn'):
            self._sms_broadcast_btn.config(text=f"Send SMS ({count})")

    def _on_subscribers_modified(self):
        """Handle subscriber text modification"""
        if hasattr(self, 'sms_subscribers_text'):
            self.sms_subscribers_text.edit_modified(False)
        self._update_subscriber_count()

    def _send_rover_sms(self):
        """Send rover status message via SMS to all subscribers"""
        import threading

        message = self.sms_message_var.get().strip()
        if not message:
            return

        subscribers = self._parse_subscribers()
        if not subscribers:
            self._log_sms("SMS: No subscribers configured")
            return

        sid = self.config.get('twilio_account_sid', '').strip()
        token = self.config.get('twilio_auth_token', '').strip()
        from_num = self.config.get('twilio_from_number', '').strip()

        if not all([sid, token, from_num]):
            self._log_sms("SMS error: Twilio credentials not configured")
            return

        def _broadcast():
            import urllib.request
            import urllib.parse
            import base64

            sent = 0
            failed = 0
            url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            auth_str = base64.b64encode(f"{sid}:{token}".encode()).decode()

            for phone, name in subscribers:
                try:
                    post_data = urllib.parse.urlencode({
                        'From': from_num,
                        'To': phone,
                        'Body': message
                    }).encode('utf-8')

                    req = urllib.request.Request(
                        url,
                        data=post_data,
                        headers={
                            'Authorization': f'Basic {auth_str}',
                            'Content-Type': 'application/x-www-form-urlencoded',
                        },
                        method='POST'
                    )

                    with urllib.request.urlopen(req, timeout=15) as response:
                        if response.status == 201:
                            sent += 1
                        else:
                            failed += 1
                except Exception as e:
                    failed += 1
                    display = name or phone
                    print(f"SMS: Error sending to {display}: {e}")

            total = len(subscribers)
            result = f"SMS broadcast: {sent}/{total} sent"
            if failed:
                result += f" ({failed} failed)"
            result += f": {message}"
            self.root.after(0, self._log_sms, result)

        threading.Thread(target=_broadcast, daemon=True).start()
        self._log_sms(f"SMS broadcasting to {len(subscribers)} subscribers...")

    def _send_rover_slack(self):
        """Send the rover status message to Slack"""
        message = self.sms_message_var.get().strip()
        if not message:
            return
        if not self.config.get('slack_enabled', False):
            self._log_sms("Slack disabled - not sent")
            return
        self.post_to_slack(message)
        self._log_sms(f"Slack sent: {message}")

    def _count_nearby_aprs(self):
        """Count APRS stations seen within the last 30 minutes"""
        import time as _time
        cutoff = _time.time() - 1800  # 30 minutes
        return sum(1 for _, _, ts in self.nearby_aprs_stations.values() if ts > cutoff)

    def _send_rover_aprs(self):
        """Send rover status message via APRS to all nearby stations"""
        import time as _time

        message = self.sms_message_var.get().strip()
        if not message:
            return

        if not hasattr(self, 'aprs_client') or not self.aprs_client:
            self._log_sms("APRS not connected - not sent")
            return

        # APRS messages are limited to 67 characters
        if len(message) > 67:
            message = message[:67]

        # Get stations seen in last 30 minutes
        cutoff = _time.time() - 1800
        recipients = []
        expired = []
        for call, (dist, bearing, ts) in self.nearby_aprs_stations.items():
            if ts > cutoff:
                recipients.append((call, dist, bearing))
            else:
                expired.append(call)

        # Clean up expired entries
        for call in expired:
            del self.nearby_aprs_stations[call]

        if not recipients:
            self._log_sms("APRS: No nearby stations in last 30 min")
            return

        # Send to each recipient
        sent = 0
        for call, dist, bearing in recipients:
            try:
                if self.aprs_client.send_message(call, message):
                    sent += 1
            except Exception as e:
                print(f"APRS: Error sending to {call}: {e}")

        self._log_sms(f"APRS sent to {sent}/{len(recipients)} nearby: {message}")

        # Update button count
        count = self._count_nearby_aprs()
        self._aprs_send_btn.config(text=f"Send APRS ({count})")

    def send_sms(self, message, callsign=None):
        """Send an SMS via Twilio REST API (background thread).

        callsign: optional — if provided, enforces a per-station 5-minute cooldown
                  (prevents flooding when a priority station is active on FT8).
        """
        import threading

        if not message or not message.strip():
            return

        if not self.config.get('sms_enabled', False):
            self._log_sms(f"SMS disabled - not sent: {message}")
            return

        sid = self.config.get('twilio_account_sid', '').strip()
        token = self.config.get('twilio_auth_token', '').strip()
        from_num = self.config.get('twilio_from_number', '').strip()
        to_num = self.config.get('twilio_to_number', '').strip()

        if not all([sid, token, from_num, to_num]):
            self._log_sms("SMS error: Twilio credentials not configured")
            return

        import time
        now = time.time()

        # Per-station cooldown: 5 minutes between SMS for the same callsign
        if callsign:
            last = self._sms_station_cooldowns.get(callsign.upper(), 0)
            if now - last < 300:
                self._log_sms(f"SMS cooldown ({callsign}) - skipped: {message}")
                return
            self._sms_station_cooldowns[callsign.upper()] = now

        # Global rate limit: 10 seconds minimum between any SMS
        if now - self._sms_last_send_time < 10:
            self._log_sms(f"SMS rate limited - skipped: {message}")
            return
        self._sms_last_send_time = now

        def _send():
            import urllib.request
            import urllib.parse
            import base64

            try:
                url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
                post_data = urllib.parse.urlencode({
                    'From': from_num,
                    'To': to_num,
                    'Body': message.strip()
                }).encode('utf-8')

                auth_str = base64.b64encode(f"{sid}:{token}".encode()).decode()
                req = urllib.request.Request(
                    url,
                    data=post_data,
                    headers={
                        'Authorization': f'Basic {auth_str}',
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    method='POST'
                )

                with urllib.request.urlopen(req, timeout=15) as response:
                    if response.status == 201:
                        self.root.after(0, self._log_sms, f"SMS sent: {message}")
                        print(f"SMS: Sent to {to_num}")
                    else:
                        self.root.after(0, self._log_sms,
                                        f"SMS error: HTTP {response.status}")
            except Exception as e:
                error_msg = str(e)[:80]
                self.root.after(0, self._log_sms, f"SMS error: {error_msg}")
                print(f"SMS: Error sending: {e}")

        threading.Thread(target=_send, daemon=True).start()

    def _log_sms(self, message):
        """Add a timestamped entry to the SMS message log"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"

        if hasattr(self, 'sms_log_text'):
            self.sms_log_text.config(state=tk.NORMAL)
            self.sms_log_text.insert(tk.END, formatted)
            self.sms_log_text.see(tk.END)
            self.sms_log_text.config(state=tk.DISABLED)

    def _test_sms(self):
        """Send a test SMS using current UI field values"""
        import urllib.request
        import urllib.parse
        import base64

        sid = self.twilio_sid_var.get().strip()
        token = self.twilio_token_var.get().strip()
        from_num = self.twilio_from_var.get().strip()
        to_num = self.twilio_to_var.get().strip()

        if not all([sid, token, from_num, to_num]):
            messagebox.showwarning("SMS Test", "Please fill in all Twilio fields first.")
            return

        my_call = self.config.get('my_call', '') or 'Unknown'
        test_body = f"Test from {my_call} Co-Pilot - SMS integration working!"

        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            post_data = urllib.parse.urlencode({
                'From': from_num,
                'To': to_num,
                'Body': test_body
            }).encode('utf-8')

            auth_str = base64.b64encode(f"{sid}:{token}".encode()).decode()
            req = urllib.request.Request(
                url,
                data=post_data,
                headers={
                    'Authorization': f'Basic {auth_str}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status == 201:
                    self._log_sms(f"Test SMS sent to {to_num}")
                    self.add_alert(f"SMS: Test sent successfully to {to_num}")
                    messagebox.showinfo("SMS Test", "Test SMS sent successfully!")
                else:
                    self._log_sms(f"Test failed: HTTP {response.status}")
                    messagebox.showwarning("SMS Test", f"Twilio returned HTTP {response.status}")

        except Exception as e:
            error_msg = str(e)[:100]
            self._log_sms(f"Test failed: {error_msg}")
            self.add_alert(f"SMS: Test failed - {error_msg}")
            messagebox.showerror("SMS Test", f"Error: {error_msg}")

    def _save_sms_settings(self):
        """Save SMS/Twilio settings from Notify tab to config"""
        self.config['sms_enabled'] = self.sms_enabled_var.get()
        self.config['twilio_account_sid'] = self.twilio_sid_var.get().strip()
        self.config['twilio_auth_token'] = self.twilio_token_var.get().strip()
        self.config['twilio_from_number'] = self.twilio_from_var.get().strip()
        self.config['twilio_to_number'] = self.twilio_to_var.get().strip()
        self.config['sms_on_priority'] = self.sms_priority_var.get()
        self.config['sms_on_dx2'] = self.sms_dx2_var.get()
        self.config['sms_on_dx3'] = self.sms_dx3_var.get()
        self.config['sms_on_new_grid'] = self.sms_new_grid_var.get()
        # Save subscriber list
        if hasattr(self, 'sms_subscribers_text'):
            self.config['sms_subscribers'] = self.sms_subscribers_text.get('1.0', tk.END).strip()
        self.save_config()
        subs = self._parse_subscribers()
        self._log_sms(f"Settings saved ({len(subs)} subscribers)")
        self.add_alert("SMS: Settings saved")

    def create_settings_tab(self, parent):
        """Create settings configuration tab with scrollbar"""
        # Create outer frame
        outer_frame = ttk.Frame(parent)
        
        # Create canvas and scrollbar
        canvas = tk.Canvas(outer_frame)
        scrollbar = ttk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview)
        
        # Create scrollable frame inside canvas
        frame = ttk.Frame(canvas)
        
        # Configure canvas
        frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Pack scrollbar and canvas
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Update scroll region when frame size changes
        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        frame.bind("<Configure>", on_frame_configure)
        
        # Make canvas expand to fill width
        def on_canvas_configure(event):
            canvas.itemconfig(frame_id, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
        
        # Enable mousewheel scrolling only when mouse is over canvas
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", on_mousewheel)
        
        def unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")
        
        canvas.bind("<Enter>", bind_mousewheel)
        canvas.bind("<Leave>", unbind_mousewheel)
        
        # Contest Mode Settings (at top for visibility)
        contest_frame = ttk.LabelFrame(frame, text="Contest Mode", padding=10)
        contest_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Contest mode selector
        ttk.Label(contest_frame, text="Mode:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.contest_mode_var = tk.StringVar(value=self.config.get('contest_mode', 'vhf'))
        mode_combo = ttk.Combobox(contest_frame, textvariable=self.contest_mode_var, 
                                  values=list(CONTEST_MODES.values()), width=30, state='readonly')
        mode_combo.grid(row=0, column=1, columnspan=2, sticky=tk.W, pady=2, padx=5)
        # Set display value from stored key
        mode_key = self.config.get('contest_mode', 'vhf')
        mode_combo.set(CONTEST_MODES.get(mode_key, CONTEST_MODES['vhf']))
        mode_combo.bind('<<ComboboxSelected>>', self._on_contest_mode_change)
        
        # Grid precision display (auto-set by mode)
        ttk.Label(contest_frame, text="Grid Precision:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.grid_precision_label = ttk.Label(contest_frame, text="4-char", font=('TkDefaultFont', 9, 'bold'))
        self.grid_precision_label.grid(row=1, column=1, sticky=tk.W, pady=2, padx=5)
        ttk.Label(contest_frame, text="(auto-set by contest mode)", 
                 foreground="gray").grid(row=1, column=2, sticky=tk.W, padx=5)
        
        # State QSO Party settings (shown/hidden based on mode)
        self.qso_party_frame = ttk.Frame(contest_frame)
        self.qso_party_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(10, 0))
        
        # QSOParty.sec file path
        ttk.Label(self.qso_party_frame, text="N1MM+ QSOParty.sec:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.qsoparty_file_var = tk.StringVar(value=self.config.get('qsoparty_file', get_default_qsoparty_path()))
        qsoparty_entry = ttk.Entry(self.qso_party_frame, textvariable=self.qsoparty_file_var, width=45)
        qsoparty_entry.grid(row=0, column=1, sticky=tk.W, pady=2, padx=5)
        ttk.Button(self.qso_party_frame, text="Browse...", 
                   command=self._browse_qsoparty_file).grid(row=0, column=2, pady=2, padx=2)
        ttk.Button(self.qso_party_frame, text="Reload", 
                   command=self._reload_qsoparty_file).grid(row=0, column=3, pady=2, padx=2)
        
        # QSO Party selector
        ttk.Label(self.qso_party_frame, text="QSO Party:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.qso_party_code_var = tk.StringVar(value=self.config.get('qso_party_code', 'OK'))
        self.qso_party_combo = ttk.Combobox(self.qso_party_frame, textvariable=self.qso_party_code_var,
                                   values=sorted(self.qso_parties.keys()) if self.qso_parties else [],
                                   width=15, state='readonly')
        self.qso_party_combo.grid(row=1, column=1, sticky=tk.W, pady=2, padx=5)
        self.qso_party_combo.bind('<<ComboboxSelected>>', self._on_qsoparty_change)
        
        # County selector
        ttk.Label(self.qso_party_frame, text="County:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.qso_party_county_var = tk.StringVar(value=self.config.get('qso_party_county', ''))
        self.county_combo = ttk.Combobox(self.qso_party_frame, textvariable=self.qso_party_county_var,
                                         values=[], width=15, state='readonly')
        self.county_combo.grid(row=2, column=1, sticky=tk.W, pady=2, padx=5)
        
        ttk.Button(self.qso_party_frame, text="Set County", 
                   command=self._apply_county).grid(row=2, column=2, pady=2, padx=5)
        
        # Current county display (what gets sent to N1MM+)
        ttk.Label(self.qso_party_frame, text="Sent to N1MM+:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.county_display_var = tk.StringVar(value="----")
        ttk.Label(self.qso_party_frame, textvariable=self.county_display_var, 
                 font=('TkDefaultFont', 10, 'bold')).grid(row=3, column=1, sticky=tk.W, pady=2, padx=5)
        
        # Initialize county list for current QSO party
        self._update_county_list()
        
        # Update UI based on current mode
        self._update_contest_mode_ui()
        
        # Station Info
        station_frame = ttk.LabelFrame(frame, text="Station Info", padding=10)
        station_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(station_frame, text="My Callsign:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.my_call_var = tk.StringVar(value=self.config.get('my_call', ''))
        call_entry = ttk.Entry(station_frame, textvariable=self.my_call_var, width=15)
        call_entry.grid(row=0, column=1, sticky=tk.W, pady=2, padx=5)
        # Auto-uppercase
        self.my_call_var.trace_add('write', lambda *args: self.my_call_var.set(self.my_call_var.get().upper()))
        ttk.Label(station_frame, text="Used for Slack notifications, APRS, etc.", 
                 foreground="gray").grid(row=0, column=2, sticky=tk.W, padx=5)
        
        # Data Files
        datafiles_frame = ttk.LabelFrame(frame, text="Data Files", padding=10)
        datafiles_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # SCP master.dta file
        ttk.Label(datafiles_frame, text="Super Check Partial:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.scp_file_var = tk.StringVar(value=self.config.get('scp_file', 'data/master.dta'))
        ttk.Entry(datafiles_frame, textvariable=self.scp_file_var, width=35).grid(row=0, column=1, pady=2, padx=5)
        ttk.Button(datafiles_frame, text="Download", 
                   command=self._download_scp_file).grid(row=0, column=2, pady=2, padx=2)
        ttk.Button(datafiles_frame, text="Browse...", 
                   command=self._browse_scp_file).grid(row=0, column=3, pady=2, padx=2)
        ttk.Button(datafiles_frame, text="Reload", 
                   command=self._reload_scp_file).grid(row=0, column=4, pady=2, padx=2)
        
        scp_count = len(self.scp_calls) if hasattr(self, 'scp_calls') else 0
        self.scp_count_label = ttk.Label(datafiles_frame, text=f"({scp_count} callsigns loaded)", foreground="gray")
        self.scp_count_label.grid(row=0, column=5, sticky=tk.W, padx=5)
        
        # QRZ fallback lookup (when SCP has no matches)
        ttk.Label(datafiles_frame, text="QRZ Lookup:").grid(row=1, column=0, sticky=tk.W, pady=2)
        qrz_cred_frame = ttk.Frame(datafiles_frame)
        qrz_cred_frame.grid(row=1, column=1, columnspan=4, sticky=tk.W, pady=2)
        
        ttk.Label(qrz_cred_frame, text="Username:").pack(side=tk.LEFT)
        self.qrz_username_var = tk.StringVar(value=self.config.get('qrz_username', ''))
        ttk.Entry(qrz_cred_frame, textvariable=self.qrz_username_var, width=12).pack(side=tk.LEFT, padx=2)
        
        ttk.Label(qrz_cred_frame, text="Password:").pack(side=tk.LEFT, padx=(10, 0))
        self.qrz_password_var = tk.StringVar(value=self.config.get('qrz_password', ''))
        ttk.Entry(qrz_cred_frame, textvariable=self.qrz_password_var, width=12, show='*').pack(side=tk.LEFT, padx=2)
        
        ttk.Label(qrz_cred_frame, text="(fallback when SCP has no match)", foreground="gray").pack(side=tk.LEFT, padx=10)
        
        # GPS Settings
        gps_frame = ttk.LabelFrame(frame, text="GPS Settings", padding=10)
        gps_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(gps_frame, text="GPS COM Port:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.gps_port_var = tk.StringVar(value=self.config['gps_port'])
        self.gps_port_combo = ttk.Combobox(gps_frame, textvariable=self.gps_port_var, width=12)
        self.gps_port_combo.grid(row=0, column=1, pady=2, padx=5)
        ttk.Button(gps_frame, text="Connect", command=self._reconnect_gps).grid(row=0, column=2, pady=2, padx=2)
        ttk.Button(gps_frame, text="Refresh Ports", command=self._refresh_com_ports).grid(row=0, column=3, pady=2, padx=2)
        
        # Initial population of COM ports
        self._refresh_com_ports()

        # Baud rate selection
        ttk.Label(gps_frame, text="Baud Rate:").grid(row=1, column=0, sticky=tk.W, pady=2)
        baud_config = self.config.get('gps_baudrate', None)
        self.gps_baudrate_var = tk.StringVar(
            value='Auto' if baud_config is None else str(baud_config))
        baud_values = ['Auto', '4800', '9600', '19200', '38400', '57600', '115200']
        self.gps_baud_combo = ttk.Combobox(gps_frame, textvariable=self.gps_baudrate_var,
                                            values=baud_values, width=12, state='readonly')
        self.gps_baud_combo.grid(row=1, column=1, pady=2, padx=5)
        self.gps_baud_combo.bind('<<ComboboxSelected>>', self._on_gps_baud_changed)

        ttk.Button(gps_frame, text="Auto-Detect",
                   command=self._auto_detect_gps_baud).grid(row=1, column=2, pady=2, padx=2)

        self.gps_baud_status_var = tk.StringVar(value="")
        ttk.Label(gps_frame, textvariable=self.gps_baud_status_var,
                  foreground="gray").grid(row=1, column=3, sticky=tk.W, padx=5)

        # Update rate selection
        ttk.Label(gps_frame, text="Update Rate:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.gps_update_rate_var = tk.StringVar(
            value=str(self.config.get('gps_update_rate_hz', 1)))
        rate_values = ['1', '2', '5', '10']
        self.gps_rate_combo = ttk.Combobox(gps_frame, textvariable=self.gps_update_rate_var,
                                            values=rate_values, width=6, state='readonly')
        self.gps_rate_combo.grid(row=2, column=1, pady=2, padx=5, sticky=tk.W)
        self.gps_rate_combo.bind('<<ComboboxSelected>>', self._on_gps_rate_changed)

        ttk.Label(gps_frame, text="Hz  (requires 19200+ baud for >1Hz)",
                  foreground="gray").grid(row=2, column=2, columnspan=2, sticky=tk.W, padx=5)

        # Enable/disable rate combo based on current baud
        self._update_rate_combo_state()

        # Grid boundary alerts toggle
        self.grid_boundary_var = tk.BooleanVar(value=self.config.get('grid_boundary_alerts', False))
        ttk.Checkbutton(gps_frame, text="Grid Boundary Alerts",
                        variable=self.grid_boundary_var,
                        command=self.toggle_grid_boundary_alerts).grid(row=3, column=0, sticky=tk.W, pady=2)
        ttk.Label(gps_frame, text="Voice alerts at 5mi, 2mi, 1mi, 100yd, 50yd when approaching boundary",
                  foreground="gray").grid(row=3, column=1, columnspan=3, sticky=tk.W, padx=5)
        
        # GPS Time Sync Settings
        timesync_frame = ttk.LabelFrame(frame, text="GPS Time Sync", padding=10)
        timesync_frame.pack(fill=tk.X, padx=5, pady=5)

        # Row 0: Enable + Interval
        self.gps_time_sync_var = tk.BooleanVar(value=self.config.get('gps_time_sync_enabled', False))
        ttk.Checkbutton(timesync_frame, text="Enable GPS Time Sync",
                        variable=self.gps_time_sync_var,
                        command=self._on_time_sync_toggle).grid(row=0, column=0, sticky=tk.W, pady=2)

        ttk.Label(timesync_frame, text="Interval:").grid(row=0, column=1, sticky=tk.E, padx=(20, 5), pady=2)
        _interval_cfg = self.config.get('gps_time_sync_interval_minutes', 5)
        self.gps_sync_interval_var = tk.StringVar(
            value='Manual' if _interval_cfg == 0 else str(_interval_cfg))
        interval_values = ['1', '5', '10', '15', 'Manual']
        self.gps_sync_interval_combo = ttk.Combobox(
            timesync_frame, textvariable=self.gps_sync_interval_var,
            values=interval_values, width=8, state='readonly')
        self.gps_sync_interval_combo.grid(row=0, column=2, pady=2, padx=2, sticky=tk.W)
        self.gps_sync_interval_combo.bind('<<ComboboxSelected>>', self._on_sync_interval_changed)
        ttk.Label(timesync_frame, text="min", foreground="gray").grid(row=0, column=3, sticky=tk.W, pady=2)

        # Row 1: Intermittent mode + WSJT-X grid update
        self.gps_intermittent_var = tk.BooleanVar(value=self.config.get('gps_time_sync_intermittent', False))
        self.gps_intermittent_cb = ttk.Checkbutton(
            timesync_frame, text="Intermittent Mode",
            variable=self.gps_intermittent_var,
            command=self._on_intermittent_toggle)
        self.gps_intermittent_cb.grid(row=1, column=0, sticky=tk.W, pady=2)

        self.gps_intermittent_hint = ttk.Label(
            timesync_frame, text="(close GPS between syncs to reduce RF noise)",
            foreground="gray")
        self.gps_intermittent_hint.grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2)

        self.gps_sync_grid_var = tk.BooleanVar(value=self.config.get('gps_time_sync_update_grid', True))
        ttk.Checkbutton(timesync_frame, text="Update WSJT-X Grid Square after sync",
                        variable=self.gps_sync_grid_var).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=2)

        # Row 3: Sync Now + Status
        ttk.Button(timesync_frame, text="Sync Now",
                   command=self._time_sync_now).grid(row=3, column=0, sticky=tk.W, pady=2)

        self.gps_sync_status_var = tk.StringVar(value="Not synced")
        ttk.Label(timesync_frame, textvariable=self.gps_sync_status_var,
                  foreground="gray").grid(row=3, column=1, columnspan=3, sticky=tk.W, padx=10, pady=2)

        # Row 4: Admin warning (hidden by default)
        self.gps_admin_warning_var = tk.StringVar(value="")
        self.gps_admin_warning_label = ttk.Label(
            timesync_frame, textvariable=self.gps_admin_warning_var,
            foreground="red", wraplength=500)
        self.gps_admin_warning_label.grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=2)

        # Check admin status and update intermittent availability
        self._check_time_sync_admin()
        self._update_intermittent_state()

        # Voice Alerts Settings
        voice_frame = ttk.LabelFrame(frame, text="Voice Alerts", padding=10)
        voice_frame.pack(fill=tk.X, padx=5, pady=5)

        # Master switch
        self.voice_enabled_var = tk.BooleanVar(value=self.config.get('voice_enabled', True))
        ttk.Checkbutton(voice_frame, text="Enable Voice Alerts (Master)",
                        variable=self.voice_enabled_var,
                        command=self._on_voice_master_toggle).grid(
            row=0, column=0, sticky=tk.W, pady=2)
        ttk.Label(voice_frame, text="Master switch for all text-to-speech announcements",
                  foreground="gray").grid(row=0, column=1, sticky=tk.W, padx=10)

        # Individual category toggles
        ttk.Label(voice_frame, text="Individual Categories:",
                  font=('TkDefaultFont', 9, 'bold')).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(8, 2))

        VOICE_CATEGORIES = [
            ("qso_logged",    "QSO Logged",            '"QSO logged. W1AW"'),
            ("grid_change",   "Grid Change",            "Grid enter/initial announcements"),
            ("county_change", "County Change",          "QSO Party county changes"),
            ("grid_boundary", "Grid Boundary",          "Proximity alerts (5mi, 2mi, 1mi...)"),
            ("new_grid",      "New Grid",               "New grid decoded in WSJT-X"),
            ("calling_me",    "Calling Me",             "Station calling your callsign"),
            ("priority_dx",   "Priority DX Station",    "DX!/AP! priority station alerts"),
            ("psk_alert",     "PSK Band Activity",      "PSK Reporter band opening alerts"),
            ("aprs_message",  "APRS Messages",          "Incoming APRS messages"),
            ("aprs_nearby",   "APRS Nearby Station",    "Nearby station position alerts"),
            ("gps_waypoint",  "GPS Waypoint",           "Waypoint added confirmations"),
            ("warnings",      "Warnings",               "GPS lock, battery, WSJT-X watchdog"),
            ("operational",   "Operational Status",     "Log reload, logger updates, baud changes"),
        ]

        disabled_cats = set(self.config.get('voice_disabled_categories', []))
        self.voice_category_vars = {}
        self.voice_category_cbs = {}

        for i, (key, label, hint) in enumerate(VOICE_CATEGORIES):
            var = tk.BooleanVar(value=(key not in disabled_cats))
            self.voice_category_vars[key] = var
            cb = ttk.Checkbutton(voice_frame, text=label, variable=var,
                                 command=self._on_voice_category_change)
            cb.grid(row=i + 2, column=0, sticky=tk.W, padx=(20, 0), pady=1)
            self.voice_category_cbs[key] = cb
            ttk.Label(voice_frame, text=hint, foreground="gray").grid(
                row=i + 2, column=1, sticky=tk.W, padx=10, pady=1)

        ttk.Label(voice_frame,
                  text="Test Voice button always works regardless of these settings",
                  foreground="gray", font=('TkDefaultFont', 8)).grid(
            row=len(VOICE_CATEGORIES) + 2, column=0, columnspan=2,
            sticky=tk.W, pady=(5, 0), padx=5)

        # Grey out categories if master is off
        self._update_voice_category_states()

        # Victron Settings
        victron_frame = ttk.LabelFrame(frame, text="Victron SmartShunt", padding=10)
        victron_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(victron_frame, text="BLE Address:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.victron_addr_var = tk.StringVar(value=self.config['victron_address'])
        ttk.Entry(victron_frame, textvariable=self.victron_addr_var, width=40).grid(row=0, column=1, pady=2)
        
        ttk.Label(victron_frame, text="Encryption Key:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.victron_key_var = tk.StringVar(value=self.config['victron_key'])
        ttk.Entry(victron_frame, textvariable=self.victron_key_var, width=40).grid(row=1, column=1, pady=2)
        
        ttk.Button(victron_frame, text="Discover Devices", 
                   command=self.discover_victron).grid(row=2, column=1, pady=5, sticky=tk.E)
        
        # Logger Settings (N1MM+, N3FJP, or Log4OM v2)
        logger_frame = ttk.LabelFrame(frame, text="Logger", padding=10)
        logger_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Logger selection dropdown
        ttk.Label(logger_frame, text="Logger:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.logger_var = tk.StringVar(value=self.config.get('contest_logger', 'n1mm'))
        logger_combo = ttk.Combobox(logger_frame, textvariable=self.logger_var,
                                    values=['n1mm', 'n3fjp', 'log4om'], width=10, state='readonly')
        logger_combo.grid(row=0, column=1, sticky=tk.W, pady=2, padx=5)
        logger_combo.bind('<<ComboboxSelected>>', self._on_logger_change)
        
        # N1MM+ settings frame
        self.n1mm_settings_frame = ttk.Frame(logger_frame)
        self.n1mm_settings_frame.grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=(5,0))
        
        ttk.Label(self.n1mm_settings_frame, text="N1MM+ TCP Port:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.n1mm_port_var = tk.StringVar(value=str(self.config.get('n1mm_udp_port', 52001)))
        ttk.Entry(self.n1mm_settings_frame, textvariable=self.n1mm_port_var, width=10).grid(row=0, column=1, pady=2, padx=5)
        ttk.Label(self.n1mm_settings_frame, text="(Config → Configure Ports → WSJT/JTDX Setup)", 
                 foreground="gray").grid(row=0, column=2, sticky=tk.W, pady=2)
        
        # N3FJP settings frame
        self.n3fjp_settings_frame = ttk.Frame(logger_frame)
        self.n3fjp_settings_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5,0))
        
        ttk.Label(self.n3fjp_settings_frame, text="N3FJP API Port:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.n3fjp_port_var = tk.StringVar(value=str(self.config.get('n3fjp_port', 1100)))
        ttk.Entry(self.n3fjp_settings_frame, textvariable=self.n3fjp_port_var, width=10).grid(row=0, column=1, pady=2, padx=5)
        ttk.Label(self.n3fjp_settings_frame, text="(Settings → Application Program Interface)",
                 foreground="gray").grid(row=0, column=2, sticky=tk.W, pady=2)

        # Log4OM v2 settings frame
        self.log4om_settings_frame = ttk.Frame(logger_frame)
        self.log4om_settings_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(5,0))

        ttk.Label(self.log4om_settings_frame, text="Log4OM IP:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.log4om_host_var = tk.StringVar(value=self.config.get('log4om_host', '127.0.0.1'))
        ttk.Entry(self.log4om_settings_frame, textvariable=self.log4om_host_var, width=15).grid(row=0, column=1, pady=2, padx=5)

        ttk.Label(self.log4om_settings_frame, text="UDP Port:").grid(row=0, column=2, sticky=tk.W, pady=2, padx=(10,0))
        self.log4om_port_var = tk.StringVar(value=str(self.config.get('log4om_port', 2333)))
        ttk.Entry(self.log4om_settings_frame, textvariable=self.log4om_port_var, width=8).grid(row=0, column=3, pady=2, padx=5)

        ttk.Label(self.log4om_settings_frame, text="(Log4OM v2 → Settings → UDP)",
                 foreground="gray").grid(row=0, column=4, sticky=tk.W, pady=2)

        # Show/hide appropriate settings
        self._update_logger_ui()
        
        # My Bands (what bands you have equipment for)
        bands_frame = ttk.LabelFrame(frame, text="My Bands (equipment you have)", padding=10)
        bands_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(bands_frame, text="Check the bands you have equipment for. Used in Manual Entry and Grid Corner tabs.",
                 foreground="gray").grid(row=0, column=0, columnspan=8, sticky=tk.W, pady=(0,5))
        
        # All possible bands
        self.all_bands = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m',  # HF
                          '6m', '2m', '1.25m', '70cm', '33cm', '23cm',  # VHF/UHF
                          '13cm', '9cm', '5cm', '3cm', '1.2cm', '6mm', '4mm', '2mm', '1mm']  # Microwave
        
        # Load saved bands or default to common VHF+ bands
        default_bands = ['6m', '2m', '1.25m', '70cm', '33cm', '23cm']
        saved_bands = self.config.get('my_bands', default_bands)
        
        self.band_check_vars = {}
        row = 1
        col = 0
        for band in self.all_bands:
            var = tk.BooleanVar(value=band in saved_bands)
            self.band_check_vars[band] = var
            ttk.Checkbutton(bands_frame, text=band, variable=var).grid(row=row, column=col, sticky=tk.W, padx=5)
            col += 1
            if col >= 8:  # 8 columns
                col = 0
                row += 1
        
        # WSJT-X/Radio Instances
        wsjt_frame = ttk.LabelFrame(frame, text="WSJT-X / Radio Instances", padding=10)
        wsjt_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(wsjt_frame, text="Configure up to 4 WSJT-X instances. Set custom radio names for your setup.",
                 foreground="gray").grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0,5))
        
        ttk.Label(wsjt_frame, text="Radio Name", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Label(wsjt_frame, text="WSJT-X Log Path", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=1, sticky=tk.W, padx=5)
        ttk.Label(wsjt_frame, text="UDP Port", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=2, sticky=tk.W, padx=5)
        
        # Get existing instances or create defaults
        existing_instances = self.config.get('wsjt_instances', [])
        # Ensure we have 4 slots
        while len(existing_instances) < 4:
            existing_instances.append({'name': '', 'log_path': '', 'udp_port': 2237 + len(existing_instances)})
        
        self.wsjt_name_vars = []
        self.wsjt_path_vars = []
        self.wsjt_port_vars = []
        
        for i in range(4):
            instance = existing_instances[i] if i < len(existing_instances) else {'name': '', 'log_path': '', 'udp_port': 2237 + i}
            
            # Radio name
            name_var = tk.StringVar(value=instance.get('name', ''))
            self.wsjt_name_vars.append(name_var)
            ttk.Entry(wsjt_frame, textvariable=name_var, width=20).grid(row=i+2, column=0, pady=2, padx=5, sticky=tk.W)
            
            # Log path
            path_var = tk.StringVar(value=instance.get('log_path', ''))
            self.wsjt_path_vars.append(path_var)
            ttk.Entry(wsjt_frame, textvariable=path_var, width=45).grid(row=i+2, column=1, pady=2, padx=5)
            
            # Browse button
            ttk.Button(wsjt_frame, text="Browse...", 
                       command=lambda idx=i: self.browse_log_path(idx)).grid(row=i+2, column=2, pady=2, padx=2)
            
            # UDP port
            port_var = tk.StringVar(value=str(instance.get('udp_port', 2237 + i)))
            self.wsjt_port_vars.append(port_var)
            ttk.Entry(wsjt_frame, textvariable=port_var, width=6).grid(row=i+2, column=3, pady=2, padx=5)
        
        ttk.Label(wsjt_frame, text="Leave unused rows blank. UDP Port must match WSJT-X → Settings → Reporting → UDP Server port.",
                 foreground="gray").grid(row=6, column=0, columnspan=4, sticky=tk.W, pady=(5,0))
        
        # APRS-IS Settings
        aprs_frame = ttk.LabelFrame(frame, text="APRS-IS (Internet) - Beaconing & Nearby Alerts", padding=10)
        aprs_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Enable checkbox (uses same variable as top status bar)
        ttk.Checkbutton(aprs_frame, text="Enable APRS-IS", 
                       variable=self.aprs_enabled_var,
                       command=self.toggle_aprs).grid(row=0, column=0, sticky=tk.W, pady=2)
        
        # Callsign
        ttk.Label(aprs_frame, text="Callsign-SSID:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.aprs_call_var = tk.StringVar(value=self.config.get('aprs_callsign', 'N5ZY-9'))
        ttk.Entry(aprs_frame, textvariable=self.aprs_call_var, width=15).grid(row=1, column=1, sticky=tk.W, pady=2)
        ttk.Label(aprs_frame, text="(-9 = mobile, -7 = HT)", 
                 foreground="gray").grid(row=1, column=2, sticky=tk.W, padx=5)
        
        # Beacon interval
        ttk.Label(aprs_frame, text="Beacon Interval:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.aprs_beacon_var = tk.StringVar(value=str(self.config.get('aprs_beacon_interval', 600)))
        ttk.Entry(aprs_frame, textvariable=self.aprs_beacon_var, width=10).grid(row=2, column=1, sticky=tk.W, pady=2)
        ttk.Label(aprs_frame, text="seconds (600 = 10 min)", 
                 foreground="gray").grid(row=2, column=2, sticky=tk.W, padx=5)
        
        # Alert radius
        ttk.Label(aprs_frame, text="Alert Radius:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.aprs_radius_var = tk.StringVar(value=str(self.config.get('aprs_alert_radius', 10)))
        ttk.Entry(aprs_frame, textvariable=self.aprs_radius_var, width=10).grid(row=3, column=1, sticky=tk.W, pady=2)
        ttk.Label(aprs_frame, text="miles (alerts for mobile stations only)", 
                 foreground="gray").grid(row=3, column=2, sticky=tk.W, padx=5)
        
        # Beacon comment
        ttk.Label(aprs_frame, text="Beacon Comment:").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.aprs_comment_var = tk.StringVar(value=self.config.get('aprs_comment', 'N5ZY.ORG Rover!'))
        ttk.Entry(aprs_frame, textvariable=self.aprs_comment_var, width=30).grid(row=4, column=1, columnspan=2, sticky=tk.W, pady=2)
        
        ttk.Label(aprs_frame, text="Uses APRS-IS (internet via Starlink) - no RF conflict with 2m contesting",
                 foreground="gray").grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=2)
        
        # PSK Reporter Settings
        psk_frame = ttk.LabelFrame(frame, text="PSK Reporter Monitor - Band Activity & Propagation Alerts", padding=10)
        psk_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Enable checkbox (uses shared variable from __init__)
        ttk.Checkbutton(psk_frame, text="Enable PSK Reporter Monitoring", 
                       variable=self.psk_enabled_var,
                       command=self._toggle_psk_monitor).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=2)
        
        # VHF Spot Radius
        ttk.Label(psk_frame, text="VHF Spot Radius:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.psk_vhf_radius_var = tk.StringVar(value=str(self.config.get('psk_vhf_radius', 250)))
        ttk.Entry(psk_frame, textvariable=self.psk_vhf_radius_var, width=8).grid(row=1, column=1, sticky=tk.W, pady=2, padx=5)
        ttk.Label(psk_frame, text="miles (for 6m and up)", 
                 foreground="gray").grid(row=1, column=2, sticky=tk.W, padx=5)
        
        # HF Spot Radius
        ttk.Label(psk_frame, text="HF Spot Radius:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.psk_hf_radius_var = tk.StringVar(value=str(self.config.get('psk_hf_radius', 100)))
        ttk.Entry(psk_frame, textvariable=self.psk_hf_radius_var, width=8).grid(row=2, column=1, sticky=tk.W, pady=2, padx=5)
        ttk.Label(psk_frame, text="miles (for QSO Party HF bands)", 
                 foreground="gray").grid(row=2, column=2, sticky=tk.W, padx=5)
        
        # Baseline period
        ttk.Label(psk_frame, text="Baseline Period:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.psk_baseline_var = tk.StringVar(value=str(self.config.get('psk_baseline_minutes', 15)))
        ttk.Entry(psk_frame, textvariable=self.psk_baseline_var, width=8).grid(row=3, column=1, sticky=tk.W, pady=2, padx=5)
        ttk.Label(psk_frame, text="minutes (for band opening detection)", 
                 foreground="gray").grid(row=3, column=2, sticky=tk.W, padx=5)
        
        # Alert checkboxes
        alert_frame = ttk.Frame(psk_frame)
        alert_frame.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(10,2))
        
        self.psk_alert_openings_var = tk.BooleanVar(value=self.config.get('psk_alert_openings', True))
        ttk.Checkbutton(alert_frame, text="Band openings", 
                       variable=self.psk_alert_openings_var).pack(side=tk.LEFT, padx=(0,15))
        
        self.psk_alert_mspe_var = tk.BooleanVar(value=self.config.get('psk_alert_mspe', True))
        ttk.Checkbutton(alert_frame, text="MSp-E (PULL OVER!)", 
                       variable=self.psk_alert_mspe_var).pack(side=tk.LEFT, padx=(0,15))
        
        self.psk_alert_spe_var = tk.BooleanVar(value=self.config.get('psk_alert_spe', True))
        ttk.Checkbutton(alert_frame, text="Sp-E (2m/70cm)", 
                       variable=self.psk_alert_spe_var).pack(side=tk.LEFT, padx=(0,15))
        
        alert_frame2 = ttk.Frame(psk_frame)
        alert_frame2.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=2)
        
        self.psk_alert_modes_var = tk.BooleanVar(value=self.config.get('psk_alert_modes', True))
        ttk.Checkbutton(alert_frame2, text="Unusual modes (Q65, MSK144, FT4)", 
                       variable=self.psk_alert_modes_var).pack(side=tk.LEFT, padx=(0,15))
        
        self.psk_crossref_var = tk.BooleanVar(value=self.config.get('psk_crossref_qsy', True))
        ttk.Checkbutton(alert_frame2, text="Cross-ref QSY Advisor", 
                       variable=self.psk_crossref_var).pack(side=tk.LEFT, padx=(0,15))
        
        ttk.Label(psk_frame, text="Polls every 5 min (PSK Reporter rate limit). Priority: 🔴MSp-E 🟠Sp-E/70cm+ 🟡Sp-E/2m 🔵Opening",
                 foreground="gray").grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=2)

        # ── Priority Station Alerts ──
        ttk.Separator(psk_frame, orient=tk.HORIZONTAL).grid(row=7, column=0, columnspan=3, sticky=tk.EW, pady=(10,5))

        ttk.Label(psk_frame, text="Priority Station Alerts",
                 font=('TkDefaultFont', 9, 'bold')).grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=2)

        self.psk_priority_enabled_var = tk.BooleanVar(value=self.config.get('psk_priority_enabled', False))
        ttk.Checkbutton(psk_frame, text="Enable Priority Station Alerts",
                       variable=self.psk_priority_enabled_var).grid(row=9, column=0, sticky=tk.W, pady=2)

        # DX! stations (Daily DX mode)
        ttk.Label(psk_frame, text="DX! Stations:").grid(row=10, column=0, sticky=tk.W, pady=2)
        # Migrate old key
        dx_default = self.config.get('dx_priority_stations', '') or self.config.get('psk_priority_stations', '')
        self.dx_priority_stations_var = tk.StringVar(value=dx_default)
        ttk.Entry(psk_frame, textvariable=self.dx_priority_stations_var, width=50).grid(
            row=10, column=1, columnspan=2, sticky=tk.W, pady=2, padx=5)
        ttk.Label(psk_frame, text="DX expedition callsigns — Daily DX mode only (e.g., 3Y0K,J51A,T8OK)",
                 foreground="gray").grid(row=11, column=0, columnspan=3, sticky=tk.W, padx=(20,0))

        # AP! stations (VHF/QSO Party modes)
        ttk.Label(psk_frame, text="AP! Stations:").grid(row=12, column=0, sticky=tk.W, pady=2)
        self.ap_priority_stations_var = tk.StringVar(value=self.config.get('ap_priority_stations', ''))
        ttk.Entry(psk_frame, textvariable=self.ap_priority_stations_var, width=50).grid(
            row=12, column=1, columnspan=2, sticky=tk.W, pady=2, padx=5)
        ttk.Label(psk_frame, text="Family/friends/club — VHF Contest & QSO Party modes only",
                 foreground="gray").grid(row=13, column=0, columnspan=3, sticky=tk.W, padx=(20,0))

        # ── DXCC Alerts ──
        ttk.Separator(psk_frame, orient=tk.HORIZONTAL).grid(row=14, column=0, columnspan=3, sticky=tk.EW, pady=(10,5))
        ttk.Label(psk_frame, text="DXCC Alerts (requires LoTW + cty.dat)",
                 font=('TkDefaultFont', 9, 'bold')).grid(row=15, column=0, columnspan=3, sticky=tk.W, pady=2)

        dxcc_frame = ttk.Frame(psk_frame)
        dxcc_frame.grid(row=16, column=0, columnspan=3, sticky=tk.W, pady=2)

        self.dx2_enabled_var = tk.BooleanVar(value=self.config.get('dx2_enabled', False))
        ttk.Checkbutton(dxcc_frame, text="DX2 — Alert on new DXCC entity",
                       variable=self.dx2_enabled_var).pack(side=tk.LEFT, padx=(0, 20))

        self.dx3_enabled_var = tk.BooleanVar(value=self.config.get('dx3_enabled', False))
        ttk.Checkbutton(dxcc_frame, text="DX3 — Alert on new:",
                       variable=self.dx3_enabled_var).pack(side=tk.LEFT)

        self.dx3_granularity_var = tk.StringVar(value=self.config.get('dx3_granularity', 'band'))
        dx3_combo = ttk.Combobox(dxcc_frame, textvariable=self.dx3_granularity_var,
                                  values=['band', 'mode', 'band_mode'], width=12, state='readonly')
        dx3_combo.pack(side=tk.LEFT, padx=5)

        # ── LoTW Integration ──
        ttk.Separator(psk_frame, orient=tk.HORIZONTAL).grid(row=17, column=0, columnspan=3, sticky=tk.EW, pady=(10,5))
        ttk.Label(psk_frame, text="LoTW Integration",
                 font=('TkDefaultFont', 9, 'bold')).grid(row=18, column=0, columnspan=3, sticky=tk.W, pady=2)

        lotw_cred_frame = ttk.Frame(psk_frame)
        lotw_cred_frame.grid(row=19, column=0, columnspan=3, sticky=tk.W, pady=2)

        ttk.Label(lotw_cred_frame, text="Username:").pack(side=tk.LEFT)
        self.lotw_username_var = tk.StringVar(value=self.config.get('lotw_username', ''))
        ttk.Entry(lotw_cred_frame, textvariable=self.lotw_username_var, width=15).pack(side=tk.LEFT, padx=(5, 15))

        ttk.Label(lotw_cred_frame, text="Password:").pack(side=tk.LEFT)
        self.lotw_password_var = tk.StringVar(value=self.config.get('lotw_password', ''))
        ttk.Entry(lotw_cred_frame, textvariable=self.lotw_password_var, width=15, show='*').pack(side=tk.LEFT, padx=5)

        lotw_btn_frame = ttk.Frame(psk_frame)
        lotw_btn_frame.grid(row=20, column=0, columnspan=3, sticky=tk.W, pady=2)

        ttk.Button(lotw_btn_frame, text="Refresh Now", command=self._lotw_refresh).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(lotw_btn_frame, text="Load from File...", command=self._lotw_load_file).pack(side=tk.LEFT, padx=(0, 15))

        self.lotw_auto_refresh_var = tk.BooleanVar(value=self.config.get('lotw_auto_refresh', True))
        ttk.Checkbutton(lotw_btn_frame, text="Auto-refresh weekly",
                       variable=self.lotw_auto_refresh_var).pack(side=tk.LEFT)

        self.lotw_status_var = tk.StringVar(value=self._get_lotw_status_text())
        ttk.Label(psk_frame, textvariable=self.lotw_status_var, foreground="gray").grid(
            row=21, column=0, columnspan=3, sticky=tk.W, pady=2)

        # ── cty.dat ──
        ttk.Separator(psk_frame, orient=tk.HORIZONTAL).grid(row=22, column=0, columnspan=3, sticky=tk.EW, pady=(10,5))
        ttk.Label(psk_frame, text="cty.dat (Country File)",
                 font=('TkDefaultFont', 9, 'bold')).grid(row=23, column=0, columnspan=3, sticky=tk.W, pady=2)

        cty_frame = ttk.Frame(psk_frame)
        cty_frame.grid(row=24, column=0, columnspan=3, sticky=tk.W, pady=2)

        self.cty_status_var = tk.StringVar(value=self._get_cty_status_text())
        ttk.Label(cty_frame, textvariable=self.cty_status_var).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Button(cty_frame, text="Update from AD1C", command=self._cty_update).pack(side=tk.LEFT)

        # Slack Webhook Settings
        slack_frame = ttk.LabelFrame(frame, text="Slack Notifications - Grid Activation Alerts", padding=10)
        slack_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(slack_frame, text="Post grid changes to Slack channels. Get webhook URLs from Slack App settings.",
                 foreground="gray").grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0,5))
        
        # Enable checkbox
        self.slack_enabled_var = tk.BooleanVar(value=self.config.get('slack_enabled', False))
        ttk.Checkbutton(slack_frame, text="Enable Slack Notifications", 
                       variable=self.slack_enabled_var).grid(row=1, column=0, sticky=tk.W, pady=2)
        
        # Webhook entries (up to 3)
        ttk.Label(slack_frame, text="Webhook URLs:", font=('TkDefaultFont', 9, 'bold')).grid(row=2, column=0, sticky=tk.W, pady=(10,2))
        
        self.slack_webhook_vars = []
        self.slack_name_vars = []
        saved_webhooks = self.config.get('slack_webhooks', [])
        
        for i in range(3):
            # Name field
            name_var = tk.StringVar(value=saved_webhooks[i].get('name', '') if i < len(saved_webhooks) else '')
            self.slack_name_vars.append(name_var)
            ttk.Label(slack_frame, text=f"#{i+1} Name:").grid(row=3+i, column=0, sticky=tk.W, pady=2)
            ttk.Entry(slack_frame, textvariable=name_var, width=15).grid(row=3+i, column=1, sticky=tk.W, pady=2, padx=5)
            
            # Webhook URL field
            webhook_var = tk.StringVar(value=saved_webhooks[i].get('url', '') if i < len(saved_webhooks) else '')
            self.slack_webhook_vars.append(webhook_var)
            ttk.Entry(slack_frame, textvariable=webhook_var, width=60).grid(row=3+i, column=2, sticky=tk.W, pady=2, padx=5)
        
        # Test button
        ttk.Button(slack_frame, text="Test Webhooks", 
                   command=self._test_slack_webhooks).grid(row=6, column=0, pady=10, sticky=tk.W)
        
        # What gets posted
        ttk.Label(slack_frame, text="Posts: Grid changes, session start/end. Format: \"N5ZY/R now in EM15 on 6m, 2m, 70cm\"",
                 foreground="gray").grid(row=6, column=1, columnspan=2, sticky=tk.W, pady=2)
        
        # Save button
        ttk.Button(frame, text="Save Settings", command=self.save_settings).pack(pady=10)
        
        return outer_frame
    
    def create_manual_entry_tab(self, parent):
        """Create manual QSO entry tab for phone/CW contacts"""
        frame = ttk.Frame(parent)
        
        # Create left/right paned layout
        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left side: Entry form
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=3)
        
        entry_frame = ttk.LabelFrame(left_frame, text="Manual QSO Entry (Phone/CW)", padding=10)
        entry_frame.pack(fill=tk.BOTH, expand=True)
        
        # Row 0: Band selection (includes HF for QSO parties)
        ttk.Label(entry_frame, text="Band:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.manual_band_var = tk.StringVar()
        self.manual_band_combo = ttk.Combobox(entry_frame, textvariable=self.manual_band_var, 
                                   values=ALL_BANDS, width=10, state='readonly')
        self.manual_band_combo.grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        self.manual_band_combo.bind('<<ComboboxSelected>>', self._on_band_select)
        
        # Row 1: Mode selection
        ttk.Label(entry_frame, text="Mode:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.manual_mode_var = tk.StringVar(value='USB')
        mode_frame = ttk.Frame(entry_frame)
        mode_frame.grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Radiobutton(mode_frame, text="USB", variable=self.manual_mode_var, 
                       value='USB', command=self._on_mode_change).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="LSB", variable=self.manual_mode_var, 
                       value='LSB', command=self._on_mode_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(mode_frame, text="FM", variable=self.manual_mode_var, 
                       value='FM', command=self._on_mode_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(mode_frame, text="CW", variable=self.manual_mode_var, 
                       value='CW', command=self._on_mode_change).pack(side=tk.LEFT)
        
        # Row 2: Frequency (MHz)
        ttk.Label(entry_frame, text="Frequency:").grid(row=2, column=0, sticky=tk.W, pady=5)
        freq_frame = ttk.Frame(entry_frame)
        freq_frame.grid(row=2, column=1, sticky=tk.W, pady=5, padx=5)
        self.manual_freq_var = tk.StringVar()
        ttk.Entry(freq_frame, textvariable=self.manual_freq_var, width=12).pack(side=tk.LEFT)
        ttk.Label(freq_frame, text="MHz").pack(side=tk.LEFT, padx=5)
        
        # Row 3: Callsign
        ttk.Label(entry_frame, text="Callsign:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.manual_call_var = tk.StringVar()
        call_entry = ttk.Entry(entry_frame, textvariable=self.manual_call_var, width=15)
        call_entry.grid(row=3, column=1, sticky=tk.W, pady=5, padx=5)
        # Auto-uppercase and trigger SCP lookup
        self.manual_call_var.trace_add('write', self._on_callsign_change)
        
        # Row 4: Their Grid / Their Exchange (label changes based on mode)
        self.their_grid_label = ttk.Label(entry_frame, text="Their Grid:")
        self.their_grid_label.grid(row=4, column=0, sticky=tk.W, pady=5)
        self.manual_grid_var = tk.StringVar()
        grid_entry = ttk.Entry(entry_frame, textvariable=self.manual_grid_var, width=10)
        grid_entry.grid(row=4, column=1, sticky=tk.W, pady=5, padx=5)
        # Auto-uppercase
        self.manual_grid_var.trace_add('write', lambda *args: self.manual_grid_var.set(self.manual_grid_var.get().upper()))
        
        # Row 5: Signal Reports
        ttk.Label(entry_frame, text="RST Sent:").grid(row=5, column=0, sticky=tk.W, pady=5)
        rst_frame = ttk.Frame(entry_frame)
        rst_frame.grid(row=5, column=1, sticky=tk.W, pady=5, padx=5)
        self.manual_rst_sent_var = tk.StringVar(value='59')
        ttk.Entry(rst_frame, textvariable=self.manual_rst_sent_var, width=5).pack(side=tk.LEFT)
        ttk.Label(rst_frame, text="  RST Rcvd:").pack(side=tk.LEFT)
        self.manual_rst_rcvd_var = tk.StringVar(value='59')
        ttk.Entry(rst_frame, textvariable=self.manual_rst_rcvd_var, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(rst_frame, text="(59=phone, 599=CW)", foreground='gray').pack(side=tk.LEFT)
        
        # Row 6: My Grid / My County (label changes based on mode)
        self.my_grid_label = ttk.Label(entry_frame, text="My Grid:")
        self.my_grid_label.grid(row=6, column=0, sticky=tk.W, pady=5)
        self.manual_mygrid_var = tk.StringVar(value=self.current_grid)
        mygrid_entry = ttk.Entry(entry_frame, textvariable=self.manual_mygrid_var, width=10)
        mygrid_entry.grid(row=6, column=1, sticky=tk.W, pady=5, padx=5)
        self.my_grid_hint_label = ttk.Label(entry_frame, text="(auto-filled from GPS)", foreground='gray')
        self.my_grid_hint_label.grid(row=6, column=2, sticky=tk.W)
        
        # Row 7: Log button
        button_frame = ttk.Frame(entry_frame)
        button_frame.grid(row=7, column=0, columnspan=3, pady=15)
        
        # Button text will be updated by _update_manual_entry_labels()
        self.log_qso_button = ttk.Button(button_frame, text="Log QSO", 
                   command=self.log_manual_qso)
        self.log_qso_button.pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Clear Form", 
                   command=self._clear_manual_form).pack(side=tk.LEFT, padx=5)
        
        # Help text
        ttk.Label(entry_frame, text="QSO is sent to N1MM+ and saved to ADIF backup.",
                 foreground="gray").grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=5)
        
        # Right side: Super Check Partial
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)
        
        scp_frame = ttk.LabelFrame(right_frame, text="Super Check Partial", padding=5)
        scp_frame.pack(fill=tk.BOTH, expand=True)
        
        # SCP count label
        self.scp_count_var = tk.StringVar(value=f"Loaded: {len(self.scp_calls)} calls")
        ttk.Label(scp_frame, textvariable=self.scp_count_var, foreground='gray').pack(anchor=tk.W)
        
        # SCP matches listbox with scrollbar
        scp_list_frame = ttk.Frame(scp_frame)
        scp_list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        scp_scrollbar = ttk.Scrollbar(scp_list_frame)
        scp_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.scp_listbox = tk.Listbox(scp_list_frame, yscrollcommand=scp_scrollbar.set, 
                                       font=('Consolas', 10), height=12)
        self.scp_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scp_scrollbar.config(command=self.scp_listbox.yview)
        
        # Click to select a call
        self.scp_listbox.bind('<Double-1>', self._on_scp_select)
        self.scp_listbox.bind('<Return>', self._on_scp_select)
        
        # Match count
        self.scp_match_var = tk.StringVar(value="Type callsign to search...")
        ttk.Label(scp_frame, textvariable=self.scp_match_var, foreground='gray').pack(anchor=tk.W)
        
        # QRZ Lookup button (fallback when not in SCP)
        lookup_frame = ttk.Frame(scp_frame)
        lookup_frame.pack(fill=tk.X, pady=5)
        ttk.Button(lookup_frame, text="Lookup on QRZ", 
                   command=self._lookup_callsign_qrz).pack(side=tk.LEFT, padx=2)
        ttk.Button(lookup_frame, text="Callook (US)", 
                   command=self._lookup_callsign_callook).pack(side=tk.LEFT, padx=2)
        
        # Set default band and populate frequency
        self.manual_band_var.set('2m')
        self._on_band_select()  # Populate frequency for default band/mode
        
        return frame
    
    def _on_callsign_change(self, *args):
        """Handle callsign entry changes - uppercase and SCP lookup"""
        # Prevent recursion when we set uppercase
        if hasattr(self, '_updating_call') and self._updating_call:
            return
        
        # Get current value
        current = self.manual_call_var.get()
        upper = current.upper()
        
        # Set to uppercase if needed
        if current != upper:
            self._updating_call = True
            self.manual_call_var.set(upper)
            self._updating_call = False
        
        # Always do SCP lookup
        self._update_scp_matches(upper)
    
    def _update_scp_matches(self, partial):
        """Update SCP listbox with matching callsigns"""
        self.scp_listbox.delete(0, tk.END)
        
        if not partial or len(partial) < 2:
            self.scp_match_var.set("Type 2+ chars to search...")
            return
        
        # Find matches in worked_calls first (highest priority)
        worked_prefix = []
        worked_contains = []
        for call in self.worked_calls:
            if call.startswith(partial):
                worked_prefix.append(call)
            elif partial in call:
                worked_contains.append(call)
        
        # Find matches in SCP database
        scp_prefix = []
        scp_contains = []
        for call in self.scp_calls:
            # Skip if already in worked_calls (avoid duplicates)
            if call in self.worked_calls:
                continue
            if call.startswith(partial):
                scp_prefix.append(call)
            elif partial in call:
                scp_contains.append(call)
        
        # Build display list: worked calls first (with suffix), then SCP matches
        worked_matches = worked_prefix[:25] + worked_contains[:25]
        scp_matches = scp_prefix[:50] + scp_contains[:50]
        
        # Insert worked calls with "(Worked)" suffix
        for call in worked_matches[:50]:
            self.scp_listbox.insert(tk.END, f"{call} (Worked)")
        
        # Insert SCP matches
        for call in scp_matches[:50]:
            self.scp_listbox.insert(tk.END, call)
        
        # Update count
        total_worked = len(worked_prefix) + len(worked_contains)
        total_scp = len(scp_prefix) + len(scp_contains)
        total = total_worked + total_scp
        
        if total > 100:
            self.scp_match_var.set(f"{total} matches ({total_worked} worked)")
        elif total > 0:
            if total_worked > 0:
                self.scp_match_var.set(f"{total} matches ({total_worked} worked)")
            else:
                self.scp_match_var.set(f"{total} matches")
        else:
            # No matches - try QRZ lookup if credentials configured
            if len(partial) >= 3 and self.config.get('qrz_username') and self.config.get('qrz_password'):
                self.scp_match_var.set("Checking QRZ...")
                # Run QRZ lookup in background thread
                import threading
                threading.Thread(target=self._qrz_lookup_async, args=(partial,), daemon=True).start()
            else:
                self.scp_match_var.set("0 matches")
    
    def _on_scp_select(self, event=None):
        """Handle double-click or Enter on SCP listbox"""
        selection = self.scp_listbox.curselection()
        if selection:
            call = self.scp_listbox.get(selection[0])
            # Remove suffixes if present
            if call.endswith(" (QRZ)"):
                call = call[:-6]
            elif call.endswith(" (Worked)"):
                call = call[:-9]
            self.manual_call_var.set(call)
    
    def _qrz_lookup_async(self, callsign):
        """Look up callsign on QRZ.com (runs in background thread)"""
        import urllib.request
        import urllib.parse
        import xml.etree.ElementTree as ET
        import time
        
        # Check cache first (valid for 5 minutes)
        cache_entry = self._qrz_last_lookup.get(callsign.upper())
        if cache_entry:
            found, timestamp = cache_entry
            if time.time() - timestamp < 300:  # 5 minute cache
                self.root.after(0, lambda: self._qrz_show_result(callsign, found))
                return
        
        try:
            # Get session key if we don't have one
            if not self._qrz_session_key:
                if not self._qrz_login():
                    self.root.after(0, lambda: self.scp_match_var.set("QRZ login failed"))
                    return
            
            # Look up the callsign
            url = f"https://xmldata.qrz.com/xml/current/?s={self._qrz_session_key}&callsign={urllib.parse.quote(callsign)}"
            
            with urllib.request.urlopen(url, timeout=5) as response:
                data = response.read().decode('utf-8')
            
            # Parse XML response
            root = ET.fromstring(data)
            ns = {'qrz': 'http://xmldata.qrz.com'}
            
            # Check for session error (need to re-login)
            session = root.find('.//qrz:Session', ns)
            if session is None:
                session = root.find('.//Session')
            if session is not None:
                error = session.find('qrz:Error', ns)
                if error is None:
                    error = session.find('Error')
                if error is not None and error.text and 'session' in error.text.lower():
                    self._qrz_session_key = None
                    # Try once more with fresh login
                    if self._qrz_login():
                        self._qrz_lookup_async(callsign)
                    return
            
            # Check if callsign found
            callsign_elem = root.find('.//qrz:Callsign', ns)
            if callsign_elem is None:
                callsign_elem = root.find('.//Callsign')
            found = callsign_elem is not None
            
            # Cache the result
            self._qrz_last_lookup[callsign.upper()] = (found, time.time())
            
            # Update UI on main thread
            self.root.after(0, lambda: self._qrz_show_result(callsign, found))
            
        except Exception as e:
            print(f"QRZ lookup error: {e}")
            self.root.after(0, lambda: self.scp_match_var.set("QRZ lookup failed"))
    
    def _qrz_login(self):
        """Login to QRZ.com and get session key"""
        import urllib.request
        import urllib.parse
        import xml.etree.ElementTree as ET
        
        username = self.config.get('qrz_username', '')
        password = self.config.get('qrz_password', '')
        
        if not username or not password:
            return False
        
        try:
            url = f"https://xmldata.qrz.com/xml/current/?username={urllib.parse.quote(username)}&password={urllib.parse.quote(password)}"
            
            with urllib.request.urlopen(url, timeout=5) as response:
                data = response.read().decode('utf-8')
            
            # Debug: print raw response
            print(f"QRZ Response: {data[:500]}")
            
            # Parse XML response
            root = ET.fromstring(data)
            ns = {'qrz': 'http://xmldata.qrz.com'}
            
            # Get session element - fix deprecation warning
            session = root.find('.//qrz:Session', ns)
            if session is None:
                session = root.find('.//Session')
            
            if session is not None:
                # Check for error message first
                error_elem = session.find('qrz:Error', ns)
                if error_elem is None:
                    error_elem = session.find('Error')
                if error_elem is not None:
                    print(f"QRZ Error: {error_elem.text}")
                    return False
                
                # Get session key - fix deprecation warning
                key_elem = session.find('qrz:Key', ns)
                if key_elem is None:
                    key_elem = session.find('Key')
                if key_elem is not None:
                    self._qrz_session_key = key_elem.text
                    print(f"QRZ: Logged in successfully")
                    return True
            
            print(f"QRZ: Login failed - no session key in response")
            return False
            
        except Exception as e:
            print(f"QRZ login error: {e}")
            return False
    
    def _qrz_show_result(self, callsign, found):
        """Show QRZ lookup result in SCP listbox (called on main thread)"""
        # Only show if the callsign field still matches what we looked up
        current = self.manual_call_var.get().upper()
        if current != callsign.upper():
            return  # User typed something else, ignore this result
        
        if found:
            self.scp_listbox.delete(0, tk.END)
            self.scp_listbox.insert(tk.END, f"{callsign.upper()} (QRZ)")
            self.scp_match_var.set("Found on QRZ")
        else:
            self.scp_match_var.set("Not found (SCP or QRZ)")
    
    def _lookup_callsign_qrz(self):
        """Manual QRZ lookup button - opens QRZ.com in browser"""
        import webbrowser
        callsign = self.manual_call_var.get().strip().upper()
        if callsign:
            webbrowser.open(f"https://www.qrz.com/db/{callsign}")
        else:
            self.scp_match_var.set("Enter a callsign first")
    
    def _lookup_callsign_callook(self):
        """Manual Callook lookup button - opens Callook.info in browser (US calls only)"""
        import webbrowser
        callsign = self.manual_call_var.get().strip().upper()
        if callsign:
            webbrowser.open(f"https://callook.info/{callsign}")
        else:
            self.scp_match_var.set("Enter a callsign first")
    
    def _on_band_select(self, event=None):
        """Auto-fill typical frequency when band is selected"""
        band = self.manual_band_var.get()
        mode = self.manual_mode_var.get()
        
        # Typical calling frequencies
        # USB is standard for VHF+ (above 10 MHz), LSB uses same frequencies
        freq_map = {
            '6m': {'USB': '50.125', 'LSB': '50.125', 'FM': '52.525', 'CW': '50.090'},
            '2m': {'USB': '144.200', 'LSB': '144.200', 'FM': '146.520', 'CW': '144.050'},
            '1.25m': {'USB': '222.100', 'LSB': '222.100', 'FM': '223.500', 'CW': '222.050'},
            '70cm': {'USB': '432.100', 'LSB': '432.100', 'FM': '446.000', 'CW': '432.050'},
            '33cm': {'USB': '903.100', 'LSB': '903.100', 'FM': '903.125', 'CW': '902.050'},
            '23cm': {'USB': '1296.100', 'LSB': '1296.100', 'FM': '1294.500', 'CW': '1296.050'},
            '13cm': {'USB': '2304.100', 'LSB': '2304.100', 'FM': '2304.100', 'CW': '2304.050'},
            '9cm': {'USB': '3456.100', 'LSB': '3456.100', 'FM': '3456.100', 'CW': '3456.050'},
            '6cm': {'USB': '5760.100', 'LSB': '5760.100', 'FM': '5760.100', 'CW': '5760.050'},
            '3cm': {'USB': '10368.100', 'LSB': '10368.100', 'FM': '10368.100', 'CW': '10368.050'},
        }
        
        if band in freq_map:
            self.manual_freq_var.set(freq_map[band].get(mode, freq_map[band]['USB']))
    
    def _on_mode_change(self):
        """Update RST defaults and frequency when mode changes"""
        mode = self.manual_mode_var.get()
        
        # Update RST defaults
        if mode == 'CW':
            self.manual_rst_sent_var.set('599')
            self.manual_rst_rcvd_var.set('599')
        else:
            self.manual_rst_sent_var.set('59')
            self.manual_rst_rcvd_var.set('59')
        
        # Update frequency if band is selected
        if self.manual_band_var.get():
            self._on_band_select()
    
    def _clear_manual_form(self):
        """Clear the manual entry form"""
        self.manual_call_var.set('')
        self.manual_grid_var.set('')
        self.manual_mygrid_var.set(self.current_grid)
        # Clear SCP results
        if hasattr(self, 'scp_listbox'):
            self.scp_listbox.delete(0, tk.END)
            self.scp_match_var.set("Type callsign to search...")
        # Keep band, mode, freq, RST for next QSO
    
    def create_test_tab(self, parent):
        """Create test mode tab"""
        frame = ttk.Frame(parent)
        
        # Grid Precision Frame
        precision_frame = ttk.LabelFrame(frame, text="Grid Precision", padding=10)
        precision_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.grid_precision_var = tk.IntVar(value=self.config.get('grid_precision', 4))
        
        ttk.Radiobutton(precision_frame, text="4-char (EM15) - VHF Contests", 
                       variable=self.grid_precision_var, value=4,
                       command=self.on_precision_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(precision_frame, text="6-char (EM15fp) - Testing / Distance", 
                       variable=self.grid_precision_var, value=6,
                       command=self.on_precision_change).pack(side=tk.LEFT, padx=10)
        
        ttk.Label(precision_frame, text="(6-char grids are ~2.5 x 3.5 miles - easier to test grid changes)",
                 foreground="gray").pack(side=tk.LEFT, padx=10)
        
        # Manual Grid Test Frame
        manual_frame = ttk.LabelFrame(frame, text="Manual Grid Test", padding=10)
        manual_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Grid entry
        ttk.Label(manual_frame, text="Test Grid:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        self.test_grid_var = tk.StringVar(value="EM15")
        ttk.Entry(manual_frame, textvariable=self.test_grid_var, width=15).grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        
        # Test button - sends to WSJT-X AND logger
        logger_name = LOGGER_NAMES.get(self.config.get('contest_logger', 'n1mm'), 'N1MM+')
        self.test_send_button = ttk.Button(manual_frame, text=f"Send to WSJT-X + {logger_name}", 
                   command=self.send_test_grid)
        self.test_send_button.grid(row=1, column=0, columnspan=2, pady=5, padx=5, sticky=tk.EW)
        
        self.test_hint_label = ttk.Label(manual_frame, text=f"Sends grid to all WSJT-X instances and {logger_name}",
                 foreground="gray")
        self.test_hint_label.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=5, padx=5)
        
        # Other Test Controls
        test_frame = ttk.LabelFrame(frame, text="Other Test Controls", padding=10)
        test_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(test_frame, text="Test Voice Announcement", 
                   command=self.test_voice).pack(pady=5, fill=tk.X)
        
        ttk.Button(test_frame, text="Test Victron Connection", 
                   command=self.test_victron).pack(pady=5, fill=tk.X)
        
        ttk.Button(test_frame, text="Reload WSJT-X Logs", 
                   command=self.reload_logs).pack(pady=5, fill=tk.X)
        
        # APRS Controls
        aprs_frame = ttk.LabelFrame(frame, text="APRS-IS Controls", padding=10)
        aprs_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.aprs_status_var = tk.StringVar(value="APRS: Disabled")
        ttk.Label(aprs_frame, textvariable=self.aprs_status_var).pack(pady=2)
        
        ttk.Button(aprs_frame, text="Send Beacon Now", 
                   command=self.send_aprs_beacon).pack(pady=5, fill=tk.X)
        
        ttk.Button(aprs_frame, text="Show APRS Stats", 
                   command=self.show_aprs_stats).pack(pady=5, fill=tk.X)
        
        return frame
    
    def create_about_tab(self, parent):
        """Create about/support tab with links and donation info"""
        import webbrowser
        
        frame = ttk.Frame(parent)
        
        # App info section
        info_frame = ttk.LabelFrame(frame, text="N5ZY Co-Pilot", padding=15)
        info_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(info_frame, text=f"Version {self.VERSION}", 
                 font=('Arial', 14, 'bold')).pack(pady=(0,5))
        ttk.Label(info_frame, text="VHF/UHF Contest Automation for Rovers",
                 font=('Arial', 11)).pack()
        ttk.Label(info_frame, text="By Marcus N5ZY",
                 foreground="gray").pack(pady=(5,0))
        
        # Links section
        links_frame = ttk.LabelFrame(frame, text="Resources", padding=15)
        links_frame.pack(fill=tk.X, padx=10, pady=10)
        
        def make_link_button(parent, text, url):
            btn = ttk.Button(parent, text=text, 
                           command=lambda: webbrowser.open(url))
            return btn
        
        # Blog / Documentation
        make_link_button(links_frame, "📖 Documentation & Blog", 
                        "https://n5zy.org/copilot").pack(fill=tk.X, pady=3)
        
        # Forum / Community
        make_link_button(links_frame, "💬 Community Forum (Groups.io)", 
                        "https://groups.io/g/n5zy-copilot").pack(fill=tk.X, pady=3)
        
        # GitHub
        make_link_button(links_frame, "💻 Source Code (GitHub)", 
                        "https://github.com/n5zy/copilot").pack(fill=tk.X, pady=3)
        
        # QRZ
        make_link_button(links_frame, "📻 N5ZY on QRZ", 
                        "https://www.qrz.com/db/N5ZY").pack(fill=tk.X, pady=3)
        
        # Support section
        support_frame = ttk.LabelFrame(frame, text="Support Development", padding=15)
        support_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(support_frame, 
                 text="If this tool helps your rover operation, consider a small donation\n"
                      "to help cover development costs. Suggested: $10-20",
                 justify=tk.CENTER).pack(pady=(0,10))
        
        donate_btn = ttk.Button(support_frame, text="💰 Donate via PayPal", 
                               command=lambda: webbrowser.open("https://paypal.me/n5zy"))
        donate_btn.pack(pady=5)
        
        ttk.Label(support_frame, text="Thank you for your support! 73 de N5ZY",
                 foreground="gray", font=('Arial', 9, 'italic')).pack(pady=(10,0))
        
        # Credits
        credits_frame = ttk.LabelFrame(frame, text="Credits", padding=10)
        credits_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(credits_frame, 
                 text="Developed January 2026 with assistance from Claude (Anthropic).\n"
                      "Special thanks to the Oklahoma Rovers and North Texas VHF communities.",
                 foreground="gray", justify=tk.CENTER).pack()
        
        return frame
    
    def on_precision_change(self):
        """Handle grid precision toggle"""
        precision = self.grid_precision_var.get()
        
        # Save to config
        self.config['grid_precision'] = precision
        self.save_config()
        
        # Update GPS monitor if running
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            self.gps_monitor.set_precision(precision)
        
        self.add_alert(f"Grid precision set to {precision} characters")
    
    def create_qso_log_tab(self, parent):
        """Create QSO log display tab - shows QSOs captured from all WSJT-X instances"""
        frame = ttk.Frame(parent)
        
        # Header info
        info_frame = ttk.Frame(frame)
        info_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(info_frame, text="QSO Log Relay", font=('Arial', 12, 'bold')).pack(side=tk.LEFT)
        ttk.Label(info_frame, text="  (Captures QSOs from ALL WSJT-X instances)", 
                 foreground="gray").pack(side=tk.LEFT)
        
        # QSO count
        self.qso_count_var = tk.StringVar(value="QSOs: 0")
        ttk.Label(info_frame, textvariable=self.qso_count_var, font=('Arial', 10, 'bold'),
                 foreground="blue").pack(side=tk.RIGHT, padx=10)
        
        # Status label at bottom (pack first so it's at bottom)
        status_frame = ttk.Frame(frame)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=5)
        self.qso_status_var = tk.StringVar(value="ADIF log: logs/n5zy_copilot_YYYYMMDD.adi")
        ttk.Label(status_frame, textvariable=self.qso_status_var, 
                 foreground="gray").pack(side=tk.LEFT)
        
        # Control buttons - vertical stack on right side (pack before treeview)
        button_frame = ttk.Frame(frame)
        button_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        
        ttk.Button(button_frame, text="Reload Contest Log", width=18,
                   command=self.reload_contest_log).pack(pady=2)
        
        ttk.Button(button_frame, text="Open ADIF Folder", width=18,
                   command=self.open_adif_folder).pack(pady=2)
        
        ttk.Button(button_frame, text="Delete Selected", width=18,
                   command=self.delete_selected_qso).pack(pady=2)
        
        ttk.Button(button_frame, text="Clear All", width=18,
                   command=self.clear_qso_display).pack(pady=2)
        
        # Treeview for QSO log - optimized for rover use (MyGrid more important than RST)
        columns = ('time', 'call', 'grid', 'band', 'mode', 'my_grid', 'source')
        self.qso_tree = ttk.Treeview(frame, columns=columns, show='headings', height=15)
        
        # Column headers
        self.qso_tree.heading('time', text='Time (UTC)')
        self.qso_tree.heading('call', text='Callsign')
        self.qso_tree.heading('grid', text='Their Grid')
        self.qso_tree.heading('band', text='Band')
        self.qso_tree.heading('mode', text='Mode')
        self.qso_tree.heading('my_grid', text='My Grid')
        self.qso_tree.heading('source', text='Source')
        
        # Column widths
        self.qso_tree.column('time', width=90)
        self.qso_tree.column('call', width=100)
        self.qso_tree.column('grid', width=70)
        self.qso_tree.column('band', width=60)
        self.qso_tree.column('mode', width=60)
        self.qso_tree.column('my_grid', width=70)
        self.qso_tree.column('source', width=100)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.qso_tree.yview)
        self.qso_tree.configure(yscrollcommand=scrollbar.set)
        
        # Pack treeview and scrollbar (fills remaining space)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
        self.qso_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Track QSO count
        self.qso_count = 0
        
        return frame
    
    def on_qso_logged(self, qso_data):
        """Called when a QSO is logged (from WSJT-X or Manual Entry)"""
        try:
            # Duplicate detection - suppress echo from N1MM+
            qso_key = (
                qso_data['datetime_off'].strftime('%Y%m%d%H%M') if qso_data.get('datetime_off') else '',
                qso_data.get('dx_call', ''),
                qso_data.get('band', '')
            )
            
            if not hasattr(self, '_recent_qsos'):
                self._recent_qsos = set()
            
            if qso_key in self._recent_qsos:
                # This is an echo (probably from N1MM+), suppress
                print(f"on_qso_logged: Suppressing duplicate for {qso_data.get('dx_call')}")
                return
            
            self._recent_qsos.add(qso_key)
            
            # Add to worked_calls for SCP matching
            dx_call = qso_data.get('dx_call', '').upper()
            if dx_call:
                self.worked_calls.add(dx_call)
            
            # Limit set size (keep last 100 QSOs)
            if len(self._recent_qsos) > 100:
                # Remove oldest entries (sets don't maintain order, but this prevents unbounded growth)
                self._recent_qsos = set(list(self._recent_qsos)[-50:])
            
            # Update count
            self.qso_count += 1
            self.qso_count_var.set(f"QSOs: {self.qso_count}")
            
            # Add to treeview (at the top)
            time_str = qso_data['datetime_off'].strftime('%H:%M:%S') if qso_data['datetime_off'] else ''
            source = qso_data.get('wsjtx_id', 'Unknown')
            
            self.qso_tree.insert('', 0, values=(
                time_str,
                qso_data['dx_call'],
                qso_data.get('dx_grid', ''),
                qso_data['band'],
                qso_data['mode'],
                self.current_grid or "",  # My Grid
                source                     # Source (WSJT-X instance or "Manual")
            ))
            
            # Add alert
            self.add_alert(f"QSO: {qso_data['dx_call']} on {qso_data['band']} via {source}")
            
            # Voice announcement
            if self.voice:
                self.voice.announce(f"QSO logged. {qso_data['dx_call']}", category="qso_logged")
            
            # Check for QSY opportunities (other bands this station operates)
            if self.qsy_advisor:
                self.qsy_advisor.log_qso(
                    qso_data['dx_call'],
                    qso_data['band'],
                    qso_data.get('dx_grid')
                )
            
            # Update status
            import datetime
            today = datetime.datetime.now().strftime('%Y%m%d')
            self.qso_status_var.set(f"Last QSO: {qso_data['dx_call']} - ADIF: logs/n5zy_copilot_{today}.adi")
            
        except Exception as e:
            print(f"Error updating QSO display: {e}")
    
    def open_adif_folder(self):
        """Open the logs folder in file explorer"""
        import subprocess
        import sys
        
        log_dir = os.path.join(os.path.dirname(__file__), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        if sys.platform == 'win32':
            os.startfile(log_dir)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', log_dir])
        else:
            subprocess.Popen(['xdg-open', log_dir])
    
    def create_psk_monitor_tab(self, parent):
        """Create PSK Reporter Monitor tab with split Priority/Propagation panes"""
        frame = ttk.Frame(parent)

        # Header with status
        header_frame = ttk.Frame(frame)
        header_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(header_frame, text="PSK Reporter Monitor",
                 font=('Arial', 12, 'bold')).pack(side=tk.LEFT)

        self.psk_status_var = tk.StringVar(value="Status: Not started")
        ttk.Label(header_frame, textvariable=self.psk_status_var,
                 foreground='gray').pack(side=tk.LEFT, padx=20)

        # Enable checkbox (uses shared variable from __init__)
        ttk.Checkbutton(header_frame, text="Enable Monitoring",
                       variable=self.psk_enabled_var,
                       command=self._toggle_psk_monitor).pack(side=tk.RIGHT, padx=10)

        # Main content - split into left (alerts) and right (band activity)
        content_frame = ttk.Frame(frame)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left side - Vertical PanedWindow with Priority + Propagation panes
        self.psk_paned = tk.PanedWindow(content_frame, orient=tk.VERTICAL, sashwidth=4)
        self.psk_paned.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,5))

        # ── Priority Alerts pane (top) — pinned, time-aged ──
        self.psk_priority_frame = ttk.LabelFrame(self.psk_paned, text="Priority Alerts", padding=5)

        alert_columns = ('time', 'pri', 'band', 'nearby', 'far', 'entity', 'qso_dist', 'my_dist', 'my_dir', 'prop', 'mode')

        self.psk_priority_tree = ttk.Treeview(self.psk_priority_frame, columns=alert_columns,
                                               show='headings', height=5)
        for col, heading, width in [
            ('time', 'Time', 50), ('pri', 'P', 25), ('band', 'Band', 45),
            ('nearby', 'Nearby', 75), ('far', 'Far (Try!)', 75), ('entity', 'Entity', 95),
            ('qso_dist', 'QSO Dist', 55),
            ('my_dist', 'My Dist', 50), ('my_dir', 'My Dir', 35), ('prop', 'Prop', 50), ('mode', 'Mode', 45)
        ]:
            self.psk_priority_tree.heading(col, text=heading)
            self.psk_priority_tree.column(col, width=width)

        # Priority tree tags
        self.psk_priority_tree.tag_configure('dx', foreground='magenta', font=('Arial', 9, 'bold'))
        self.psk_priority_tree.tag_configure('dx2', foreground='cyan', font=('Arial', 9, 'bold'))
        self.psk_priority_tree.tag_configure('dx3', foreground='dodgerblue', font=('Arial', 9, 'bold'))
        self.psk_priority_tree.tag_configure('ap', foreground='lime green', font=('Arial', 9, 'bold'))

        pri_scroll = ttk.Scrollbar(self.psk_priority_frame, orient=tk.VERTICAL,
                                    command=self.psk_priority_tree.yview)
        self.psk_priority_tree.configure(yscrollcommand=pri_scroll.set)
        self.psk_priority_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pri_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.psk_priority_tree.bind('<Double-1>', self._psk_open_pskreporter)

        # ── Propagation Alerts pane (bottom) — rolling, sortable, no row cap ──
        prop_frame = ttk.LabelFrame(self.psk_paned, text="Propagation Alerts", padding=5)

        self.psk_alert_tree = ttk.Treeview(prop_frame, columns=alert_columns,
                                           show='headings', height=12)

        for col, heading, width in [
            ('time', 'Time', 50), ('pri', 'P', 25), ('band', 'Band', 45),
            ('nearby', 'Nearby', 75), ('far', 'Far (Try!)', 75), ('entity', 'Entity', 95),
            ('qso_dist', 'QSO Dist', 55),
            ('my_dist', 'My Dist', 50), ('my_dir', 'My Dir', 35), ('prop', 'Prop', 50), ('mode', 'Mode', 45)
        ]:
            self.psk_alert_tree.heading(col, text=heading,
                                         command=lambda c=col: self._sort_psk_column(c))
            self.psk_alert_tree.column(col, width=width)

        # Configure row colors for priority levels (propagation pane)
        self.psk_alert_tree.tag_configure('p1', foreground='red', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('p2', foreground='orange', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('p3', foreground='goldenrod')
        self.psk_alert_tree.tag_configure('p4', foreground='green')
        self.psk_alert_tree.tag_configure('dx', foreground='magenta', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('dx2', foreground='cyan', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('dx3', foreground='dodgerblue', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('ap', foreground='lime green', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('info', foreground='gray')

        psk_scrollbar = ttk.Scrollbar(prop_frame, orient=tk.VERTICAL,
                                      command=self.psk_alert_tree.yview)
        self.psk_alert_tree.configure(yscrollcommand=psk_scrollbar.set)

        self.psk_alert_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        psk_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Double-click to open PSK Reporter
        self.psk_alert_tree.bind('<Double-1>', self._psk_open_pskreporter)

        # Track sort state for propagation pane
        self._psk_sort_col = None
        self._psk_sort_reverse = False

        # Add panes to PanedWindow
        # Priority pane is conditionally added by _update_priority_pane_visibility()
        self.psk_paned.add(prop_frame, stretch='always')
        self._psk_priority_pane_visible = False

        # Right side - Band Activity Summary
        psk_activity_outer = ttk.LabelFrame(content_frame, text="Band Activity", padding=5)
        psk_activity_outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(5,0))

        # Container for band rows (rebuilt when My Bands changes)
        self.psk_band_rows_frame = ttk.Frame(psk_activity_outer)
        self.psk_band_rows_frame.pack(fill=tk.X)

        # Build band activity rows from My Bands config
        self.band_activity_labels = {}
        self._rebuild_band_activity_labels()

        # Legend (stays fixed below band rows)
        ttk.Separator(psk_activity_outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        ttk.Label(psk_activity_outer, text="Priority:", font=('Arial', 9, 'bold')).pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="DX! Priority Stn", font=('Arial', 8), foreground='magenta').pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="DX2 New Entity", font=('Arial', 8), foreground='cyan').pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="DX3 New Band/Mode", font=('Arial', 8), foreground='dodgerblue').pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="AP! Friends/Family", font=('Arial', 8), foreground='lime green').pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="P1! MSp-E UHF+", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="P2! MSp-E 2m", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="P2  Sp-E UHF", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="P3  Sp-E 2m/6m", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="P4  Tropo", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(psk_activity_outer, text="P5  LOS/GW/Sky", font=('Arial', 8)).pack(anchor=tk.W)

        # Bottom - Settings summary
        settings_frame = ttk.Frame(frame)
        settings_frame.pack(fill=tk.X, padx=5, pady=5)

        self.psk_settings_var = tk.StringVar(value="VHF radius: 250 mi | Baseline: 15 min | Poll: 5 min")
        ttk.Label(settings_frame, textvariable=self.psk_settings_var,
                 foreground='gray').pack(side=tk.LEFT)

        # Last updated timestamp
        self.psk_last_update_var = tk.StringVar(value="Last updated: --:--:--")
        ttk.Label(settings_frame, textvariable=self.psk_last_update_var,
                 foreground='gray').pack(side=tk.LEFT, padx=20)

        ttk.Button(settings_frame, text="Clear Alerts",
                  command=self._clear_psk_alerts).pack(side=tk.RIGHT, padx=5)

        ttk.Button(settings_frame, text="Refresh Now",
                  command=self._refresh_psk_now).pack(side=tk.RIGHT, padx=5)

        # Set initial priority pane visibility (after a short delay for UI to settle)
        self.root.after(500, self._update_priority_pane_visibility)

        return frame
    
    def _toggle_psk_monitor(self):
        """Toggle PSK Reporter monitoring on/off"""
        enabled = self.psk_enabled_var.get()
        self.config['psk_enabled'] = enabled
        self.save_config()
        
        if enabled:
            self._start_psk_monitor()
        else:
            self._stop_psk_monitor()
    
    def _start_psk_monitor(self):
        """Start PSK Reporter monitoring"""
        if self.psk_monitor is None:
            self.psk_monitor = PSKMonitor(
                my_grid=self.current_grid if self.current_grid != "----" else None,
                config=self.config,
                alert_callback=self._on_psk_alert,
                voice=self.voice
            )
            self.psk_monitor.set_qsy_advisor(self.qsy_advisor)
            # Set spot callback for displaying ALL nearby spots
            self.psk_monitor.spot_callback = self._on_psk_spot
            # Set poll complete callback for updating timestamp
            self.psk_monitor.poll_complete_callback = self._on_psk_poll_complete
        
        self.psk_monitor.start()
        self.psk_status_var.set("Status: Monitoring (polls every 5 min)")
        self.add_alert("PSK Monitor: Started")
    
    def _stop_psk_monitor(self):
        """Stop PSK Reporter monitoring"""
        if self.psk_monitor:
            self.psk_monitor.stop()
        self.psk_status_var.set("Status: Stopped")
        self.add_alert("PSK Monitor: Stopped")
    
    def _resolve_entity(self, callsign):
        """Resolve callsign to DXCC entity name via cty.dat, truncated to 15 chars"""
        try:
            if not callsign or not hasattr(self, 'cty_lookup') or not self.cty_lookup:
                return ''
            entity = self.cty_lookup.lookup(callsign)
            if entity and entity.name:
                return entity.name[:15]
        except Exception:
            pass
        return ''

    def _on_psk_spot(self, spot_data):
        """Handle individual PSK spot — route to Priority or Propagation pane"""
        try:
            from datetime import datetime

            now = datetime.now()
            time_str = now.strftime('%H:%M')

            # Update last updated timestamp
            self.psk_last_update_var.set(f"Last updated: {now.strftime('%H:%M:%S')}")

            # Determine priority based on prop mode and band
            prop_mode = spot_data.get('prop_mode', '')
            band = spot_data.get('band', '')

            # Check PriorityEngine first (DX!, AP!, DX2, DX3)
            priority_result = None
            if hasattr(self, 'priority_engine') and self.priority_engine:
                # Check both sender and receiver calls
                for call_key in ['nearby_call', 'far_call']:
                    call = spot_data.get(call_key, '')
                    if call:
                        mode = spot_data.get('mode', 'FT8')
                        priority_result = self.priority_engine.check(call, band, mode)
                        if priority_result:
                            break

            # Also check legacy priority_dx flag
            if not priority_result and spot_data.get('priority_dx'):
                # Legacy DX! path — build a result-like dict for routing
                pri_text = 'DX!'
                row_tag = 'dx'
                matched_call = spot_data.get('priority_call', '')
                if matched_call:
                    self._priority_voice_alert(matched_call, band)
                # Route to priority pane
                self._insert_priority_spot(time_str, pri_text, row_tag, spot_data, prop_mode)
                self._update_psk_band_activity()
                return

            if priority_result:
                pri_text = priority_result.code
                row_tag = priority_result.tag
                # Fire voice alert with dedup
                self._priority_voice_alert(priority_result.callsign, band,
                                            voice_msg=priority_result.voice_msg)
                # Route to Priority pane
                self._insert_priority_spot(time_str, pri_text, row_tag, spot_data, prop_mode)
                self._update_psk_band_activity()
                return

            # No priority match — determine propagation priority for Propagation pane
            if prop_mode == 'multi_hop_e' and band in ['70cm', '1.25m', '33cm', '23cm']:
                pri_text = 'P1!'
                row_tag = 'p1'
            elif prop_mode == 'multi_hop_e' and band == '2m':
                pri_text = 'P2!'
                row_tag = 'p2'
            elif prop_mode == 'sporadic_e' and band in ['70cm', '1.25m']:
                pri_text = 'P2'
                row_tag = 'p2'
            elif prop_mode == 'sporadic_e':
                pri_text = 'P3'
                row_tag = 'p3'
            elif prop_mode == 'tropo':
                pri_text = 'P4'
                row_tag = 'p4'
            else:
                pri_text = 'P5'
                row_tag = 'info'

            # Format prop mode for display
            prop_display = {
                'multi_hop_e': 'MSp-E', 'sporadic_e': 'Sp-E',
                'tropo': 'Tropo', 'line_of_sight': 'LOS',
                'groundwave': 'GW', 'skywave': 'Sky',
            }.get(prop_mode, prop_mode)

            self.psk_alert_tree.insert('', 0, values=(
                time_str, pri_text, band,
                spot_data.get('nearby_call', ''),
                spot_data.get('far_call', ''),
                self._resolve_entity(spot_data.get('far_call', '')),
                spot_data.get('qso_distance', ''),
                spot_data.get('my_distance', ''),
                spot_data.get('bearing', ''),
                prop_display,
                spot_data.get('mode', 'FT8')
            ), tags=(row_tag,))

            # Update band activity display
            self._update_psk_band_activity()

            # No row cap — rely on scrollbar (removed old 50-row trim)

        except Exception as e:
            print(f"Error adding PSK spot to tree: {e}")

    def _insert_priority_spot(self, time_str, pri_text, row_tag, spot_data, prop_mode):
        """Insert a spot into the Priority Alerts pane (deduped by callsign+band)"""
        prop_display = {
            'multi_hop_e': 'MSp-E', 'sporadic_e': 'Sp-E',
            'tropo': 'Tropo', 'line_of_sight': 'LOS',
        }.get(prop_mode, prop_mode)

        far_call = spot_data.get('far_call', '')
        band = spot_data.get('band', '')

        # Dedup: if same callsign+band already exists, update time and move to top
        for item in self.psk_priority_tree.get_children():
            values = self.psk_priority_tree.item(item, 'values')
            if len(values) >= 5 and values[4] == far_call and values[2] == band:
                new_values = (time_str,) + values[1:]
                self.psk_priority_tree.item(item, values=new_values)
                self.psk_priority_tree.move(item, '', 0)
                return

        self.psk_priority_tree.insert('', 0, values=(
            time_str, pri_text, band,
            spot_data.get('nearby_call', ''),
            far_call,
            self._resolve_entity(far_call),
            spot_data.get('qso_distance', ''),
            spot_data.get('my_distance', ''),
            spot_data.get('bearing', ''),
            prop_display,
            spot_data.get('mode', 'FT8')
        ), tags=(row_tag,))

        # Cap at 25 rows
        children = self.psk_priority_tree.get_children()
        if len(children) > 25:
            for child in children[25:]:
                self.psk_priority_tree.delete(child)

        # Ensure priority pane is visible
        if not self._psk_priority_pane_visible:
            self._update_priority_pane_visibility(force_show=True)
    
    def _on_psk_alert(self, message, priority):
        """Handle PSK Monitor alert - adds to main Alerts tab only"""
        # Add to main alerts tab (not PSK tree - that's handled by _on_psk_spot)
        self.add_alert(message, priority=(priority <= 2))
    
    def _on_psk_poll_complete(self, spot_count):
        """Called when PSK Reporter poll finishes (regardless of spot count)"""
        from datetime import datetime
        now = datetime.now()
        self.psk_last_update_var.set(f"Last updated: {now.strftime('%H:%M:%S')} ({spot_count} spots)")
    
    def _clear_psk_alerts(self):
        """Clear Propagation alerts display and reset alert cooldowns"""
        for item in self.psk_alert_tree.get_children():
            self.psk_alert_tree.delete(item)
        # Reset PSK Monitor's alert cooldowns so cleared spots can re-appear
        if hasattr(self, 'psk_monitor') and self.psk_monitor:
            self.psk_monitor.recent_alerts.clear()

    # ── Priority Pane Management ──

    def _update_priority_pane_visibility(self, force_show=False):
        """Show/hide Priority Alerts pane based on mode and enabled alerts"""
        if not hasattr(self, 'psk_paned'):
            return

        show = force_show
        if not show:
            mode = self.config.get('contest_mode', 'vhf')
            dx_stations = self.config.get('dx_priority_stations', '')
            ap_stations = self.config.get('ap_priority_stations', '')
            dx2 = self.config.get('dx2_enabled', False)
            dx3 = self.config.get('dx3_enabled', False)

            if mode == 'daily_dx' and (dx_stations or dx2 or dx3):
                show = True
            if mode in ('vhf', '222up', 'qso_party') and (ap_stations or dx2 or dx3):
                show = True

        if show and not self._psk_priority_pane_visible:
            # Insert Priority pane at the top of the PanedWindow
            self.psk_paned.add(self.psk_priority_frame, before=self.psk_paned.panes()[0],
                                stretch='never', height=150)
            self._psk_priority_pane_visible = True
        elif not show and self._psk_priority_pane_visible:
            self.psk_paned.forget(self.psk_priority_frame)
            self._psk_priority_pane_visible = False

    def _age_priority_alerts(self):
        """Remove priority alerts older than 30 minutes. Runs on a timer."""
        if not hasattr(self, 'psk_priority_tree'):
            return

        from datetime import datetime
        now = datetime.now()

        for item in self.psk_priority_tree.get_children():
            values = self.psk_priority_tree.item(item, 'values')
            if values:
                try:
                    # Parse time from "HH:MM" format
                    spot_time = datetime.strptime(values[0], '%H:%M').replace(
                        year=now.year, month=now.month, day=now.day)
                    if (now - spot_time).total_seconds() > 1800:  # 30 minutes
                        self.psk_priority_tree.delete(item)
                except Exception:
                    pass

        # Schedule next aging check in 60 seconds
        if hasattr(self, 'root'):
            self.root.after(60000, self._age_priority_alerts)

    def _sort_psk_column(self, col):
        """Sort Propagation pane by column click"""
        if self._psk_sort_col == col:
            self._psk_sort_reverse = not self._psk_sort_reverse
        else:
            self._psk_sort_col = col
            self._psk_sort_reverse = False

        # Get all items with values
        items = [(self.psk_alert_tree.set(k, col), k) for k in self.psk_alert_tree.get_children()]

        # Numeric sort for distance columns
        numeric_cols = {'qso_dist', 'my_dist'}
        if col == 'pri':
            # Priority rank: DX!/AP! highest, P1!→P4 descending, -- (LOS) lowest
            pri_rank = {'DX!': 0, 'DX2': 0, 'DX3': 0, 'AP!': 0,
                        'P1!': 1, 'P2!': 2, 'P2': 3, 'P3': 4, 'P4': 5, 'P5': 6}
            items.sort(key=lambda item: pri_rank.get(item[0], 6),
                       reverse=self._psk_sort_reverse)
        elif col in numeric_cols:
            def sort_key(item):
                try:
                    return float(item[0]) if item[0] else 0
                except ValueError:
                    return 0
            items.sort(key=sort_key, reverse=self._psk_sort_reverse)
        else:
            items.sort(key=lambda t: t[0], reverse=self._psk_sort_reverse)

        # Rearrange items
        for index, (_, k) in enumerate(items):
            self.psk_alert_tree.move(k, '', index)

        # Update heading indicators
        alert_columns = ('time', 'pri', 'band', 'nearby', 'far', 'entity', 'qso_dist', 'my_dist', 'my_dir', 'prop', 'mode')
        heading_names = {
            'time': 'Time', 'pri': 'P', 'band': 'Band', 'nearby': 'Nearby',
            'far': 'Far (Try!)', 'entity': 'Entity', 'qso_dist': 'QSO Dist',
            'my_dist': 'My Dist', 'my_dir': 'My Dir', 'prop': 'Prop', 'mode': 'Mode',
        }
        for c in alert_columns:
            indicator = ''
            if c == col:
                indicator = ' ▼' if self._psk_sort_reverse else ' ▲'
            self.psk_alert_tree.heading(c, text=heading_names[c] + indicator)

    # ── LoTW Integration Handlers ──

    def _lotw_refresh(self):
        """Download LoTW credits in background thread"""
        import threading
        username = self.lotw_username_var.get().strip()
        password = self.lotw_password_var.get().strip()
        if not username or not password:
            from tkinter import messagebox
            messagebox.showwarning("LoTW", "Please enter LoTW username and password first.")
            return

        self.lotw_status_var.set("Downloading from LoTW...")

        def _on_done(success):
            if success:
                self.config['lotw_last_refresh'] = datetime.now().isoformat()[:10]
                self.save_config()
                # Enrich cty.dat with LoTW mapping
                if hasattr(self, 'cty_lookup') and self.cty_lookup:
                    self.cty_lookup.set_dxcc_mapping(self.lotw_client.get_prefix_to_dxcc_mapping())
                # Reconfigure priority engine
                if hasattr(self, 'priority_engine') and self.priority_engine:
                    self.priority_engine.configure(self.config, self.cty_lookup, self.lotw_client)
            self.root.after(0, lambda: self.lotw_status_var.set(self._get_lotw_status_text()))

        if not hasattr(self, 'lotw_client') or not self.lotw_client:
            from modules.lotw_client import LoTWClient
            self.lotw_client = LoTWClient(self.config)
            self.lotw_client.cty_lookup = getattr(self, 'cty_lookup', None)
        self.lotw_client.download_credits_async(callback=_on_done,
                                                  username=username, password=password)

    def _lotw_load_file(self):
        """Load LoTW ADIF from file"""
        from tkinter import filedialog
        filepath = filedialog.askopenfilename(
            title="Select LoTW ADIF File",
            filetypes=[("ADIF files", "*.adi *.adif"), ("All files", "*.*")]
        )
        if not filepath:
            return

        if not hasattr(self, 'lotw_client') or not self.lotw_client:
            from modules.lotw_client import LoTWClient
            self.lotw_client = LoTWClient(self.config)
            self.lotw_client.cty_lookup = getattr(self, 'cty_lookup', None)

        success = self.lotw_client.load_from_file(filepath)
        if success:
            # Cache it
            self.lotw_client.save_cache('data/lotw_credits.adi',
                                         open(filepath, 'r', encoding='utf-8', errors='ignore').read())
            self.config['lotw_last_refresh'] = datetime.now().isoformat()[:10]
            self.save_config()
            # Enrich cty.dat
            if hasattr(self, 'cty_lookup') and self.cty_lookup:
                self.cty_lookup.set_dxcc_mapping(self.lotw_client.get_prefix_to_dxcc_mapping())
            if hasattr(self, 'priority_engine') and self.priority_engine:
                self.priority_engine.configure(self.config, self.cty_lookup, self.lotw_client)
            from tkinter import messagebox
            status = self.lotw_client.get_status()
            messagebox.showinfo("LoTW", f"Loaded {status['record_count']} QSOs, "
                              f"{status['entity_count']} confirmed entities")
        else:
            from tkinter import messagebox
            messagebox.showerror("LoTW", "Failed to parse ADIF file")

        self.lotw_status_var.set(self._get_lotw_status_text())

    def _get_lotw_status_text(self):
        """Build LoTW status label text"""
        if hasattr(self, 'lotw_client') and self.lotw_client and self.lotw_client.is_loaded():
            status = self.lotw_client.get_status()
            last = status.get('last_refresh', '')[:10] or 'Unknown'
            return f"Loaded: {status['entity_count']} entities, {status['record_count']} QSOs | Last refresh: {last}"
        last = self.config.get('lotw_last_refresh', '')
        if last:
            return f"Not loaded (last refresh: {last})"
        return "Not loaded — download or load file to enable DX2/DX3 alerts"

    def _cty_update(self):
        """Download latest cty.dat from AD1C in background"""
        import threading
        self.cty_status_var.set("Downloading from AD1C...")

        def _worker():
            if not hasattr(self, 'cty_lookup') or not self.cty_lookup:
                from modules.cty_lookup import CTYLookup
                self.cty_lookup = CTYLookup()
            success = self.cty_lookup.update_from_ad1c('data')
            if success:
                self.cty_lookup.load_dxcc_mapping('data/dxcc_entities.json')
                self.config['cty_last_update'] = datetime.now().isoformat()[:10]
                self.save_config()
                if hasattr(self, 'priority_engine') and self.priority_engine:
                    self.priority_engine.configure(self.config, self.cty_lookup,
                                                    getattr(self, 'lotw_client', None))
            self.root.after(0, lambda: self.cty_status_var.set(self._get_cty_status_text()))

        threading.Thread(target=_worker, daemon=True).start()

    def _get_cty_status_text(self):
        """Build cty.dat status label text"""
        if hasattr(self, 'cty_lookup') and self.cty_lookup and self.cty_lookup._loaded:
            status = self.cty_lookup.get_status()
            last = self.config.get('cty_last_update', 'bundled')
            return f"Loaded: {status['entity_count']} entities, {status['prefix_count']} prefixes | Updated: {last}"
        return "Not loaded — click Update to download"

    def _check_lotw_auto_refresh(self):
        """Check if LoTW data needs auto-refresh (called 30s after startup)"""
        if not self.config.get('lotw_auto_refresh', True):
            return
        if self.config.get('contest_mode', 'vhf') != 'daily_dx':
            return
        if not self.config.get('lotw_username', '') or not self.config.get('lotw_password', ''):
            return
        if hasattr(self, 'lotw_client') and self.lotw_client and not self.lotw_client.needs_refresh():
            return
        print("LoTW: Auto-refreshing (weekly, Daily DX mode active)...")
        self._lotw_refresh()

    def _psk_open_pskreporter(self, event):
        """Open PSK Reporter map for selected spot (works for both trees)"""
        import webbrowser

        # Determine which tree was clicked
        tree = event.widget
        selection = tree.selection()
        if not selection:
            return

        item = selection[0]
        values = tree.item(item, 'values')
        # columns: time, pri, band, nearby, far, qso_dist, my_dist, my_dir, prop, mode
        if len(values) >= 5:
            far_call = values[4]  # The "far" callsign is who to look up
            if far_call and far_call != '--':
                url = f"https://pskreporter.info/pskmap.html?callsign={far_call}"
                webbrowser.open(url)
    
    def _refresh_psk_now(self):
        """Force an immediate PSK Reporter poll"""
        if self.psk_monitor and self.psk_enabled_var.get():
            self.add_alert("PSK Monitor: Manual refresh requested")
            # Run poll in thread to not block UI
            import threading
            threading.Thread(target=self.psk_monitor._poll_psk_reporter, daemon=True).start()
        else:
            messagebox.showinfo("PSK Monitor", "Enable PSK monitoring first")
    
    def _update_psk_band_activity(self):
        """Update band activity display from PSK monitor"""
        if self.psk_monitor:
            activity = self.psk_monitor.get_band_activity()
            for band, label_var in self.band_activity_labels.items():
                count = activity.get(band, 0)
                if count > 0:
                    label_var.set(f"{count} spots")
                else:
                    label_var.set("--")

    def _rebuild_band_activity_labels(self):
        """Rebuild the Band Activity panel from My Bands config.
        Called at startup and when settings are saved."""
        # Destroy existing band row widgets
        for widget in list(self.psk_band_rows_frame.winfo_children()):
            widget.destroy()

        self.band_activity_labels = {}

        # Canonical display order (top = highest band, bottom = lowest)
        display_order = ['1mm', '2mm', '4mm', '6mm', '1.2cm', '3cm', '5cm', '9cm',
                         '13cm', '23cm', '33cm', '70cm', '1.25m', '2m', '6m',
                         '10m', '12m', '15m', '17m', '20m', '30m', '40m', '60m', '80m', '160m']

        my_bands = self.config.get('my_bands', ['6m', '2m', '1.25m', '70cm', '33cm', '23cm'])

        # Filter to only selected bands, in canonical order
        bands_to_show = [b for b in display_order if b in my_bands]

        for band in bands_to_show:
            row_frame = ttk.Frame(self.psk_band_rows_frame)
            row_frame.pack(fill=tk.X, pady=2)

            ttk.Label(row_frame, text=f"{band}:", width=6).pack(side=tk.LEFT)

            activity_var = tk.StringVar(value="--")
            self.band_activity_labels[band] = activity_var

            lbl = ttk.Label(row_frame, textvariable=activity_var, width=8,
                           font=('Arial', 10, 'bold'))
            lbl.pack(side=tk.LEFT, padx=5)

    def create_grid_corner_tab(self, parent):
        """Create Grid Corner QSO Tracker tab for rover-to-rover operations"""
        frame = ttk.Frame(parent)
        
        # Grid Corner session data
        self.gc_rovers = []  # List of {call, grid, bands: [list]}
        self.gc_qsos = {}    # {(my_grid, their_call, their_grid, band): timestamp}
        self.gc_my_grid_var = tk.StringVar(value=self.current_grid)
        
        # Available bands for grid corner ops
        self.gc_available_bands = self.config.get('my_bands', ['6m', '2m', '1.25m', '70cm', '33cm', '23cm'])
        
        # Top section - Your position and session controls
        top_frame = ttk.Frame(frame)
        top_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Left: Your grid
        my_frame = ttk.LabelFrame(top_frame, text="Your Position", padding=5)
        my_frame.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(my_frame, text="Grid:").pack(side=tk.LEFT)
        self.gc_my_grid_combo = ttk.Combobox(my_frame, textvariable=self.gc_my_grid_var, 
                                              width=6, values=[])
        self.gc_my_grid_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(my_frame, text="Use GPS", command=self._gc_use_gps_grid).pack(side=tk.LEFT, padx=5)
        
        # Right: Session controls
        session_frame = ttk.LabelFrame(top_frame, text="Session", padding=5)
        session_frame.pack(side=tk.LEFT, padx=20)
        
        ttk.Button(session_frame, text="New Session", command=self._gc_new_session).pack(side=tk.LEFT, padx=5)
        ttk.Button(session_frame, text="Export Log", command=self._gc_export_log).pack(side=tk.LEFT, padx=5)
        
        # Progress display
        self.gc_progress_var = tk.StringVar(value="QSOs: 0 / 0")
        ttk.Label(session_frame, textvariable=self.gc_progress_var, 
                 font=('Arial', 12, 'bold')).pack(side=tk.LEFT, padx=20)
        
        # Middle section - Rovers list and Add rover
        middle_frame = ttk.Frame(frame)
        middle_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left: Rovers in session
        rovers_frame = ttk.LabelFrame(middle_frame, text="Rovers in Session", padding=5)
        rovers_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,5))
        
        # Rovers treeview
        rover_cols = ('call', 'grid', 'bands', 'worked', 'remain')
        self.gc_rover_tree = ttk.Treeview(rovers_frame, columns=rover_cols, 
                                           show='headings', height=6)
        
        self.gc_rover_tree.heading('call', text='Callsign')
        self.gc_rover_tree.heading('grid', text='Grid')
        self.gc_rover_tree.heading('bands', text='Bands')
        self.gc_rover_tree.heading('worked', text='Worked')
        self.gc_rover_tree.heading('remain', text='Remain')
        
        self.gc_rover_tree.column('call', width=80)
        self.gc_rover_tree.column('grid', width=60)
        self.gc_rover_tree.column('bands', width=150)
        self.gc_rover_tree.column('worked', width=60)
        self.gc_rover_tree.column('remain', width=60)
        
        self.gc_rover_tree.pack(fill=tk.BOTH, expand=True)
        self.gc_rover_tree.bind('<<TreeviewSelect>>', self._gc_on_rover_select)
        
        # Rover controls
        rover_btn_frame = ttk.Frame(rovers_frame)
        rover_btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(rover_btn_frame, text="Remove Selected", 
                  command=self._gc_remove_rover).pack(side=tk.LEFT, padx=5)
        ttk.Button(rover_btn_frame, text="Update Grid", 
                  command=self._gc_update_rover_grid).pack(side=tk.LEFT, padx=5)
        
        # Right: Add rover
        add_frame = ttk.LabelFrame(middle_frame, text="Add Rover", padding=10)
        add_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(5,0))
        
        ttk.Label(add_frame, text="Callsign:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.gc_add_call_var = tk.StringVar()
        ttk.Entry(add_frame, textvariable=self.gc_add_call_var, width=10).grid(row=0, column=1, pady=2)
        
        ttk.Label(add_frame, text="Grid:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.gc_add_grid_var = tk.StringVar()
        ttk.Entry(add_frame, textvariable=self.gc_add_grid_var, width=10).grid(row=1, column=1, pady=2)
        
        ttk.Label(add_frame, text="Bands:").grid(row=2, column=0, sticky=tk.W, pady=2)
        
        # Band checkboxes
        self.gc_add_band_vars = {}
        band_frame = ttk.Frame(add_frame)
        band_frame.grid(row=2, column=1, sticky=tk.W)
        for i, band in enumerate(self.gc_available_bands):
            var = tk.BooleanVar(value=(band in ['6m', '2m']))  # Default 6m and 2m checked
            self.gc_add_band_vars[band] = var
            ttk.Checkbutton(band_frame, text=band, variable=var).grid(row=i//3, column=i%3, sticky=tk.W)
        
        ttk.Button(add_frame, text="Add Rover", command=self._gc_add_rover).grid(row=3, column=0, 
                                                                                  columnspan=2, pady=10)
        
        # Bottom section - QSO Matrix (work the selected rover)
        work_frame = ttk.LabelFrame(frame, text="Work Selected Rover", padding=10)
        work_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Target info
        target_frame = ttk.Frame(work_frame)
        target_frame.pack(fill=tk.X)
        
        ttk.Label(target_frame, text="Working:").pack(side=tk.LEFT)
        self.gc_target_var = tk.StringVar(value="(select a rover)")
        ttk.Label(target_frame, textvariable=self.gc_target_var, 
                 font=('Arial', 14, 'bold')).pack(side=tk.LEFT, padx=10)
        
        ttk.Button(target_frame, text="◀ Prev", command=self._gc_prev_rover).pack(side=tk.RIGHT, padx=5)
        ttk.Button(target_frame, text="Next ▶", command=self._gc_next_rover).pack(side=tk.RIGHT, padx=5)
        
        # Band buttons - click to log QSO
        self.gc_band_buttons = {}
        band_btn_frame = ttk.Frame(work_frame)
        band_btn_frame.pack(fill=tk.X, pady=10)
        
        for band in self.gc_available_bands:
            btn = tk.Button(band_btn_frame, text=f"{band}\n---", width=8, height=2,
                           command=lambda b=band: self._gc_log_qso(b),
                           state='disabled', bg='lightgray')
            btn.pack(side=tk.LEFT, padx=5)
            self.gc_band_buttons[band] = btn
        
        # Mode and Frequency controls
        mode_freq_frame = ttk.Frame(work_frame)
        mode_freq_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(mode_freq_frame, text="Mode:").pack(side=tk.LEFT, padx=5)
        self.gc_mode_var = tk.StringVar(value="SSB")
        ttk.Radiobutton(mode_freq_frame, text="SSB", variable=self.gc_mode_var, value="SSB",
                        command=self._gc_mode_changed).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_freq_frame, text="FM", variable=self.gc_mode_var, value="FM",
                        command=self._gc_mode_changed).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_freq_frame, text="CW", variable=self.gc_mode_var, value="CW",
                        command=self._gc_mode_changed).pack(side=tk.LEFT)
        
        ttk.Label(mode_freq_frame, text="   Freq (MHz):").pack(side=tk.LEFT, padx=5)
        self.gc_freq_var = tk.StringVar(value="144.200")
        freq_entry = ttk.Entry(mode_freq_frame, textvariable=self.gc_freq_var, width=10)
        freq_entry.pack(side=tk.LEFT, padx=5)
        
        # Default frequencies by band and mode
        # SSB calling frequencies
        self.gc_ssb_freqs = {
            '6m': '50.125', '2m': '144.200', '1.25m': '222.100',
            '70cm': '432.100', '33cm': '902.100', '23cm': '1296.100',
            '13cm': '2304.100', '9cm': '3456.100', '5cm': '5760.100',
            '3cm': '10368.100', '1.2cm': '24192.100', '6mm': '47088.100',
            '4mm': '75500.100', '2mm': '119980.100', '1mm': '241000.100'
        }
        # FM simplex frequencies
        self.gc_fm_freqs = {
            '6m': '52.525', '2m': '146.520', '1.25m': '223.500',
            '70cm': '446.000', '33cm': '906.500', '23cm': '1294.500',
            '13cm': '2304.100', '9cm': '3456.100', '5cm': '5760.100',
            '3cm': '10368.100', '1.2cm': '24192.100', '6mm': '47088.100',
            '4mm': '75500.100', '2mm': '119980.100', '1mm': '241000.100'
        }
        # CW calling frequencies
        self.gc_cw_freqs = {
            '6m': '50.090', '2m': '144.100', '1.25m': '222.050',
            '70cm': '432.100', '33cm': '902.100', '23cm': '1296.100',
            '13cm': '2304.100', '9cm': '3456.100', '5cm': '5760.100',
            '3cm': '10368.100', '1.2cm': '24192.100', '6mm': '47088.100',
            '4mm': '75500.100', '2mm': '119980.100', '1mm': '241000.100'
        }
        # Currently selected band for freq updates
        self.gc_current_band = '2m'
        
        # Session log at bottom
        log_frame = ttk.LabelFrame(frame, text="Session Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        log_cols = ('time', 'my_grid', 'their_call', 'their_grid', 'band')
        self.gc_log_tree = ttk.Treeview(log_frame, columns=log_cols, show='headings', height=5)
        
        self.gc_log_tree.heading('time', text='Time')
        self.gc_log_tree.heading('my_grid', text='My Grid')
        self.gc_log_tree.heading('their_call', text='Their Call')
        self.gc_log_tree.heading('their_grid', text='Their Grid')
        self.gc_log_tree.heading('band', text='Band')
        
        self.gc_log_tree.column('time', width=70)
        self.gc_log_tree.column('my_grid', width=60)
        self.gc_log_tree.column('their_call', width=80)
        self.gc_log_tree.column('their_grid', width=60)
        self.gc_log_tree.column('band', width=60)
        
        gc_log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.gc_log_tree.yview)
        self.gc_log_tree.configure(yscrollcommand=gc_log_scroll.set)
        
        self.gc_log_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        gc_log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        return frame
    
    def _gc_use_gps_grid(self):
        """Set grid corner grid from GPS"""
        if self.current_grid and self.current_grid != "----":
            self.gc_my_grid_var.set(self.current_grid)
            # Update combo values with nearby grids if at corner
            # For now just use current grid
    
    def _gc_new_session(self):
        """Start a new grid corner session"""
        if self.gc_rovers or self.gc_qsos:
            if not messagebox.askyesno("New Session", 
                                       "Clear current session and start fresh?"):
                return
        
        self.gc_rovers = []
        self.gc_qsos = {}
        self._gc_refresh_rover_list()
        self._gc_clear_log()
        self._gc_update_progress()
        self.gc_target_var.set("(select a rover)")
        self._gc_update_band_buttons(None)
    
    def _gc_mode_changed(self):
        """Update frequency when mode changes"""
        mode = self.gc_mode_var.get()
        band = self.gc_current_band
        
        if mode == 'SSB':
            freq = self.gc_ssb_freqs.get(band, '144.200')
        elif mode == 'FM':
            freq = self.gc_fm_freqs.get(band, '146.520')
        elif mode == 'CW':
            freq = self.gc_cw_freqs.get(band, '144.100')
        else:
            freq = '144.200'
        
        self.gc_freq_var.set(freq)
    
    def _gc_update_freq_for_next_band(self, just_logged_band):
        """After logging a QSO, update freq box to the next unworked band's default freq"""
        # Find the next unworked band for this rover
        selection = self.gc_rover_tree.selection()
        if not selection:
            return
        
        item = self.gc_rover_tree.item(selection[0])
        their_call = item['values'][0]
        their_grid = str(item['values'][1]).upper()
        my_grid = self.gc_my_grid_var.get().upper()
        
        # Find rover's bands
        rover = None
        for r in self.gc_rovers:
            if r['call'] == their_call:
                rover = r
                break
        
        if not rover:
            return
        
        # Find next unworked band
        mode = self.gc_mode_var.get()
        for band in rover['bands']:
            key = (my_grid, their_call, their_grid, band)
            if key not in self.gc_qsos:
                # This band not worked yet - set freq for it
                if mode == 'SSB':
                    freq = self.gc_ssb_freqs.get(band, '144.200')
                elif mode == 'FM':
                    freq = self.gc_fm_freqs.get(band, '146.520')
                else:
                    freq = self.gc_cw_freqs.get(band, '144.100')
                self.gc_freq_var.set(freq)
                self.gc_current_band = band
                return
    
    def _gc_add_rover(self):
        """Add a rover to the session"""
        call = self.gc_add_call_var.get().strip().upper()
        grid = self.gc_add_grid_var.get().strip().upper()
        
        if not call:
            messagebox.showwarning("Add Rover", "Please enter a callsign")
            return
        if not grid or len(grid) < 4:
            messagebox.showwarning("Add Rover", "Please enter a valid grid (e.g., EM15)")
            return
        
        # Get selected bands
        bands = [b for b, var in self.gc_add_band_vars.items() if var.get()]
        if not bands:
            messagebox.showwarning("Add Rover", "Please select at least one band")
            return
        
        # Check for duplicate
        for rover in self.gc_rovers:
            if rover['call'] == call:
                messagebox.showwarning("Add Rover", f"{call} is already in the session")
                return
        
        # Add rover
        self.gc_rovers.append({
            'call': call,
            'grid': grid[:4],
            'bands': bands
        })
        
        # Clear inputs
        self.gc_add_call_var.set("")
        self.gc_add_grid_var.set("")
        
        self._gc_refresh_rover_list()
        self._gc_update_progress()
    
    def _gc_remove_rover(self):
        """Remove selected rover from session"""
        selection = self.gc_rover_tree.selection()
        if not selection:
            return
        
        item = self.gc_rover_tree.item(selection[0])
        call = item['values'][0]
        
        self.gc_rovers = [r for r in self.gc_rovers if r['call'] != call]
        self._gc_refresh_rover_list()
        self._gc_update_progress()
        self._gc_update_band_buttons(None)
        self.gc_target_var.set("(select a rover)")
    
    def _gc_update_rover_grid(self):
        """Update selected rover's grid"""
        selection = self.gc_rover_tree.selection()
        if not selection:
            return
        
        item = self.gc_rover_tree.item(selection[0])
        call = item['values'][0]
        
        new_grid = simpledialog.askstring("Update Grid", f"New grid for {call}:",
                                          initialvalue=item['values'][1])
        if new_grid:
            for rover in self.gc_rovers:
                if rover['call'] == call:
                    rover['grid'] = new_grid.upper()[:4]
                    break
            self._gc_refresh_rover_list()
    
    def _gc_refresh_rover_list(self):
        """Refresh the rovers treeview"""
        # Remember current selection
        selection = self.gc_rover_tree.selection()
        selected_call = None
        if selection:
            item = self.gc_rover_tree.item(selection[0])
            selected_call = item['values'][0]
        
        # Clear and rebuild
        for item in self.gc_rover_tree.get_children():
            self.gc_rover_tree.delete(item)
        
        my_grid = self.gc_my_grid_var.get()
        
        new_selection = None
        for rover in self.gc_rovers:
            # Count worked and remaining
            worked = 0
            total = 0
            for band in rover['bands']:
                key = (my_grid, rover['call'], rover['grid'], band)
                total += 1
                if key in self.gc_qsos:
                    worked += 1
            
            remain = total - worked
            bands_str = ' '.join(rover['bands'])
            
            item_id = self.gc_rover_tree.insert('', 'end', values=(
                rover['call'], rover['grid'], bands_str, worked, remain
            ))
            
            # Remember if this was the selected one
            if rover['call'] == selected_call:
                new_selection = item_id
        
        # Restore selection
        if new_selection:
            self.gc_rover_tree.selection_set(new_selection)
            self.gc_rover_tree.see(new_selection)
    
    def _gc_on_rover_select(self, event):
        """Handle rover selection"""
        selection = self.gc_rover_tree.selection()
        if not selection:
            return
        
        item = self.gc_rover_tree.item(selection[0])
        call = item['values'][0]
        grid = item['values'][1]
        
        self.gc_target_var.set(f"{call} ({grid})")
        
        # Find rover data
        rover = None
        for r in self.gc_rovers:
            if r['call'] == call:
                rover = r
                break
        
        self._gc_update_band_buttons(rover)
        
        # Set freq to first unworked band's default
        if rover:
            my_grid = self.gc_my_grid_var.get().upper()
            their_grid = str(grid).upper()
            mode = self.gc_mode_var.get()
            
            for band in rover['bands']:
                key = (my_grid, call, their_grid, band)
                if key not in self.gc_qsos:
                    # First unworked band - set freq for it
                    if mode == 'SSB':
                        freq = self.gc_ssb_freqs.get(band, '144.200')
                    elif mode == 'FM':
                        freq = self.gc_fm_freqs.get(band, '146.520')
                    else:
                        freq = self.gc_cw_freqs.get(band, '144.100')
                    self.gc_freq_var.set(freq)
                    self.gc_current_band = band
                    break
    
    def _gc_update_band_buttons(self, rover):
        """Update band buttons based on selected rover"""
        my_grid = self.gc_my_grid_var.get().upper()  # Match case used in logging
        
        for band, btn in self.gc_band_buttons.items():
            if rover is None:
                btn.config(text=f"{band}\n---", state='disabled', bg='lightgray')
            elif band not in rover['bands']:
                btn.config(text=f"{band}\n--", state='disabled', bg='lightgray')
            else:
                their_grid = rover['grid'].upper()  # Match case used in logging
                key = (my_grid, rover['call'], their_grid, band)
                if key in self.gc_qsos:
                    btn.config(text=f"{band}\n✓", state='disabled', bg='lightgreen')
                else:
                    btn.config(text=f"{band}\n", state='normal', bg='white')
    
    def _gc_prev_rover(self):
        """Select previous rover in list"""
        selection = self.gc_rover_tree.selection()
        children = self.gc_rover_tree.get_children()
        if not children:
            return
        
        if not selection:
            self.gc_rover_tree.selection_set(children[-1])
        else:
            idx = children.index(selection[0])
            new_idx = (idx - 1) % len(children)
            self.gc_rover_tree.selection_set(children[new_idx])
        
        self._gc_on_rover_select(None)
    
    def _gc_next_rover(self):
        """Select next rover in list"""
        selection = self.gc_rover_tree.selection()
        children = self.gc_rover_tree.get_children()
        if not children:
            return
        
        if not selection:
            self.gc_rover_tree.selection_set(children[0])
        else:
            idx = children.index(selection[0])
            new_idx = (idx + 1) % len(children)
            self.gc_rover_tree.selection_set(children[new_idx])
        
        self._gc_on_rover_select(None)
    
    def _gc_log_qso(self, band):
        """Log a QSO with the selected rover on the given band"""
        import datetime
        
        selection = self.gc_rover_tree.selection()
        if not selection:
            return
        
        item = self.gc_rover_tree.item(selection[0])
        their_call = item['values'][0]
        their_grid = str(item['values'][1]).upper()  # Ensure uppercase
        my_grid = self.gc_my_grid_var.get().upper()  # Ensure uppercase
        
        # Check if already worked
        key = (my_grid, their_call, their_grid, band)
        if key in self.gc_qsos:
            return
        
        # Get mode
        mode = self.gc_mode_var.get()
        
        # Use the frequency from the entry box (allows user override)
        # If it doesn't look right for this band, use the default
        freq_str = self.gc_freq_var.get().strip()
        
        try:
            freq_mhz = float(freq_str)
        except ValueError:
            # Invalid freq, use default for band
            if mode == 'SSB':
                freq_str = self.gc_ssb_freqs.get(band, '144.200')
            elif mode == 'FM':
                freq_str = self.gc_fm_freqs.get(band, '146.520')
            else:
                freq_str = self.gc_cw_freqs.get(band, '144.100')
            freq_mhz = float(freq_str)
        
        # Update current band (for mode switching to know which band)
        self.gc_current_band = band
        
        # After logging, update freq box to default for NEXT band (the one after this)
        # This gives user a starting point for the next click
        self._gc_update_freq_for_next_band(band)
        
        # Log it
        timestamp = datetime.datetime.utcnow()
        self.gc_qsos[key] = timestamp
        
        # Add to session log tree
        time_str = timestamp.strftime('%H:%M:%S')
        self.gc_log_tree.insert('', 0, values=(
            time_str, my_grid, their_call, their_grid, band
        ))
        
        # Build QSO data structure (same format as Manual Entry)
        qso_data = {
            'dx_call': their_call,
            'dx_grid': their_grid,
            'mode': mode,
            'freq_mhz': freq_mhz,
            'freq_hz': int(freq_mhz * 1_000_000),
            'band': band,
            'report_sent': '59',
            'report_rcvd': '59',
            'datetime_on': timestamp,
            'datetime_off': timestamp,
            'my_call': self.config.get('my_call', ''),
            'my_grid': my_grid,
            'wsjtx_id': 'GridCorner',
        }
        
        # Update RoverQTH to our current grid before logging
        # (ensures N1MM+ uses correct grid as exchange, not county)
        if self.radio_updater:
            self.radio_updater.update_grid(my_grid)
        
        # Send to logger via the radio updater's relay queue (same as Manual Entry)
        if self.radio_updater:
            self.radio_updater.queue_qso_for_relay(qso_data)
            self._stamp_qso_location(qso_data)  # Add MY_STATE, MY_CNTY, etc.
            self.radio_updater._write_qso_to_adif(qso_data)
        
        # Update QSO display on main tab
        self.on_qso_logged(qso_data)
        
        # Refresh displays
        self._gc_refresh_rover_list()
        
        # Find rover to update buttons
        for r in self.gc_rovers:
            if r['call'] == their_call:
                self._gc_update_band_buttons(r)
                break
        
        self._gc_update_progress()
        
        # Alert
        self.add_alert(f"Grid Corner: {their_call} on {band} {mode} - {their_grid}")
        
        # Voice announcement handled by on_qso_logged() - no need to duplicate here
    
    def _gc_update_progress(self):
        """Update progress display"""
        total = 0
        worked = 0
        my_grid = self.gc_my_grid_var.get().upper()  # Match case used in logging
        
        for rover in self.gc_rovers:
            their_grid = rover['grid'].upper()  # Match case used in logging
            for band in rover['bands']:
                total += 1
                key = (my_grid, rover['call'], their_grid, band)
                if key in self.gc_qsos:
                    worked += 1
        
        self.gc_progress_var.set(f"QSOs: {worked} / {total}")
    
    def _gc_clear_log(self):
        """Clear the session log display"""
        for item in self.gc_log_tree.get_children():
            self.gc_log_tree.delete(item)
    
    def _gc_export_log(self):
        """Export session log to file"""
        if not self.gc_qsos:
            messagebox.showinfo("Export", "No QSOs to export")
            return
        
        from tkinter import filedialog
        import datetime
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"grid_corner_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        )
        
        if filename:
            try:
                with open(filename, 'w') as f:
                    f.write("Grid Corner Session Log\n")
                    f.write(f"Exported: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("=" * 50 + "\n\n")
                    
                    f.write("Rovers:\n")
                    for rover in self.gc_rovers:
                        f.write(f"  {rover['call']} - {rover['grid']} - {' '.join(rover['bands'])}\n")
                    f.write("\n")
                    
                    f.write("QSOs:\n")
                    for key, timestamp in sorted(self.gc_qsos.items(), key=lambda x: x[1]):
                        my_grid, their_call, their_grid, band = key
                        f.write(f"  {timestamp.strftime('%H:%M:%S')} | {my_grid} -> {their_call} ({their_grid}) | {band}\n")
                    
                    f.write(f"\nTotal: {len(self.gc_qsos)} QSOs\n")
                
                messagebox.showinfo("Export", f"Saved to {filename}")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))

    def create_gps_logger_tab(self, parent):
        """Create GPS Logger tab for recording tracks and waypoints"""
        frame = ttk.Frame(parent)
        
        # Initialize GPS logger state
        self.gps_logger_active = False
        self.gps_logger_paused = False
        self.gps_logger_file = None
        self.gps_logger_filepath = None
        self.gps_logger_point_count = 0
        
        # === Top: File Settings ===
        file_frame = ttk.LabelFrame(frame, text="Track File", padding=10)
        file_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(file_frame, text="Save to:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.gps_log_path_var = tk.StringVar(value=self.config.get('gps_log_path', 'logs/'))
        ttk.Entry(file_frame, textvariable=self.gps_log_path_var, width=40).grid(row=0, column=1, padx=5, pady=2)
        ttk.Button(file_frame, text="Browse...", command=self._browse_gps_log_path).grid(row=0, column=2, padx=5, pady=2)
        
        ttk.Label(file_frame, text="Filename:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.gps_log_filename_var = tk.StringVar(value=f"track_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        ttk.Entry(file_frame, textvariable=self.gps_log_filename_var, width=40).grid(row=1, column=1, padx=5, pady=2)
        
        # Control buttons
        btn_frame = ttk.Frame(file_frame)
        btn_frame.grid(row=2, column=0, columnspan=3, pady=10)
        
        self.gps_start_btn = ttk.Button(btn_frame, text="▶ Start Track", command=self._gps_logger_start, width=15)
        self.gps_start_btn.pack(side=tk.LEFT, padx=5)
        
        self.gps_pause_btn = ttk.Button(btn_frame, text="⏸ Pause", command=self._gps_logger_pause, width=15, state='disabled')
        self.gps_pause_btn.pack(side=tk.LEFT, padx=5)
        
        self.gps_stop_btn = ttk.Button(btn_frame, text="⏹ Stop & Save", command=self._gps_logger_stop, width=15, state='disabled')
        self.gps_stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Status
        self.gps_logger_status_var = tk.StringVar(value="Not recording")
        ttk.Label(file_frame, textvariable=self.gps_logger_status_var, foreground="gray").grid(row=3, column=0, columnspan=3, pady=5)
        
        # === Middle: GPS Data Display ===
        data_frame = ttk.LabelFrame(frame, text="Current GPS Data", padding=10)
        data_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Left column
        left_frame = ttk.Frame(data_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        # Position
        ttk.Label(left_frame, text="Position:", font=('TkDefaultFont', 9, 'bold')).grid(row=0, column=0, sticky=tk.W)
        self.gps_log_position_var = tk.StringVar(value="No GPS Lock")
        ttk.Label(left_frame, textvariable=self.gps_log_position_var, font=('Consolas', 11)).grid(row=0, column=1, sticky=tk.W, padx=10)
        
        # Grid
        ttk.Label(left_frame, text="Grid:", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=0, sticky=tk.W)
        self.gps_log_grid_var = tk.StringVar(value="------")
        ttk.Label(left_frame, textvariable=self.gps_log_grid_var, font=('Consolas', 14, 'bold')).grid(row=1, column=1, sticky=tk.W, padx=10)
        
        # County
        ttk.Label(left_frame, text="County:", font=('TkDefaultFont', 9, 'bold')).grid(row=2, column=0, sticky=tk.W)
        self.gps_log_county_var = tk.StringVar(value="---")
        ttk.Label(left_frame, textvariable=self.gps_log_county_var).grid(row=2, column=1, sticky=tk.W, padx=10)
        
        # Altitude
        ttk.Label(left_frame, text="Altitude:", font=('TkDefaultFont', 9, 'bold')).grid(row=3, column=0, sticky=tk.W)
        self.gps_log_altitude_var = tk.StringVar(value="--- ft")
        ttk.Label(left_frame, textvariable=self.gps_log_altitude_var).grid(row=3, column=1, sticky=tk.W, padx=10)
        
        # Right column
        right_frame = ttk.Frame(data_frame)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        # Time
        ttk.Label(right_frame, text="GPS Time:", font=('TkDefaultFont', 9, 'bold')).grid(row=0, column=0, sticky=tk.W)
        self.gps_log_time_var = tk.StringVar(value="--:--:--")
        ttk.Label(right_frame, textvariable=self.gps_log_time_var, font=('Consolas', 11)).grid(row=0, column=1, sticky=tk.W, padx=10)
        
        # Satellites
        ttk.Label(right_frame, text="Satellites:", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=0, sticky=tk.W)
        self.gps_log_sats_var = tk.StringVar(value="0")
        ttk.Label(right_frame, textvariable=self.gps_log_sats_var).grid(row=1, column=1, sticky=tk.W, padx=10)
        
        # Accuracy (HDOP)
        ttk.Label(right_frame, text="Accuracy:", font=('TkDefaultFont', 9, 'bold')).grid(row=2, column=0, sticky=tk.W)
        self.gps_log_accuracy_var = tk.StringVar(value="---")
        ttk.Label(right_frame, textvariable=self.gps_log_accuracy_var).grid(row=2, column=1, sticky=tk.W, padx=10)
        
        # Direction
        ttk.Label(right_frame, text="Heading:", font=('TkDefaultFont', 9, 'bold')).grid(row=3, column=0, sticky=tk.W)
        self.gps_log_heading_var = tk.StringVar(value="---")
        ttk.Label(right_frame, textvariable=self.gps_log_heading_var).grid(row=3, column=1, sticky=tk.W, padx=10)
        
        # Far right column
        right2_frame = ttk.Frame(data_frame)
        right2_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        # Speed
        ttk.Label(right2_frame, text="Speed:", font=('TkDefaultFont', 9, 'bold')).grid(row=0, column=0, sticky=tk.W)
        self.gps_log_speed_var = tk.StringVar(value="--- mph")
        ttk.Label(right2_frame, textvariable=self.gps_log_speed_var).grid(row=0, column=1, sticky=tk.W, padx=10)
        
        # Avg Speed
        ttk.Label(right2_frame, text="Avg Speed:", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=0, sticky=tk.W)
        self.gps_log_avgspeed_var = tk.StringVar(value="--- mph")
        ttk.Label(right2_frame, textvariable=self.gps_log_avgspeed_var).grid(row=1, column=1, sticky=tk.W, padx=10)
        
        # Distance
        ttk.Label(right2_frame, text="Distance:", font=('TkDefaultFont', 9, 'bold')).grid(row=2, column=0, sticky=tk.W)
        self.gps_log_distance_var = tk.StringVar(value="0.0 mi")
        ttk.Label(right2_frame, textvariable=self.gps_log_distance_var).grid(row=2, column=1, sticky=tk.W, padx=10)
        
        # Points logged
        ttk.Label(right2_frame, text="Points:", font=('TkDefaultFont', 9, 'bold')).grid(row=3, column=0, sticky=tk.W)
        self.gps_log_points_var = tk.StringVar(value="0")
        ttk.Label(right2_frame, textvariable=self.gps_log_points_var).grid(row=3, column=1, sticky=tk.W, padx=10)
        
        # === Bottom: Annotation ===
        annot_frame = ttk.LabelFrame(frame, text="Add Annotation / Waypoint", padding=10)
        annot_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Pre-made annotations
        ttk.Label(annot_frame, text="Quick Waypoints:").grid(row=0, column=0, sticky=tk.W, pady=5)
        
        presets_frame = ttk.Frame(annot_frame)
        presets_frame.grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=5)
        
        preset_annotations = [
            "Elev. Overpass", "Elev. Exit Ramp", "Elev. Shoulder", "Elev. Viewpoint",
            "Utility Driveway", "Field Driveway", "Mesa Edge", "Elev. Parking lot",
            "School", "Elev. Cemetery", "Rest Area", "Elev. Rest Area"
        ]
        
        for i, preset in enumerate(preset_annotations):
            btn = ttk.Button(presets_frame, text=preset, width=18,
                           command=lambda p=preset: self._add_gps_annotation(p))
            btn.grid(row=i//4, column=i%4, padx=2, pady=2)
        
        # Custom annotation
        ttk.Label(annot_frame, text="Custom:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.gps_annotation_var = tk.StringVar()
        ttk.Entry(annot_frame, textvariable=self.gps_annotation_var, width=50).grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=5)
        ttk.Button(annot_frame, text="Add Waypoint", command=lambda: self._add_gps_annotation(self.gps_annotation_var.get())).grid(row=3, column=3, padx=5)
        
        # Recent annotations list
        ttk.Label(annot_frame, text="Recent Waypoints:").grid(row=4, column=0, sticky=tk.NW, pady=5)
        self.gps_recent_annotations = tk.Text(annot_frame, height=4, width=80, state='disabled')
        self.gps_recent_annotations.grid(row=4, column=1, columnspan=3, sticky=tk.W, pady=5)
        
        return frame
    
    def _browse_gps_log_path(self):
        """Browse for GPS log directory"""
        path = filedialog.askdirectory(initialdir=self.gps_log_path_var.get())
        if path:
            self.gps_log_path_var.set(path)
    
    def _gps_logger_start(self):
        """Start GPS track logging"""
        # Create full filepath
        path = self.gps_log_path_var.get()
        filename = self.gps_log_filename_var.get()
        
        if not filename.endswith('.csv'):
            filename += '.csv'
        
        os.makedirs(path, exist_ok=True)
        self.gps_logger_filepath = os.path.join(path, filename)
        
        try:
            self.gps_logger_file = open(self.gps_logger_filepath, 'w', newline='')
            
            # Write CSV header
            header = "timestamp,lat,lon,altitude_ft,altitude_m,satellites,hdop,speed_mph,heading,compass,grid,state,county,annotation\n"
            self.gps_logger_file.write(header)
            self.gps_logger_file.flush()
            
            self.gps_logger_active = True
            self.gps_logger_paused = False
            self.gps_logger_point_count = 0
            
            # Reset track stats in GPS monitor
            if hasattr(self, 'gps_monitor') and self.gps_monitor:
                if hasattr(self.gps_monitor, 'reset_track_stats'):
                    self.gps_monitor.reset_track_stats()
            
            # Update UI
            self.gps_start_btn.config(state='disabled')
            self.gps_pause_btn.config(state='normal')
            self.gps_stop_btn.config(state='normal')
            self.gps_logger_status_var.set(f"Recording to: {filename}")
            self.gps_log_filename_var.set(f"track_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")  # Prep next filename
            
            print(f"GPS Logger: Started recording to {self.gps_logger_filepath}")
            
        except Exception as e:
            messagebox.showerror("GPS Logger", f"Failed to start logging: {e}")
    
    def _gps_logger_pause(self):
        """Pause/resume GPS track logging"""
        if self.gps_logger_paused:
            self.gps_logger_paused = False
            self.gps_pause_btn.config(text="⏸ Pause")
            self.gps_logger_status_var.set(f"Recording to: {os.path.basename(self.gps_logger_filepath)}")
        else:
            self.gps_logger_paused = True
            self.gps_pause_btn.config(text="▶ Resume")
            self.gps_logger_status_var.set("PAUSED")
    
    def _gps_logger_stop(self):
        """Stop GPS track logging and close file"""
        if self.gps_logger_file:
            self.gps_logger_file.close()
            self.gps_logger_file = None
            
            print(f"GPS Logger: Stopped. {self.gps_logger_point_count} points saved to {self.gps_logger_filepath}")
            messagebox.showinfo("GPS Logger", f"Track saved!\n\n{self.gps_logger_point_count} points\n{self.gps_logger_filepath}")
        
        self.gps_logger_active = False
        self.gps_logger_paused = False
        
        # Update UI
        self.gps_start_btn.config(state='normal')
        self.gps_pause_btn.config(state='disabled', text="⏸ Pause")
        self.gps_stop_btn.config(state='disabled')
        self.gps_logger_status_var.set("Not recording")
    
    def _add_gps_annotation(self, annotation):
        """Add an annotation/waypoint to the GPS log"""
        if not annotation or not annotation.strip():
            return
        
        annotation = annotation.strip()
        
        # Write to log file with current position
        self._write_gps_point(annotation=annotation)
        
        # Clear custom entry
        self.gps_annotation_var.set("")
        
        # Add to recent list
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.gps_recent_annotations.config(state='normal')
        self.gps_recent_annotations.insert('1.0', f"{timestamp}: {annotation}\n")
        self.gps_recent_annotations.config(state='disabled')
        
        # Voice confirmation
        if hasattr(self, 'voice') and self.voice:
            self.voice.announce(f"Waypoint added: {annotation}", category="gps_waypoint")
    
    def _write_gps_point(self, annotation=""):
        """Write a GPS point to the log file"""
        if not self.gps_logger_active or self.gps_logger_paused:
            return
        if not self.gps_logger_file:
            return
        
        # Get current GPS data
        lat = getattr(self, 'current_lat', None)
        lon = getattr(self, 'current_lon', None)
        
        if lat is None or lon is None:
            return
        
        # Get extended GPS data if available
        alt_ft = ""
        alt_m = ""
        sats = ""
        hdop = ""
        speed = ""
        heading = ""
        compass = ""
        grid = self.current_grid or ""
        
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            if hasattr(self.gps_monitor, 'get_full_data'):
                data = self.gps_monitor.get_full_data()
                if data:
                    alt_ft = f"{data['altitude_ft']:.0f}" if data.get('altitude_ft') else ""
                    alt_m = f"{data['altitude_m']:.0f}" if data.get('altitude_m') else ""
                    sats = str(data.get('satellites', ''))
                    hdop = f"{data['hdop']:.1f}" if data.get('hdop') else ""
                    speed = f"{data['speed_mph']:.1f}" if data.get('speed_mph') else ""
                    heading = f"{data['heading']:.0f}" if data.get('heading') else ""
                    compass = data.get('compass', '')
                    grid = data.get('grid_6char', grid)
        
        # Get county info
        state = ""
        county = ""
        if hasattr(self, 'current_county_info') and self.current_county_info:
            state = self.current_county_info.state_abbrev
            county = self.current_county_info.name
        
        # Build CSV line
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Escape annotation for CSV
        if ',' in annotation or '"' in annotation:
            annotation = f'"{annotation.replace(chr(34), chr(34)+chr(34))}"'
        
        line = f"{timestamp},{lat:.6f},{lon:.6f},{alt_ft},{alt_m},{sats},{hdop},{speed},{heading},{compass},{grid},{state},{county},{annotation}\n"
        
        try:
            self.gps_logger_file.write(line)
            self.gps_logger_file.flush()
            self.gps_logger_point_count += 1
            self.gps_log_points_var.set(str(self.gps_logger_point_count))
        except Exception as e:
            print(f"GPS Logger: Error writing point: {e}")
    
    def _update_gps_logger_display(self, data):
        """Update GPS Logger tab display with current GPS data"""
        if not hasattr(self, 'gps_log_position_var'):
            return  # Tab not created yet
            
        if data is None:
            # No data at all (GPS monitor not running)
            self.gps_log_position_var.set("GPS not connected")
            self.gps_log_grid_var.set("------")
            self.gps_log_time_var.set("--:--:--")
            self.gps_log_sats_var.set("0")
            self.gps_log_altitude_var.set("--- ft")
            self.gps_log_accuracy_var.set("---")
            self.gps_log_heading_var.set("---")
            self.gps_log_speed_var.set("--- mph")
            self.gps_log_avgspeed_var.set("--- mph")
            return

        if not data.get('has_fix', True):
            # Receiving NMEA but no lock yet — show satellite count
            sats = data.get('satellites', 0)
            if sats > 0:
                self.gps_log_position_var.set(f"Acquiring lock... ({sats} satellite{'s' if sats != 1 else ''})")
            else:
                self.gps_log_position_var.set("Searching for satellites...")
            self.gps_log_grid_var.set("------")
            self.gps_log_time_var.set("--:--:--")
            self.gps_log_sats_var.set(str(sats))
            self.gps_log_altitude_var.set("--- ft")
            self.gps_log_accuracy_var.set("---")
            self.gps_log_heading_var.set("---")
            self.gps_log_speed_var.set("--- mph")
            self.gps_log_avgspeed_var.set("--- mph")
            return
        
        # Update display
        self.gps_log_position_var.set(f"{data['lat']:.6f}, {data['lon']:.6f}")
        self.gps_log_grid_var.set(data.get('grid_6char', '------'))
        
        if data.get('gps_time'):
            self.gps_log_time_var.set(data['gps_time'].strftime("%H:%M:%S UTC"))
        
        self.gps_log_sats_var.set(str(data.get('satellites', 0)))
        
        if data.get('altitude_ft') is not None:
            self.gps_log_altitude_var.set(f"{data['altitude_ft']:.0f} ft ({data.get('altitude_m', 0):.0f} m)")
        
        if data.get('hdop') is not None:
            hdop = data['hdop']
            if hdop < 1:
                acc_text = f"{hdop:.1f} (Excellent)"
            elif hdop < 2:
                acc_text = f"{hdop:.1f} (Good)"
            elif hdop < 5:
                acc_text = f"{hdop:.1f} (Moderate)"
            else:
                acc_text = f"{hdop:.1f} (Poor)"
            self.gps_log_accuracy_var.set(acc_text)
        
        if data.get('heading') is not None:
            self.gps_log_heading_var.set(f"{data['heading']:.0f}° {data.get('compass', '')}")
        else:
            self.gps_log_heading_var.set(data.get('compass', '---'))
        
        if data.get('speed_mph') is not None:
            self.gps_log_speed_var.set(f"{data['speed_mph']:.1f} mph")
        
        if data.get('avg_speed_mph') is not None:
            self.gps_log_avgspeed_var.set(f"{data['avg_speed_mph']:.1f} mph")
        
        if data.get('total_distance_mi') is not None:
            self.gps_log_distance_var.set(f"{data['total_distance_mi']:.2f} mi")
        
        # Update county display
        if hasattr(self, 'current_county_info') and self.current_county_info:
            self.gps_log_county_var.set(f"{self.current_county_info.state_abbrev}: {self.current_county_info.name}")
        
        # Write point to log if active (every ~5 seconds to avoid huge files)
        if self.gps_logger_active and not self.gps_logger_paused:
            # Only log every 5 seconds
            if not hasattr(self, '_last_gps_log_time'):
                self._last_gps_log_time = 0
            
            import time
            now = time.time()
            if now - self._last_gps_log_time >= 5:
                self._write_gps_point()
                self._last_gps_log_time = now

    def _start_gps_logger_timer(self):
        """Start periodic timer to update GPS Logger display"""
        def update_gps_logger():
            if hasattr(self, 'gps_monitor') and self.gps_monitor:
                if hasattr(self.gps_monitor, 'get_full_data'):
                    full_data = self.gps_monitor.get_full_data()
                    self._update_gps_logger_display(full_data)
            # Periodically check intermittent mode suppression conditions
            if self.config.get('gps_time_sync_enabled', False):
                self._update_intermittent_state()
            # Schedule next update in 2 seconds
            self.root.after(2000, update_gps_logger)

        # Start the update loop
        self.root.after(2000, update_gps_logger)

    def create_aprs_messages_tab(self, parent):
        """Create APRS Messages tab for sending/receiving APRS messages"""
        frame = ttk.Frame(parent)
        
        # Top frame - Status and connection info
        status_frame = ttk.LabelFrame(frame, text="APRS-IS Connection", padding=10)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.aprs_msg_status_var = tk.StringVar(value="Not connected")
        ttk.Label(status_frame, textvariable=self.aprs_msg_status_var).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(status_frame, text="(Enable APRS in Settings or status bar to connect)", 
                 foreground='gray').pack(side=tk.LEFT, padx=10)
        
        # Middle frame - Message inbox
        inbox_frame = ttk.LabelFrame(frame, text="Received Messages", padding=10)
        inbox_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Message list (treeview)
        columns = ('time', 'from', 'message')
        self.aprs_inbox_tree = ttk.Treeview(inbox_frame, columns=columns, show='headings', height=8)
        
        self.aprs_inbox_tree.heading('time', text='Time')
        self.aprs_inbox_tree.heading('from', text='From')
        self.aprs_inbox_tree.heading('message', text='Message')
        
        self.aprs_inbox_tree.column('time', width=80)
        self.aprs_inbox_tree.column('from', width=100)
        self.aprs_inbox_tree.column('message', width=400)
        
        # Scrollbar
        inbox_scroll = ttk.Scrollbar(inbox_frame, orient=tk.VERTICAL, command=self.aprs_inbox_tree.yview)
        self.aprs_inbox_tree.configure(yscrollcommand=inbox_scroll.set)
        
        self.aprs_inbox_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inbox_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Clear button
        ttk.Button(inbox_frame, text="Clear Messages", 
                  command=self._clear_aprs_inbox).pack(pady=5)
        
        # Bottom frame - Compose message
        compose_frame = ttk.LabelFrame(frame, text="Send Message", padding=10)
        compose_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # To callsign
        ttk.Label(compose_frame, text="To:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.aprs_to_var = tk.StringVar()
        self.aprs_to_entry = ttk.Entry(compose_frame, textvariable=self.aprs_to_var, width=15)
        self.aprs_to_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        
        # Bind uppercase conversion
        self.aprs_to_var.trace('w', self._aprs_to_uppercase)
        
        # Message text
        ttk.Label(compose_frame, text="Message:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.aprs_msg_var = tk.StringVar()
        self.aprs_msg_entry = ttk.Entry(compose_frame, textvariable=self.aprs_msg_var, width=50)
        self.aprs_msg_entry.grid(row=1, column=1, columnspan=3, padx=5, pady=5, sticky=tk.W)
        
        # Bind Enter key to send
        self.aprs_msg_entry.bind('<Return>', lambda e: self._send_aprs_message())
        
        # Send button
        ttk.Button(compose_frame, text="Send", 
                  command=self._send_aprs_message).grid(row=1, column=4, padx=10, pady=5)
        
        # Quick replies
        quick_frame = ttk.Frame(compose_frame)
        quick_frame.grid(row=2, column=0, columnspan=5, pady=5, sticky=tk.W)
        
        ttk.Label(quick_frame, text="Quick:", foreground='gray').pack(side=tk.LEFT, padx=5)
        
        quick_messages = ["73", "QSL", "QRV?", "QSY?", "TNX"]
        for msg in quick_messages:
            ttk.Button(quick_frame, text=msg, width=6,
                      command=lambda m=msg: self._set_quick_aprs_msg(m)).pack(side=tk.LEFT, padx=2)
        
        return frame
    
    def _aprs_to_uppercase(self, *args):
        """Convert APRS To field to uppercase"""
        current = self.aprs_to_var.get()
        upper = current.upper()
        if current != upper:
            self.aprs_to_var.set(upper)
    
    def _clear_aprs_inbox(self):
        """Clear APRS inbox messages"""
        self.aprs_inbox_tree.delete(*self.aprs_inbox_tree.get_children())
    
    def _set_quick_aprs_msg(self, msg):
        """Set quick reply message"""
        self.aprs_msg_var.set(msg)
        self.aprs_msg_entry.focus()
    
    def _send_aprs_message(self):
        """Send APRS message"""
        to_call = self.aprs_to_var.get().strip()
        message = self.aprs_msg_var.get().strip()
        
        if not to_call:
            messagebox.showwarning("APRS Message", "Please enter a callsign")
            return
        
        if not message:
            messagebox.showwarning("APRS Message", "Please enter a message")
            return
        
        if not self.aprs_client:
            messagebox.showwarning("APRS Message", "APRS is not connected. Enable APRS first.")
            return
        
        if not self.aprs_client.connected:
            messagebox.showwarning("APRS Message", "APRS is not connected. Check your connection.")
            return
        
        # Send the message
        if self.aprs_client.send_message(to_call, message):
            # Add to inbox as sent message
            time_str = datetime.now().strftime('%H:%M:%S')
            self.aprs_inbox_tree.insert('', 0, values=(
                time_str,
                f"To: {to_call}",
                message
            ))
            
            self.add_alert(f"APRS: Sent to {to_call}: {message}")
            
            # Clear message field (keep To field for follow-ups)
            self.aprs_msg_var.set('')
        else:
            messagebox.showerror("APRS Message", "Failed to send message")
    
    def _on_aprs_message_received(self, from_call, message, msgno):
        """Handle received APRS message - called from APRS client thread"""
        # Schedule UI update on main thread
        self.root.after(0, lambda: self._display_aprs_message(from_call, message, msgno))
    
    def _display_aprs_message(self, from_call, message, msgno):
        """Display received APRS message in inbox (main thread)"""
        time_str = datetime.now().strftime('%H:%M:%S')
        
        # Add to inbox
        self.aprs_inbox_tree.insert('', 0, values=(
            time_str,
            from_call,
            message
        ))
        
        # Alert
        self.add_alert(f"📩 APRS from {from_call}: {message}", priority=True)
        
        # Voice announcement
        if self.voice:
            self.voice.announce(f"APRS message from {from_call}", category="aprs_message")
        
        # Auto-fill reply To field
        self.aprs_to_var.set(from_call)

    def create_qsy_advisor_tab(self, parent):
        """Create QSY Advisor tab for browsing station band database"""
        frame = ttk.Frame(parent)
        
        # Search/filter frame at top
        search_frame = ttk.LabelFrame(frame, text="Search Stations", padding=10)
        search_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(search_frame, text="Callsign:").grid(row=0, column=0, padx=5, pady=5)
        self.qsy_search_var = tk.StringVar()
        self.qsy_search_var.trace('w', self.filter_qsy_stations)
        search_entry = ttk.Entry(search_frame, textvariable=self.qsy_search_var, width=15)
        search_entry.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(search_frame, text="Grid:").grid(row=0, column=2, padx=5, pady=5)
        self.qsy_grid_filter_var = tk.StringVar()
        self.qsy_grid_filter_var.trace('w', self.filter_qsy_stations)
        grid_entry = ttk.Entry(search_frame, textvariable=self.qsy_grid_filter_var, width=8)
        grid_entry.grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Label(search_frame, text="Min Bands:").grid(row=0, column=4, padx=5, pady=5)
        self.qsy_minbands_var = tk.StringVar(value="1")
        minbands_spin = ttk.Spinbox(search_frame, from_=1, to=10, width=5, 
                                    textvariable=self.qsy_minbands_var,
                                    command=self.filter_qsy_stations)
        minbands_spin.grid(row=0, column=5, padx=5, pady=5)
        
        ttk.Button(search_frame, text="Refresh", 
                   command=self.refresh_qsy_database).grid(row=0, column=6, padx=5, pady=5)
        
        ttk.Button(search_frame, text="Add Station", 
                   command=self._qsy_add_station).grid(row=0, column=7, padx=5, pady=5)
        
        ttk.Button(search_frame, text="Fetch ARRL Logs", 
                   command=self._qsy_fetch_arrl).grid(row=0, column=8, padx=5, pady=5)
        
        # Stats label
        self.qsy_stats_var = tk.StringVar(value="Stations: 0")
        ttk.Label(search_frame, textvariable=self.qsy_stats_var, 
                  font=('Arial', 10, 'bold')).grid(row=0, column=9, padx=10, pady=5)
        
        # Last refreshed date (row 1, spans columns)
        self.qsy_db_date_var = tk.StringVar(value="Database: --")
        ttk.Label(search_frame, textvariable=self.qsy_db_date_var,
                  foreground='gray').grid(row=1, column=0, columnspan=10, sticky=tk.W, padx=5)
        
        # Station database treeview
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        columns = ('call', 'bands', 'grids', 'band_count', 'dist', 'dir', 'last_seen')
        self.qsy_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=20)
        
        # Column headers with sorting
        self.qsy_tree.heading('call', text='Callsign', command=lambda: self.sort_qsy_column('call'))
        self.qsy_tree.heading('bands', text='Bands', command=lambda: self.sort_qsy_column('bands'))
        self.qsy_tree.heading('grids', text='Grids', command=lambda: self.sort_qsy_column('grids'))
        self.qsy_tree.heading('band_count', text='# Bands', command=lambda: self.sort_qsy_column('band_count'))
        self.qsy_tree.heading('dist', text='Dist (mi)', command=lambda: self.sort_qsy_column('dist'))
        self.qsy_tree.heading('dir', text='Dir', command=lambda: self.sort_qsy_column('dir'))
        self.qsy_tree.heading('last_seen', text='Last Seen', command=lambda: self.sort_qsy_column('last_seen'))
        
        # Column widths
        self.qsy_tree.column('call', width=100)
        self.qsy_tree.column('bands', width=300)
        self.qsy_tree.column('grids', width=80)
        self.qsy_tree.column('band_count', width=60)
        self.qsy_tree.column('dist', width=70)
        self.qsy_tree.column('dir', width=40)
        self.qsy_tree.column('last_seen', width=80)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.qsy_tree.yview)
        self.qsy_tree.configure(yscrollcommand=scrollbar.set)
        
        self.qsy_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Detail view when station selected
        detail_frame = ttk.LabelFrame(frame, text="Station Details (double-click to open QRZ)", padding=10)
        detail_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.qsy_detail_var = tk.StringVar(value="Select a station to see details")
        ttk.Label(detail_frame, textvariable=self.qsy_detail_var, 
                  font=('Courier', 10)).pack(fill=tk.X)
        
        # Bind selection and double-click
        self.qsy_tree.bind('<<TreeviewSelect>>', self.on_qsy_station_select)
        self.qsy_tree.bind('<Double-1>', self._qsy_open_qrz)
        
        # Track sort state
        self.qsy_sort_column = 'call'
        self.qsy_sort_reverse = False
        
        # Initial load
        self.root.after(500, self.refresh_qsy_database)
        
        return frame
    
    def refresh_qsy_database(self):
        """Load/refresh the QSY Advisor station database"""
        if not self.qsy_advisor:
            return
        
        # Update database date display
        self._update_qsy_db_date()
        
        # Just apply filters which will re-populate with all data
        self.filter_qsy_stations()
    
    def _update_qsy_db_date(self):
        """Update the database date display"""
        import os
        from datetime import datetime
        
        if not self.qsy_advisor:
            return
        
        db_path = self.qsy_advisor.data_dir / 'station_bands.json'
        if db_path.exists():
            # Get file modification time
            mtime = os.path.getmtime(db_path)
            date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
            self.qsy_db_date_var.set(f"Database last updated: {date_str}")
        else:
            self.qsy_db_date_var.set("Database: Not found - click 'Fetch ARRL Logs'")
    
    def filter_qsy_stations(self, *args):
        """Filter the QSY station display based on search criteria"""
        if not self.qsy_advisor:
            return
        
        search_call = self.qsy_search_var.get().upper().strip()
        search_grid = self.qsy_grid_filter_var.get().upper().strip()
        try:
            min_bands = int(self.qsy_minbands_var.get())
        except:
            min_bands = 1
        
        # Clear current display
        for item in self.qsy_tree.get_children():
            self.qsy_tree.delete(item)
        
        stations = self.qsy_advisor.stations
        
        # Get my position for distance calculation
        my_lat, my_lon = None, None
        if self.current_grid and self.current_grid != "----":
            my_lat, my_lon = self._grid_to_latlon(self.current_grid)
        
        # Band names - wavelength order (longest to shortest)
        band_names = {
            '50': '6m', '144': '2m', '222': '1.25m', '432': '70cm',
            '902': '33cm', '1296': '23cm', '2304': '13cm', '3456': '9cm',
            '5760': '5cm', '10368': '3cm', '10G': '3cm',
            '24G': '1.2cm', '47G': '6mm', '78G': '4mm'
        }
        
        # Sort order for bands (by wavelength, longest first)
        band_order = ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10368', '10G', '24G', '47G', '78G']
        
        def sort_bands(bands):
            """Sort bands by wavelength order"""
            return sorted(bands, key=lambda b: band_order.index(b) if b in band_order else 99)
        
        filtered_count = 0
        for call, info in stations.items():
            bands = info.get('bands', [])
            grids = info.get('grids', [])
            last_seen = info.get('last_seen', '')
            
            # Apply filters
            if search_call and search_call not in call:
                continue
            if search_grid and not any(search_grid in g for g in grids):
                continue
            if len(bands) < min_bands:
                continue
            
            # Sort bands by wavelength and convert to names
            sorted_bands = sort_bands(bands)
            band_str = ', '.join([band_names.get(b, b) for b in sorted_bands])
            grid_str = ', '.join(sorted(grids)) if grids else ''
            
            # Calculate distance and direction to first grid
            dist_str = '--'
            dir_str = '--'
            if my_lat and my_lon and grids:
                # grids is a set, convert to list to access first element
                first_grid = list(grids)[0] if grids else None
                if first_grid:
                    their_lat, their_lon = self._grid_to_latlon(first_grid)
                    if their_lat and their_lon:
                        dist = self._haversine(my_lat, my_lon, their_lat, their_lon)
                        bearing = self._bearing(my_lat, my_lon, their_lat, their_lon)
                        dist_str = str(int(dist))
                        dir_str = self._bearing_to_compass(bearing)
            
            self.qsy_tree.insert('', 'end', values=(
                call,
                band_str,
                grid_str,
                len(bands),
                dist_str,
                dir_str,
                last_seen
            ))
            filtered_count += 1
        
        self.qsy_stats_var.set(f"Stations: {filtered_count} / {len(stations)}")
    
    def sort_qsy_column(self, col):
        """Sort QSY database by column"""
        # Toggle sort direction if same column
        if self.qsy_sort_column == col:
            self.qsy_sort_reverse = not self.qsy_sort_reverse
        else:
            self.qsy_sort_column = col
            self.qsy_sort_reverse = False
        
        # Get all items
        items = [(self.qsy_tree.set(item, col), item) for item in self.qsy_tree.get_children('')]
        
        # Sort - handle numeric columns
        if col in ['band_count', 'dist']:
            # Numeric sort - treat '--' as infinity
            def num_key(x):
                try:
                    return int(x[0])
                except:
                    return 999999
            items.sort(key=num_key, reverse=self.qsy_sort_reverse)
        else:
            items.sort(key=lambda x: x[0], reverse=self.qsy_sort_reverse)
        
        # Rearrange items
        for index, (val, item) in enumerate(items):
            self.qsy_tree.move(item, '', index)
    
    def on_qsy_station_select(self, event):
        """Show details for selected station"""
        selection = self.qsy_tree.selection()
        if not selection:
            return
        
        item = selection[0]
        call = self.qsy_tree.item(item, 'values')[0]
        
        if call in self.qsy_advisor.stations:
            info = self.qsy_advisor.stations[call]
            bands = info.get('bands', [])
            grids = info.get('grids', [])
            contests = info.get('contests', [])
            notes = info.get('notes', '')
            
            # Check what we've worked this contest
            worked_info = ""
            if call in self.qsy_advisor.current_contest:
                worked_grids = self.qsy_advisor.current_contest[call]
                worked_parts = []
                for grid, worked_bands in worked_grids.items():
                    band_names = [self.qsy_advisor.BAND_NAMES.get(b, b) for b in worked_bands]
                    worked_parts.append(f"{grid}: {', '.join(band_names)}")
                worked_info = f"\nWorked this contest: {'; '.join(worked_parts)}"
            
            details = f"{call}: {len(bands)} bands | Grids: {', '.join(grids) if grids else 'Unknown'}"
            if contests:
                details += f" | Contests: {', '.join(contests[-3:])}"  # Last 3 contests
            if notes:
                details += f" | {notes}"
            details += worked_info
            
            self.qsy_detail_var.set(details)
        else:
            self.qsy_detail_var.set(f"{call}: Not in database")
    
    def _qsy_open_qrz(self, event):
        """Open QRZ page for selected station"""
        import webbrowser
        
        selection = self.qsy_tree.selection()
        if not selection:
            return
        
        item = selection[0]
        call = self.qsy_tree.item(item, 'values')[0]
        
        if call:
            url = f"https://www.qrz.com/db/{call}"
            webbrowser.open(url)
    
    def _qsy_add_station(self):
        """Add a station manually to the QSY database"""
        # Create dialog window
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Station to QSY Database")
        dialog.geometry("400x350")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Callsign
        ttk.Label(dialog, text="Callsign:").grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)
        call_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=call_var, width=15).grid(row=0, column=1, padx=10, pady=5)
        
        # Grid
        ttk.Label(dialog, text="Grid (4-char):").grid(row=1, column=0, padx=10, pady=5, sticky=tk.W)
        grid_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=grid_var, width=10).grid(row=1, column=1, padx=10, pady=5, sticky=tk.W)
        
        # Bands
        ttk.Label(dialog, text="Bands:").grid(row=2, column=0, padx=10, pady=5, sticky=tk.NW)
        bands_frame = ttk.Frame(dialog)
        bands_frame.grid(row=2, column=1, padx=10, pady=5, sticky=tk.W)
        
        band_vars = {}
        band_list = [('6m', '50'), ('2m', '144'), ('1.25m', '222'), ('70cm', '432'),
                     ('33cm', '902'), ('23cm', '1296'), ('13cm', '2304'), ('9cm', '3456'),
                     ('6cm', '5760'), ('3cm', '10368')]
        
        for i, (name, code) in enumerate(band_list):
            var = tk.BooleanVar()
            band_vars[code] = var
            ttk.Checkbutton(bands_frame, text=name, variable=var).grid(row=i//5, column=i%5, sticky=tk.W)
        
        # Notes
        ttk.Label(dialog, text="Notes:").grid(row=3, column=0, padx=10, pady=5, sticky=tk.W)
        notes_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=notes_var, width=30).grid(row=3, column=1, padx=10, pady=5)
        
        def save_station():
            call = call_var.get().strip().upper()
            grid = grid_var.get().strip().upper()
            notes = notes_var.get().strip()
            
            if not call:
                messagebox.showwarning("Add Station", "Please enter a callsign")
                return
            
            # Get selected bands
            bands = [code for code, var in band_vars.items() if var.get()]
            if not bands:
                messagebox.showwarning("Add Station", "Please select at least one band")
                return
            
            # Add to database
            if self.qsy_advisor:
                if call not in self.qsy_advisor.stations:
                    self.qsy_advisor.stations[call] = {
                        'bands': set(),
                        'grids': set(),
                        'last_seen': '',
                        'contests': [],
                        'notes': ''
                    }
                
                self.qsy_advisor.stations[call]['bands'].update(bands)
                if grid and len(grid) >= 4:
                    self.qsy_advisor.stations[call]['grids'].add(grid[:4])
                if notes:
                    self.qsy_advisor.stations[call]['notes'] = notes
                
                import datetime
                self.qsy_advisor.stations[call]['last_seen'] = datetime.datetime.now().strftime('%Y-%m')
                
                # Save database
                self.qsy_advisor.save_database()
                
                self.add_alert(f"QSY Database: Added {call}")
                self.refresh_qsy_database()
                dialog.destroy()
        
        ttk.Button(dialog, text="Save", command=save_station).grid(row=4, column=0, columnspan=2, pady=20)
    
    def _qsy_fetch_arrl(self):
        """Fetch and parse ARRL public contest logs"""
        import threading
        
        # Confirm with user
        if not messagebox.askyesno("Fetch ARRL Logs", 
            "This will fetch public logs from ARRL for:\n"
            "- January VHF Contest\n"
            "- June VHF Contest\n"
            "- September VHF Contest\n"
            "- 222 MHz and Up Distance Contest\n\n"
            "This may take a few minutes.\n\n"
            "NOTE: Only refresh when ARRL publishes new contest results,\n"
            "typically a few weeks before the next contest.\n\n"
            "Continue?"):
            return
        
        self.add_alert("QSY Database: Fetching ARRL public logs...")
        
        # Get the database path BEFORE starting thread
        db_path = str(self.qsy_advisor.data_dir / 'station_bands.json')
        self.add_alert(f"Database path: {db_path}")
        
        # Run in background thread
        def fetch_thread():
            try:
                import urllib.request
                import re
                import json
                from datetime import datetime
                
                # Import the parser function only
                from tools.parse_public_logs import parse_cabrillo_log
                
                contests = [
                    ('janvhf', 'January VHF'),
                    ('junvhf', 'June VHF'),
                    ('sepvhf', 'September VHF'),
                    ('222', '222 MHz and Up'),
                ]
                
                all_parsed = []
                
                for contest_code, contest_name in contests:
                    try:
                        # Get contest listing page
                        list_url = f"https://contests.arrl.org/publiclogs.php?cn={contest_code}"
                        self.root.after(0, lambda m=f"Fetching {contest_name} log list...": self.add_alert(m))
                        
                        req = urllib.request.Request(list_url, headers={'User-Agent': 'N5ZY-CoPilot/1.0'})
                        with urllib.request.urlopen(req, timeout=30) as response:
                            html = response.read().decode('utf-8')
                        
                        # Find the most recent year's link
                        # Pattern: publiclogs.php?eid=XX&iid=YYYY
                        year_links = re.findall(r'publiclogs\.php\?eid=(\d+)&amp;iid=(\d+)', html)
                        if not year_links:
                            # Try without &amp;
                            year_links = re.findall(r'publiclogs\.php\?eid=(\d+)&iid=(\d+)', html)
                        
                        if not year_links:
                            self.root.after(0, lambda m=f"No logs found for {contest_name}": self.add_alert(m))
                            continue
                        
                        # Get the first (most recent) one
                        eid, iid = year_links[0]
                        logs_url = f"https://contests.arrl.org/publiclogs.php?eid={eid}&iid={iid}"
                        
                        req = urllib.request.Request(logs_url, headers={'User-Agent': 'N5ZY-CoPilot/1.0'})
                        with urllib.request.urlopen(req, timeout=30) as response:
                            logs_html = response.read().decode('utf-8')
                        
                        # Find all callsign links
                        # Pattern: showpubliclog.php?q=XXXX">CALLSIGN</a>
                        call_links = re.findall(r'showpubliclog\.php\?q=([^"]+)"[^>]*>([A-Z0-9/]+)</a>', logs_html)
                        
                        self.root.after(0, lambda m=f"{contest_name}: Fetching {len(call_links)} logs...": self.add_alert(m))
                        
                        # Fetch ALL logs
                        contest_parsed = 0
                        for i, (log_key, callsign) in enumerate(call_links):
                            try:
                                log_url = f"https://contests.arrl.org/showpubliclog.php?q={log_key}"
                                req = urllib.request.Request(log_url, headers={'User-Agent': 'N5ZY-CoPilot/1.0'})
                                with urllib.request.urlopen(req, timeout=15) as response:
                                    log_text = response.read().decode('utf-8', errors='ignore')
                                
                                parsed = parse_cabrillo_log(log_text)
                                # Only keep stations with 3+ bands (the interesting ones for QSY)
                                if parsed['callsign'] and len(parsed['bands']) >= 3:
                                    all_parsed.append(parsed)
                                    contest_parsed += 1
                                
                                # Progress update every 50 logs
                                if (i + 1) % 50 == 0:
                                    self.root.after(0, lambda m=f"{contest_name}: {i+1}/{len(call_links)} processed...": self.add_alert(m))
                                
                            except Exception as e:
                                pass  # Skip individual log errors
                        
                        self.root.after(0, lambda m=f"{contest_name}: {contest_parsed} stations with 3+ bands": self.add_alert(m))
                        
                    except Exception as e:
                        self.root.after(0, lambda m=f"Error fetching {contest_name}: {e}": self.add_alert(m))
                
                # Update database - do it inline to avoid import issues
                if all_parsed:
                    self.root.after(0, lambda: self.add_alert(f"Saving {len(all_parsed)} stations to: {db_path}"))
                    
                    try:
                        # Load existing database
                        import os
                        if os.path.exists(db_path):
                            with open(db_path, 'r') as f:
                                database = json.load(f)
                        else:
                            database = {}
                        
                        # Update with new data
                        stations_added = 0
                        stations_updated = 0
                        
                        for log in all_parsed:
                            call = log['callsign']
                            
                            if call not in database:
                                database[call] = {
                                    'bands': [],
                                    'grids': [],
                                    'last_seen': '',
                                    'contests': []
                                }
                                stations_added += 1
                            else:
                                stations_updated += 1
                            
                            # Merge bands
                            existing_bands = set(database[call].get('bands', []))
                            existing_bands.update(log['bands'])
                            database[call]['bands'] = sorted(list(existing_bands), 
                                                             key=lambda b: int(b) if b.isdigit() else 0)
                            
                            # Merge grids
                            existing_grids = set(database[call].get('grids', []))
                            if log['grid']:
                                existing_grids.add(log['grid'])
                            database[call]['grids'] = list(existing_grids)
                            
                            # Update last seen
                            database[call]['last_seen'] = datetime.now().strftime('%Y-%m')
                            
                            # Add contest if known
                            if log.get('contest'):
                                contests_list = database[call].get('contests', [])
                                if log['contest'] not in contests_list:
                                    contests_list.append(log['contest'])
                                database[call]['contests'] = contests_list
                        
                        # Save database
                        with open(db_path, 'w') as f:
                            json.dump(database, f, indent=2)
                        
                        self.root.after(0, lambda a=stations_added, u=stations_updated, t=len(database): 
                            self.add_alert(f"QSY Database updated: {a} new, {u} updated, {t} total"))
                        
                        # Reload the QSY advisor and refresh display
                        self.root.after(0, lambda: self.qsy_advisor._load_database())
                        self.root.after(100, self.refresh_qsy_database)
                        
                    except Exception as e:
                        self.root.after(0, lambda err=str(e): self.add_alert(f"Save error: {err}"))
                else:
                    self.root.after(0, lambda: self.add_alert("QSY Database: No logs parsed"))
                    
            except Exception as e:
                self.root.after(0, lambda: self.add_alert(f"QSY Database fetch error: {e}"))
        
        threading.Thread(target=fetch_thread, daemon=True).start()

    def reload_contest_log(self):
        """Reload ADIF logs from contest period to restore QSO tracking after restart
        
        Loads all ADIF files from the last 4 days to cover a full contest weekend.
        Tracks QSOs by (my_grid, band, their_call) to properly handle rover dupes.
        """
        import re
        import glob
        from datetime import datetime, timedelta
        
        log_dir = os.path.join(os.path.dirname(__file__), 'logs')
        
        if not os.path.exists(log_dir):
            self.add_alert("No logs directory found")
            self.voice.announce("No logs directory found", category="operational")
            return
        
        # Find all ADIF files from the last 4 days (covers full contest weekend)
        adif_files = []
        today = datetime.now()
        for days_ago in range(4):  # Today + 3 previous days
            date_str = (today - timedelta(days=days_ago)).strftime('%Y%m%d')
            adif_path = os.path.join(log_dir, f'n5zy_copilot_{date_str}.adi')
            if os.path.exists(adif_path):
                adif_files.append(adif_path)
        
        if not adif_files:
            self.add_alert("No recent log files found (last 4 days)")
            self.voice.announce("No recent log files found", category="operational")
            return
        
        # Sort by date (oldest first so we process in chronological order)
        adif_files.sort()
        
        try:
            # Clear current display
            self.clear_qso_display()
            
            # Reset QSY Advisor tracking
            if self.qsy_advisor:
                self.qsy_advisor.start_contest()
            
            qso_count = 0
            files_loaded = 0
            
            for adif_path in adif_files:
                with open(adif_path, 'r') as f:
                    content = f.read()
                
                # Parse ADIF records
                records = re.split(r'<eor>|<EOR>', content, flags=re.IGNORECASE)
                
                for record in records:
                    if not record.strip():
                        continue
                    
                    # Extract fields
                    call_match = re.search(r'<call:(\d+)>([^<]+)', record, re.IGNORECASE)
                    band_match = re.search(r'<band:(\d+)>([^<]+)', record, re.IGNORECASE)
                    mode_match = re.search(r'<mode:(\d+)>([^<]+)', record, re.IGNORECASE)
                    gridsquare_match = re.search(r'<gridsquare:(\d+)>([^<]+)', record, re.IGNORECASE)
                    my_grid_match = re.search(r'<my_gridsquare:(\d+)>([^<]+)', record, re.IGNORECASE)
                    time_match = re.search(r'<time_on:(\d+)>([^<]+)', record, re.IGNORECASE)
                    date_match = re.search(r'<qso_date:(\d+)>([^<]+)', record, re.IGNORECASE)
                    
                    if call_match:
                        callsign = call_match.group(2).strip()
                        band = band_match.group(2).strip() if band_match else "?"
                        mode = mode_match.group(2).strip() if mode_match else "?"
                        their_grid = gridsquare_match.group(2).strip() if gridsquare_match else ""
                        my_grid = my_grid_match.group(2).strip() if my_grid_match else ""
                        time_str = time_match.group(2).strip() if time_match else ""
                        date_str = date_match.group(2).strip() if date_match else ""
                        
                        # Format time for display (include date for multi-day)
                        if len(time_str) >= 4:
                            time_display = f"{time_str[:2]}:{time_str[2:4]}"
                        else:
                            time_display = time_str
                        
                        # Add date prefix if we have multiple days
                        if date_str and len(adif_files) > 1:
                            # Show as MM-DD HH:MM
                            if len(date_str) >= 8:
                                time_display = f"{date_str[4:6]}-{date_str[6:8]} {time_display}"
                        
                        # Determine source from filename
                        source = os.path.basename(adif_path).replace('n5zy_copilot_', '').replace('.adi', '')
                        
                        # Add to display
                        self.qso_tree.insert('', 'end', values=(
                            time_display,
                            callsign,
                            their_grid,
                            band,
                            mode,
                            my_grid[:4] if my_grid else "",
                            f"ADIF:{source}"  # Source shows which log file
                        ))
                        
                        # Update QSY Advisor tracking (per-grid tracking)
                        if self.qsy_advisor and my_grid:
                            # Convert band string to MHz for QSY Advisor
                            band_mhz = self._band_to_mhz(band)
                            if band_mhz:
                                self.qsy_advisor.log_qso(callsign, band_mhz, 
                                                        grid=their_grid, 
                                                        my_grid=my_grid,
                                                        suppress_alert=True)
                        
                        # Add to worked_calls for SCP matching
                        self.worked_calls.add(callsign.upper())
                        
                        qso_count += 1
                
                files_loaded += 1
            
            # Update count
            self.qso_count = qso_count
            self.qso_count_var.set(f"QSOs: {qso_count}")
            
            # Restore current grid
            if self.qsy_advisor and self.current_grid:
                self.qsy_advisor.set_my_grid(self.current_grid)
            
            # Scroll to bottom to show latest
            if self.qso_tree.get_children():
                self.qso_tree.see(self.qso_tree.get_children()[-1])
            
            self.add_alert(f"Reloaded {qso_count} QSOs from {files_loaded} log file(s)")
            self.voice.announce(f"Reloaded {qso_count} QSOs from {files_loaded} days", category="operational")
            
        except Exception as e:
            self.add_alert(f"Error reloading logs: {e}")
            self.voice.announce("Error reloading logs", category="operational")
    
    def _band_to_mhz(self, band_str):
        """Convert ADIF band string to MHz for QSY Advisor"""
        band_map = {
            '6m': '50', '6M': '50',
            '2m': '144', '2M': '144',
            '1.25m': '222', '1.25M': '222',
            '70cm': '432', '70CM': '432',
            '33cm': '902', '33CM': '902',
            '23cm': '1296', '23CM': '1296',
            '13cm': '2304', '13CM': '2304',
            '9cm': '3456', '9CM': '3456',
            '6cm': '5760', '6CM': '5760',
            '3cm': '10368', '3CM': '10368',
        }
        return band_map.get(band_str.strip())
    
    def clear_qso_display(self):
        """Clear the QSO display (does not affect ADIF file)"""
        if self.qso_count > 0:
            result = messagebox.askyesno("Clear All QSOs", 
                f"Clear all {self.qso_count} QSOs from display?\n\n"
                "(ADIF log file is not affected)")
            if not result:
                return
        
        for item in self.qso_tree.get_children():
            self.qso_tree.delete(item)
        self.qso_count = 0
        self.qso_count_var.set("QSOs: 0")
        self.add_alert("QSO display cleared")
    
    def delete_selected_qso(self):
        """Delete selected QSO(s) from display and ADIF file"""
        import datetime
        
        selected = self.qso_tree.selection()
        if not selected:
            messagebox.showinfo("No Selection", "Please select a QSO to delete")
            return
        
        # Get info for confirmation - group by ADIF file
        count = len(selected)
        qsos_by_file = {}  # {adif_date: [qso_list]}
        
        for item in selected:
            values = self.qso_tree.item(item)['values']
            # values = (time, call, grid, band, mode, my_grid, source)
            if len(values) >= 7:
                source = str(values[6])
                # Source is like "ADIF:20260217" or "WSJT-X - ic9700" or "Manual"
                if source.startswith('ADIF:'):
                    adif_date = source.replace('ADIF:', '')
                else:
                    # Live QSO - use today's date
                    adif_date = datetime.datetime.now().strftime('%Y%m%d')
                
                if adif_date not in qsos_by_file:
                    qsos_by_file[adif_date] = []
                
                qsos_by_file[adif_date].append({
                    'time': str(values[0]),
                    'call': str(values[1]),
                    'band': str(values[3]),
                    'mode': str(values[4])
                })
        
        if count == 1:
            first_qso = list(qsos_by_file.values())[0][0] if qsos_by_file else {}
            call = first_qso.get('call', '?')
            msg = f"Delete QSO with {call}?\n\nThis will remove from display and ADIF log."
        else:
            msg = f"Delete {count} selected QSOs?\n\nThis will remove from display and ADIF log(s)."
        
        result = messagebox.askyesno("Delete QSO", msg)
        if not result:
            return
        
        # Delete from display
        for item in selected:
            self.qso_tree.delete(item)
            self.qso_count -= 1
        
        self.qso_count_var.set(f"QSOs: {self.qso_count}")
        
        # Delete from ADIF file(s)
        total_deleted = 0
        for adif_date, qsos in qsos_by_file.items():
            deleted_count = self._delete_qsos_from_adif(qsos, adif_date)
            total_deleted += deleted_count
        
        if total_deleted > 0:
            self.add_alert(f"Deleted {count} QSO(s) from display and ADIF")
        else:
            self.add_alert(f"Deleted {count} QSO(s) from display (not found in ADIF)")
        
        # Re-sort the tree by time (newest first)
        self._sort_qso_tree()
    
    def _delete_qsos_from_adif(self, qsos_to_delete, adif_date=None):
        """Remove QSOs from ADIF file, matching on call + band + time
        
        Args:
            qsos_to_delete: List of QSO dicts with time, call, band, mode
            adif_date: Date string like '20260217' to specify which ADIF file
                       If None, uses today's date
        """
        import datetime
        import re
        
        try:
            # Get ADIF file path
            log_dir = os.path.join(os.path.dirname(__file__), 'logs')
            if adif_date is None:
                adif_date = datetime.datetime.now().strftime('%Y%m%d')
            adif_path = os.path.join(log_dir, f'n5zy_copilot_{adif_date}.adi')
            
            if not os.path.exists(adif_path):
                print(f"ADIF Delete: File not found: {adif_path}")
                return 0
            
            print(f"ADIF Delete: Searching in {adif_date} log for {len(qsos_to_delete)} QSO(s)")
            
            # Read entire file
            with open(adif_path, 'r') as f:
                content = f.read()
            
            # Split into header and records
            eoh_match = re.search(r'<eoh>', content, re.IGNORECASE)
            if not eoh_match:
                print("ADIF Delete: No <EOH> found in file")
                return 0
            
            header = content[:eoh_match.end()]
            records_section = content[eoh_match.end():]
            
            # Split into individual records
            records = re.split(r'<eor>', records_section, flags=re.IGNORECASE)
            
            # Build a set of (call, band, time_hhmm) tuples to delete
            # Time in display is "HH:MM" or "HH:MM:SS" or "MM-DD HH:MM" (multi-day)
            # In ADIF it's "HHMMSS"
            delete_keys = set()
            for qso in qsos_to_delete:
                time_str = qso['time']
                # Handle multi-day format "02-17 03:49" - extract just the time part
                if ' ' in time_str and len(time_str) > 5:
                    time_str = time_str.split(' ')[-1]  # Get the HH:MM part
                # Convert display time "03:49" or "03:49:00" to "0349" for matching
                time_clean = time_str.replace(':', '')
                time_hhmm = time_clean[:4]  # First 4 chars = HHMM
                key = (qso['call'].upper(), qso['band'].lower(), time_hhmm)
                delete_keys.add(key)
            
            # Filter out matching records
            deleted_count = 0
            kept_records = []
            
            for record in records:
                record = record.strip()
                if not record:
                    continue
                
                # Extract call and band from this record
                call_match = re.search(r'<call:(\d+)>([^<\s]+)', record, re.IGNORECASE)
                band_match = re.search(r'<band:(\d+)>([^<\s]+)', record, re.IGNORECASE)
                
                if call_match and band_match:
                    # Get values using ADIF length specifier
                    call_len = int(call_match.group(1))
                    record_call = call_match.group(2)[:call_len].upper()
                    
                    band_len = int(band_match.group(1))
                    record_band = band_match.group(2)[:band_len].lower()
                    
                    # Extract both time_on and time_off
                    time_on_match = re.search(r'<time_on:(\d+)>([^<\s]+)', record, re.IGNORECASE)
                    time_off_match = re.search(r'<time_off:(\d+)>([^<\s]+)', record, re.IGNORECASE)
                    
                    # If no time fields, we can't match precisely - keep the record
                    if not time_on_match and not time_off_match:
                        kept_records.append(record)
                        continue
                    
                    # Build list of possible time keys for this record
                    # ADIF reload displays time_on, live QSOs display time_off
                    possible_keys = []
                    
                    if time_on_match:
                        time_len = int(time_on_match.group(1))
                        record_time = time_on_match.group(2)[:time_len]
                        record_time_hhmm = record_time[:4]
                        possible_keys.append((record_call, record_band, record_time_hhmm))
                    
                    if time_off_match:
                        time_len = int(time_off_match.group(1))
                        record_time = time_off_match.group(2)[:time_len]
                        record_time_hhmm = record_time[:4]
                        possible_keys.append((record_call, record_band, record_time_hhmm))
                    
                    # Check if any of the possible keys match
                    matched_key = None
                    for record_key in possible_keys:
                        if record_key in delete_keys:
                            matched_key = record_key
                            break
                    
                    if matched_key:
                        deleted_count += 1
                        print(f"ADIF Delete: Matched and removed {record_call} on {record_band} at {matched_key[2]}")
                        delete_keys.discard(matched_key)  # Remove so we only delete one per key
                        continue
                
                kept_records.append(record)
            
            if deleted_count > 0:
                # Rewrite the file
                with open(adif_path, 'w') as f:
                    f.write(header + '\n\n')
                    for record in kept_records:
                        f.write(record + ' <eor>\n')
                
                print(f"ADIF Delete: Removed {deleted_count} record(s) from {adif_date} log")
            else:
                print(f"ADIF Delete: No matching records found in {adif_date} log")
            
            return deleted_count
            
        except Exception as e:
            print(f"ADIF Delete error: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def _sort_qso_tree(self, reverse=True):
        """Sort QSO tree by time column
        
        Args:
            reverse: If True, newest first (default). If False, oldest first.
        """
        # Get all items and their values
        items = []
        for item in self.qso_tree.get_children(''):
            values = self.qso_tree.item(item)['values']
            items.append((item, values))
        
        # Sort by time column (index 0)
        # Time formats: "HH:MM", "HH:MM:SS", "MM-DD HH:MM"
        def sort_key(x):
            time_str = str(x[1][0]) if x[1] else ''
            
            # Normalize to sortable format
            # Multi-day: "02-17 03:49" -> "02-17 03:49"
            # Same-day: "03:49" -> "99-99 03:49" (to sort at end when ascending)
            # Same-day: "03:49:00" -> "99-99 03:49:00"
            
            if ' ' in time_str and '-' in time_str.split(' ')[0]:
                # Multi-day format "MM-DD HH:MM" - already good
                return time_str
            else:
                # Same-day format - prepend fake high date so it sorts at end for ascending
                # (which means it sorts first for descending/newest-first)
                return f"99-99 {time_str}"
        
        items.sort(key=sort_key, reverse=reverse)
        
        # Reorder items in tree
        for idx, (item, values) in enumerate(items):
            self.qso_tree.move(item, '', idx)
    
    def start_monitoring(self):
        """Start all monitoring threads"""
        try:
            # Start GPS monitoring with configured precision
            grid_precision = self.config.get('grid_precision', 4)
            self.gps_monitor = GPSMonitor(
                self.config['gps_port'],
                self.on_gps_update,
                grid_precision,
                lock_callback=self.on_gps_lock_change,
                baudrate=self.config.get('gps_baudrate', None),
                status_callback=self._on_gps_status_update
            )
            self.gps_monitor.start()

            # Start periodic GPS Logger display updates (every 2 seconds)
            self._start_gps_logger_timer()

            # Start GPS Time Sync timer if enabled (delay 10s to let GPS get a fix)
            if self.config.get('gps_time_sync_enabled', False):
                from modules.gps_monitor import GPSMonitor as _GM
                if _GM.is_admin():
                    self.root.after(10000, lambda: self._start_time_sync_timer())
                    self.root.after(15000, lambda: self._perform_time_sync())  # First sync
            
            # Start battery monitoring
            if self.config['victron_address'] and self.config['victron_key']:
                self.battery_monitor = BatteryMonitor(
                    self.config['victron_address'],
                    self.config['victron_key'],
                    self.on_battery_update
                )
                self.battery_monitor.start()
            
            # Initialize radio updater with QSO callback and location stamper
            self.radio_updater = RadioUpdater(
                self.config['wsjt_instances'],
                n1mm_host=self.config.get('n1mm_udp_host', '127.0.0.1'),
                n1mm_port=self.config.get('n1mm_udp_port', 52001),
                n3fjp_host=self.config.get('n3fjp_host', '127.0.0.1'),
                n3fjp_port=self.config.get('n3fjp_port', 1100),
                log4om_host=self.config.get('log4om_host', '127.0.0.1'),
                log4om_port=self.config.get('log4om_port', 2333),
                contest_logger=self.config.get('contest_logger', 'n1mm'),
                qso_callback=self.on_qso_logged,
                location_stamper=self._stamp_qso_location
            )

            # Load cty.dat for callsign → DXCC entity resolution
            from modules.cty_lookup import CTYLookup
            self.cty_lookup = CTYLookup()
            cty_path = Path('data/cty.dat')
            if cty_path.exists():
                self.cty_lookup.load_file(str(cty_path))
                dxcc_path = Path('data/dxcc_entities.json')
                if dxcc_path.exists():
                    self.cty_lookup.load_dxcc_mapping(str(dxcc_path))

            # Load LoTW cache (if exists)
            from modules.lotw_client import LoTWClient
            self.lotw_client = LoTWClient(self.config)
            self.lotw_client.cty_lookup = self.cty_lookup  # For CALL→DXCC fallback
            lotw_cache = Path('data/lotw_credits.adi')
            if lotw_cache.exists():
                self.lotw_client.load_from_file(str(lotw_cache))
                if self.cty_lookup._loaded:
                    self.cty_lookup.set_dxcc_mapping(self.lotw_client.get_prefix_to_dxcc_mapping())

            # Build PriorityEngine
            from modules.priority_engine import PriorityEngine
            self.priority_engine = PriorityEngine()
            self.priority_engine.configure(self.config, self.cty_lookup, self.lotw_client)

            # Start log monitoring
            self.log_monitor = LogMonitor(
                self.config['wsjt_instances'], self.on_new_decode,
                contest_mode=self.config.get('contest_mode', 'vhf'),
                priority_callback=self.on_priority_decode
            )
            self.log_monitor.priority_stations = self._parse_priority_stations(
                self.config.get('dx_priority_stations', '') or self.config.get('psk_priority_stations', ''))
            # Enable dynamic DX2/DX3 detection from ALL.TXT decodes
            self.log_monitor.decode_check_callback = self._on_decode_check
            self.log_monitor.start()

            # Start priority alert aging timer (every 60 seconds)
            self.root.after(60000, self._age_priority_alerts)

            # Check LoTW auto-refresh (30s after startup)
            self.root.after(30000, self._check_lotw_auto_refresh)

            # Update settings UI status labels
            if hasattr(self, 'lotw_status_var'):
                self.lotw_status_var.set(self._get_lotw_status_text())
            if hasattr(self, 'cty_status_var'):
                self.cty_status_var.set(self._get_cty_status_text())
            
            # Start APRS if enabled
            if self.config.get('aprs_enabled', False):
                self._start_aprs()
            
            # Enable grid boundary alerts if configured
            if self.config.get('grid_boundary_alerts', False):
                self.grid_boundary.set_enabled(True)
            
            self.add_alert("System started successfully")
            self.update_status("Running")
            
        except Exception as e:
            messagebox.showerror("Startup Error", f"Failed to start monitoring: {e}")
            self.update_status(f"Error: {e}")
    
    def on_gps_update(self, grid, lat, lon):
        """Called when GPS position updates"""
        # Store position for ADIF stamping
        self.current_lat = lat
        self.current_lon = lon
        
        # Always update APRS position (even if grid hasn't changed)
        if hasattr(self, 'aprs_client') and self.aprs_client:
            self.aprs_client.set_position(lat, lon, grid)
        
        # Update grid boundary monitor (checks distance to edges)
        if hasattr(self, 'grid_boundary') and self.grid_boundary:
            self.grid_boundary.update_position(lat, lon, grid)
        
        # Update PSK monitor with current grid
        if hasattr(self, 'psk_monitor') and self.psk_monitor:
            self.psk_monitor.set_grid(grid)
        
        if grid != self.current_grid:
            old_grid = self.current_grid
            self.current_grid = grid
            self.grid_label.config(text=grid)
            
            # Update Manual Entry "My Grid" field (if not in QSO Party mode)
            if hasattr(self, 'manual_mygrid_var'):
                current_val = self.manual_mygrid_var.get()
                # Only update if it shows ---- or the old grid
                if current_val in ('----', '', old_grid) or not current_val:
                    if self.config.get('contest_mode') != 'qso_party':
                        self.manual_mygrid_var.set(grid)
            
            # Update QSY Advisor with new grid (tracks per-grid for rovers!)
            if self.qsy_advisor:
                self.qsy_advisor.set_my_grid(grid)
            
            # Update radios (always send grid for WSJT-X)
            self.radio_updater.update_grid(grid)
            
            # Voice announcement
            if old_grid != "----":
                self.voice.announce(f"Grid change. Entering {grid}", category="grid_change")
                self.add_alert(f"GRID CHANGE: {old_grid} → {grid}")
                
                # Post to Slack
                my_call = self.config.get('my_call', '') or 'NOCALL'
                my_bands = self.config.get('my_bands', ['6m', '2m', '70cm'])
                bands_str = ', '.join(my_bands[:6])  # Limit to first 6 bands for readability
                if len(my_bands) > 6:
                    bands_str += f" +{len(my_bands)-6} more"
                self.post_to_slack(f"📍 {my_call}/R now in {grid} on {bands_str}")
            else:
                self.voice.announce(f"Current grid is {grid}", category="grid_change")
                self.add_alert(f"GPS acquired. Current grid: {grid}")
        
        # Check for county changes (updates display and ADIF data)
        # This is called AFTER grid handling so it has final say on button text
        self._check_county_change(lat, lon)
        
        # Update GPS Logger tab display
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            if hasattr(self.gps_monitor, 'get_full_data'):
                full_data = self.gps_monitor.get_full_data()
                self._update_gps_logger_display(full_data)
        
        # Update logger button based on contest mode
        # This ensures button always shows correct value (county or grid)
        self._update_logger_button()
    
    def _update_logger_button(self):
        """Update the logger button text based on contest mode"""
        logger = self.config.get('contest_logger', 'n1mm')
        logger_name = LOGGER_NAMES.get(logger, 'N1MM+')
        mode = self.config.get('contest_mode', 'vhf')

        # Daily DX mode — no ROVERQTH/grid push needed
        if mode == 'daily_dx':
            self.logger_button.config(text=f"{logger_name} (no grid push)", state='disabled')
        elif mode == 'qso_party':
            # QSO Party mode - show county abbreviation
            if self.current_county:
                self.logger_button.config(text=f"Send to {logger_name}: {self.current_county}", state='normal')
            else:
                # No county set yet
                self.logger_button.config(text=f"Send to {logger_name}: [Set County]", state='normal')
        else:
            # VHF/222up mode - show grid
            if self.current_grid and self.current_grid != "----":
                self.logger_button.config(text=f"Send to {logger_name}: {self.current_grid}", state='normal')
            else:
                self.logger_button.config(text=f"Send to {logger_name}: ----", state='disabled')
    
    def _check_county_change(self, lat, lon):
        """Check if we've crossed into a new county. Updates display for ALL modes."""
        if not self.county_lookup or not self.county_lookup.is_loaded:
            return
        
        # Look up county from GPS coordinates
        county_info = self.county_lookup.lookup(lat, lon)
        
        if not county_info:
            # Not in a US county (water, outside US, etc.)
            return
        
        # Store for ADIF stamping (works in ALL modes)
        self.current_county_info = county_info
        
        # Check if county name changed (for any mode - updates display)
        if county_info.name != self._last_county_name:
            old_county = self._last_county_name
            self._last_county_name = county_info.name
            
            # Update status bar county display
            if hasattr(self, 'county_label'):
                self.county_label.config(text=f"{county_info.name}, {county_info.state_abbrev}")
            
            # Update GPS Logger tab county display if it exists
            if hasattr(self, 'gps_log_county_var'):
                self.gps_log_county_var.set(f"{county_info.state_abbrev}: {county_info.name}")
            
            # Log the change
            if old_county:
                self.add_alert(f"COUNTY: {old_county} → {county_info.name}, {county_info.state_abbrev}")
                print(f"County: {old_county} → {county_info.name}, {county_info.state_abbrev}")
        
        # === QSO Party Mode: Additional handling ===
        contest_mode = self.config.get('contest_mode', 'vhf')
        if contest_mode != 'qso_party':
            return
        
        auto_detect = self.config.get('county_auto_detect', True)
        if not auto_detect:
            return
        
        # Get the QSO Party code and check if this county is in it
        party_code = self.config.get('qso_party_code', '').upper()
        if not party_code or party_code not in self.qso_parties:
            return
        
        party_data = self.qso_parties[party_code]
        
        # Try to map FIPS code to QSO Party abbreviation
        county_abbrev = self._fips_to_qsoparty_abbrev(county_info.fips, county_info.name, party_data, party_code)
        
        if not county_abbrev:
            # County not in this QSO Party's list - might be bordering state
            return
        
        # Check if county changed
        if county_abbrev != self.current_county:
            old_county = self.current_county
            self.current_county = county_abbrev
            self.config['qso_party_county'] = county_abbrev
            
            print(f"QSO Party County: {old_county} → {county_abbrev}")
            
            # Update displays
            if hasattr(self, 'county_display_var'):
                self.county_display_var.set(county_abbrev)
            if hasattr(self, 'qso_party_county_var'):
                self.qso_party_county_var.set(county_abbrev)
            
            # Send to N1MM+ via RoverQTH
            if hasattr(self, 'radio_updater') and self.radio_updater:
                self.radio_updater.send_n1mm_roverqth_county(county_abbrev)
                self.radio_updater.set_current_county(county_abbrev)  # For process restart
            
            # Voice announcement
            if old_county:
                self.voice.announce(f"County change. Now in {county_info.contest_name}", category="county_change")
                self.add_alert(f"QSO PARTY COUNTY: {old_county} → {county_abbrev} ({county_info.contest_name})")
            else:
                self.voice.announce(f"Current county is {county_info.contest_name}", category="county_change")
                self.add_alert(f"QSO Party county detected: {county_abbrev} ({county_info.contest_name})")
            
            # Update Manual Entry "My County" field
            if hasattr(self, 'manual_mygrid_var'):
                self.manual_mygrid_var.set(county_abbrev)
    
    def _fips_to_qsoparty_abbrev(self, fips, county_name, party_data, party_code=None):
        """Convert FIPS code or county name to QSO Party county abbreviation"""
        # The party_code (e.g., "OK") IS the state abbreviation
        state_abbrev = party_code or party_data.get('state', '')
        
        # First, try to load a FIPS mapping file if it exists
        fips_map_path = Path(f"data/county_mappings/{state_abbrev}.json")
        if fips_map_path.exists():
            try:
                import json
                with open(fips_map_path) as f:
                    fips_map = json.load(f)
                # Skip metadata key
                if fips in fips_map and fips != '_metadata':
                    return fips_map[fips].get('code')
            except Exception as e:
                print(f"Error loading {fips_map_path}: {e}")
        
        # Fallback: match by county name from party data
        if not hasattr(self, '_county_name_cache'):
            self._county_name_cache = {}
        
        if state_abbrev not in self._county_name_cache:
            name_map = {}
            counties_data = party_data.get('counties', [])
            for item in counties_data:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    abbrev, name = item[0], item[1]
                    normalized = name.upper().replace(' COUNTY', '').replace(' PARISH', '').strip()
                    name_map[normalized] = abbrev
            self._county_name_cache[state_abbrev] = name_map
        
        name_map = self._county_name_cache.get(state_abbrev, {})
        normalized_input = county_name.upper().replace(' COUNTY', '').replace(' PARISH', '').strip()
        
        return name_map.get(normalized_input)
    
    def _stamp_qso_location(self, qso_data):
        """
        Stamp QSO data with GPS-derived location fields for ADIF.
        
        Adds these fields for proper LoTW/Log4OM import:
        - my_state: State abbreviation (e.g., "OK")
        - my_county: County name (e.g., "Oklahoma")
        - my_lat: ADIF format latitude (e.g., "N035 28.056")
        - my_lon: ADIF format longitude (e.g., "W097 30.984")
        - my_country: "United States"
        - my_cq_zone: CQ Zone (default "4" for central US)
        - my_itu_zone: ITU Zone (default "7" for central US)
        - my_dxcc: DXCC entity (291 = USA)
        """
        # Get state and county from current county info
        if self.current_county_info:
            qso_data['my_state'] = self.current_county_info.state_abbrev
            qso_data['my_county'] = f"{self.current_county_info.state_abbrev},{self.current_county_info.name}"
        
        # Get lat/lon from GPS
        if self.current_lat is not None and self.current_lon is not None:
            qso_data['my_lat'] = self._to_adif_latitude(self.current_lat)
            qso_data['my_lon'] = self._to_adif_longitude(self.current_lon)
        
        # Static fields for US stations
        qso_data['my_country'] = "United States"
        qso_data['my_cq_zone'] = self.config.get('my_cq_zone', '4')
        qso_data['my_itu_zone'] = self.config.get('my_itu_zone', '7')
        qso_data['my_dxcc'] = '291'  # USA
        
        return qso_data
    
    def _to_adif_latitude(self, lat):
        """Convert decimal latitude to ADIF format: N/S DDD MM.MMM"""
        hemisphere = 'N' if lat >= 0 else 'S'
        abs_lat = abs(lat)
        degrees = int(abs_lat)
        minutes = (abs_lat - degrees) * 60
        return f"{hemisphere}{degrees:03d} {minutes:06.3f}"
    
    def _to_adif_longitude(self, lon):
        """Convert decimal longitude to ADIF format: E/W DDD MM.MMM"""
        hemisphere = 'E' if lon >= 0 else 'W'
        abs_lon = abs(lon)
        degrees = int(abs_lon)
        minutes = (abs_lon - degrees) * 60
        return f"{hemisphere}{degrees:03d} {minutes:06.3f}"
    
    def on_gps_lock_change(self, has_lock, message):
        """Called when GPS lock status changes"""
        if has_lock:
            self.add_alert(f"GPS: Lock acquired ✓")
            self.voice.announce("GPS lock acquired", category="warnings")
            # Update GPS indicator to green
            if hasattr(self, 'gps_indicator'):
                self.gps_indicator.config(fg='green')
        else:
            self.add_alert(f"GPS: Lock lost ✗", priority=True)
            self.voice.announce("Warning: GPS lock lost", category="warnings")
            # Update GPS indicator to red
            if hasattr(self, 'gps_indicator'):
                self.gps_indicator.config(fg='red')
    
    def on_battery_update(self, voltage, current, soc, remaining_mins):
        """Called when battery data updates"""
        self.battery_voltage = voltage
        self.battery_current = current
        self.battery_soc = soc
        
        # Format: "13.2V -15A 85%"
        # Current is negative when discharging, positive when charging
        current_str = f"{current:+.0f}A" if abs(current) >= 1 else f"{current:+.1f}A"
        soc_str = f"{soc:.0f}%" if soc is not None and soc < 101 else ""
        
        if soc_str:
            self.voltage_label.config(text=f"{voltage:.1f}V {current_str} {soc_str}")
        else:
            self.voltage_label.config(text=f"{voltage:.1f}V {current_str}")
        
        # Color code based on voltage (primary concern during TX)
        if voltage < 12.0:
            self.voltage_label.config(foreground='red')
            if voltage < 11.5:
                self.voice.announce("Warning: Battery voltage critical", category="warnings")
        elif voltage < 12.5:
            self.voltage_label.config(foreground='orange')
        else:
            self.voltage_label.config(foreground='green')
    
    def _start_wsjt_watchdog(self):
        """Start watchdog timer to monitor WSJT-X heartbeats"""
        self._check_wsjt_status()
    
    def _check_wsjt_status(self):
        """Check WSJT-X instance status and update indicators"""
        import time
        now = time.time()
        timeout = 30  # Seconds without heartbeat before considered dead
        
        if hasattr(self, 'radio_updater') and self.radio_updater:
            # Get current discovered instances from radio_updater
            for discovered_port, (wsjtx_id, last_seen) in self.radio_updater.wsjtx_ids.items():
                # Match wsjtx_id to the correct config instance
                # WSJT-X ID looks like "WSJT-X - ic7610" 
                wsjtx_id_lower = wsjtx_id.lower()
                
                for cfg_port, lbl in self.wsjt_status_labels.items():
                    # Find the config for this port
                    cfg_instance = None
                    for inst in self.config.get('wsjt_instances', []):
                        if inst.get('udp_port') == cfg_port:
                            cfg_instance = inst
                            break
                    
                    if cfg_instance:
                        # Get specific identifier for this instance
                        inst_name = cfg_instance.get('name', '').lower()
                        
                        # Match if:
                        # 1. Ports match exactly, or
                        # 2. Instance name contains something from wsjtx_id, or
                        # 3. wsjtx_id contains something from instance name
                        matched = False
                        
                        # Port match
                        if discovered_port == cfg_port:
                            matched = True
                        # Name-based matching - look for common identifiers
                        else:
                            # Extract meaningful parts from names
                            name_parts = inst_name.replace('-', ' ').replace('_', ' ').split()
                            wsjtx_parts = wsjtx_id_lower.replace('-', ' ').replace('_', ' ').split()
                            
                            for part in name_parts:
                                if len(part) >= 3 and part in wsjtx_id_lower:
                                    matched = True
                                    break
                        
                        if matched:
                            self.wsjt_last_seen[cfg_port] = last_seen
        
        # Update status labels
        for port, lbl in self.wsjt_status_labels.items():
            last = self.wsjt_last_seen.get(port, 0)
            if last == 0:
                # Never seen
                lbl.config(bg='red', fg='white')
            elif now - last > timeout:
                # Timed out - was connected but lost
                if lbl.cget('bg') != 'red':
                    # Just turned red - alert!
                    lbl.config(bg='red', fg='white')
                    # Find instance name for voice alert
                    for inst in self.config.get('wsjt_instances', []):
                        if inst.get('udp_port') == port:
                            name = inst.get('name', 'Unknown')
                            short = name.split('(')[0].strip() if '(' in name else name
                            self.voice.announce(f"Warning: {short} is not responding", category="warnings")
                            self.add_alert(f"WARNING: WSJT-X {short} lost connection!", priority=True)
                            break
            else:
                # Connected
                lbl.config(bg='green', fg='white')
        
        # Schedule next check
        self.root.after(5000, self._check_wsjt_status)  # Check every 5 seconds
    
    def update_wsjt_heartbeat(self, port, wsjtx_id):
        """Called when a WSJT-X heartbeat is received"""
        import time
        # Map the wsjtx_id to the correct config port
        for cfg_port, lbl in self.wsjt_status_labels.items():
            cfg_instance = None
            for inst in self.config.get('wsjt_instances', []):
                if inst.get('udp_port') == cfg_port:
                    cfg_instance = inst
                    break
            
            if cfg_instance:
                inst_name = cfg_instance.get('name', '').lower()
                # Check if wsjtx_id matches this instance
                if any(hint in wsjtx_id.lower() for hint in ['7610', '9700', '7300']):
                    for hint in ['7610', '9700', '7300']:
                        if hint in wsjtx_id.lower() and hint in inst_name:
                            self.wsjt_last_seen[cfg_port] = time.time()
                            break

    def toggle_aprs(self):
        """Toggle APRS on/off from checkbox"""
        enabled = self.aprs_enabled_var.get()
        self.config['aprs_enabled'] = enabled
        self.save_config()
        
        if enabled:
            self._start_aprs()
        else:
            if hasattr(self, 'aprs_client') and self.aprs_client:
                self.aprs_client.stop()
                self.aprs_client = None
                self.add_alert("APRS stopped")
            # Update APRS Messages tab status
            if hasattr(self, 'aprs_msg_status_var'):
                self.aprs_msg_status_var.set("Not connected")
    
    def _start_aprs(self):
        """Start APRS-IS client"""
        # Stop existing client first to prevent duplicate connections
        if hasattr(self, 'aprs_client') and self.aprs_client:
            import time
            print("APRS: Stopping existing client before restart...")
            self.aprs_client.stop()
            self.aprs_client = None
            time.sleep(1)  # Brief pause so server recognizes disconnect

        try:
            callsign = self.config.get('aprs_callsign', 'N5ZY')
            beacon_interval = self.config.get('aprs_beacon_interval', 600)
            alert_radius = self.config.get('aprs_alert_radius', 10)
            comment = self.config.get('aprs_comment', 'N5ZY.ORG Rover!')
            
            self.aprs_client = APRSClient(
                callsign=callsign,
                callback_position=self.on_aprs_nearby_station,
                callback_message=self.on_aprs_message,
                beacon_interval=beacon_interval
            )
            
            # Set alert radius (convert miles to km)
            self.aprs_client.alert_radius_km = alert_radius * 1.60934
            
            # Set beacon comment
            self.aprs_client.beacon_comment = comment
            
            # Set initial position if we have GPS (BEFORE starting threads)
            if hasattr(self, 'gps_monitor') and self.gps_monitor:
                pos = self.gps_monitor.get_current_position()
                if pos:
                    print(f"APRS: Setting initial position from GPS: {pos['lat']:.4f}, {pos['lon']:.4f} ({pos['grid']})")
                    self.aprs_client.my_lat = pos['lat']
                    self.aprs_client.my_lon = pos['lon']
                    self.aprs_client.my_grid = pos['grid']
                else:
                    print("APRS: Warning - no GPS position available yet")
            
            self.aprs_client.start()
            self.add_alert(f"APRS started: {callsign} (beacon every {beacon_interval//60} min)")
            
            # Update APRS Messages tab status
            if hasattr(self, 'aprs_msg_status_var'):
                self.aprs_msg_status_var.set(f"Connecting as {callsign}...")
                # Schedule status check after connection attempt
                self.root.after(3000, self._update_aprs_msg_status)
            
        except Exception as e:
            print(f"APRS: Failed to start: {e}")
            self.add_alert(f"APRS error: {e}")
            if hasattr(self, 'aprs_msg_status_var'):
                self.aprs_msg_status_var.set(f"Connection failed: {e}")
    
    def toggle_grid_boundary_alerts(self):
        """Toggle grid boundary alerts on/off from checkbox"""
        enabled = self.grid_boundary_var.get()
        self.config['grid_boundary_alerts'] = enabled
        self.save_config()
        
        if hasattr(self, 'grid_boundary') and self.grid_boundary:
            self.grid_boundary.set_enabled(enabled)
            if enabled:
                self.add_alert("Grid boundary alerts enabled")
            else:
                self.add_alert("Grid boundary alerts disabled")
    
    def _update_aprs_msg_status(self):
        """Update APRS Messages tab connection status"""
        if not hasattr(self, 'aprs_msg_status_var'):
            return
            
        if hasattr(self, 'aprs_client') and self.aprs_client:
            try:
                stats = self.aprs_client.get_stats()
                if stats['connected']:
                    callsign = self.config.get('aprs_callsign', 'N5ZY')
                    self.aprs_msg_status_var.set(f"Connected as {callsign}")
                else:
                    self.aprs_msg_status_var.set("Connecting...")
                    # Retry check in 2 seconds if not connected yet
                    self.root.after(2000, self._update_aprs_msg_status)
            except Exception as e:
                self.aprs_msg_status_var.set(f"Error: {e}")
        else:
            self.aprs_msg_status_var.set("Not connected")
    
    def _on_contest_mode_change(self, event=None):
        """Handle contest mode selection change"""
        # Get the key from the display value
        selected_text = self.contest_mode_var.get()
        mode_key = 'vhf'  # default
        for key, value in CONTEST_MODES.items():
            if value == selected_text:
                mode_key = key
                break
        
        self.config['contest_mode'] = mode_key
        
        # Set grid precision based on mode
        if mode_key == 'vhf':
            self.config['grid_precision'] = 4
        elif mode_key == '222up':
            self.config['grid_precision'] = 6
        elif mode_key == 'qso_party':
            self.config['grid_precision'] = 4  # Still use 4-char for WSJT-X
        elif mode_key == 'daily_dx':
            self.config['grid_precision'] = 4
        
        self.save_config()
        self._update_contest_mode_ui()
        
        # Update GPS monitor precision if running
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            self.gps_monitor.set_precision(self.config['grid_precision'])
        
        # If switching TO QSO Party mode, immediately check county from current GPS position
        if mode_key == 'qso_party' and self.current_lat and self.current_lon:
            self._check_county_change(self.current_lat, self.current_lon)
            self._update_logger_button()
        
        self.add_alert(f"Contest mode: {CONTEST_MODES[mode_key]}")
        print(f"Contest Mode: Changed to {mode_key}, grid precision = {self.config['grid_precision']}")
    
    def _update_contest_mode_ui(self):
        """Update UI elements based on current contest mode"""
        mode = self.config.get('contest_mode', 'vhf')
        
        # Update grid precision display
        precision = self.config.get('grid_precision', 4)
        self.grid_precision_label.config(text=f"{precision}-char")
        
        # Show/hide QSO party settings
        # Also hide QSO party if using N3FJP (different apps per party)
        logger = self.config.get('contest_logger', 'n1mm')
        if mode == 'qso_party' and logger == 'n1mm':
            self.qso_party_frame.grid()
        else:
            self.qso_party_frame.grid_remove()
        
        # Always show county in top bar (useful for all modes)
        self.county_frame.pack(side=tk.LEFT, padx=10)
        
        # Update logger button to show correct value (county or grid)
        self._update_logger_button()
        
        # Update Manual Entry labels based on mode
        self._update_manual_entry_labels()
    
    def _on_logger_change(self, event=None):
        """Handle logger selection change"""
        logger = self.logger_var.get()
        self.config['contest_logger'] = logger
        self.save_config()
        self._update_logger_ui()
        self._update_contest_mode_ui()  # QSO Party only for N1MM+
        
        # Update radio_updater with new logger
        if hasattr(self, 'radio_updater') and self.radio_updater:
            self.radio_updater.set_logger(logger)
        
        # Update logger button to show correct value
        self._update_logger_button()
        
        # Update Test Mode button
        logger_name = LOGGER_NAMES.get(logger, 'N1MM+')
        if hasattr(self, 'test_send_button'):
            self.test_send_button.config(text=f"Send to WSJT-X + {logger_name}")
        if hasattr(self, 'test_hint_label'):
            self.test_hint_label.config(text=f"Sends grid to all WSJT-X instances and {logger_name}")

        # Update Manual Entry labels
        self._update_manual_entry_labels()

        self.add_alert(f"Contest logger: {logger_name}")
        print(f"Logger: Changed to {logger}")
    
    def _update_logger_ui(self):
        """Show/hide logger-specific settings"""
        logger = self.config.get('contest_logger', 'n1mm')

        if logger == 'n1mm':
            self.n1mm_settings_frame.grid()
            self.n3fjp_settings_frame.grid_remove()
            self.log4om_settings_frame.grid_remove()
        elif logger == 'n3fjp':
            self.n1mm_settings_frame.grid_remove()
            self.n3fjp_settings_frame.grid()
            self.log4om_settings_frame.grid_remove()
        elif logger == 'log4om':
            self.n1mm_settings_frame.grid_remove()
            self.n3fjp_settings_frame.grid_remove()
            self.log4om_settings_frame.grid()
    
    def _update_manual_entry_labels(self):
        """Update Manual Entry tab labels based on contest mode and logger"""
        # Safety check - ensure widgets exist
        if not hasattr(self, 'their_grid_label'):
            return
        
        mode = self.config.get('contest_mode', 'vhf')
        logger = self.config.get('contest_logger', 'n1mm')
        logger_name = LOGGER_NAMES.get(logger, 'N1MM+')

        if mode == 'qso_party':
            # QSO Party mode - use exchange labels
            self.their_grid_label.config(text="Their Exchange:")
            self.my_grid_label.config(text="My County:")
            self.my_grid_hint_label.config(text="(set via Settings → QSO Party)")
            self.log_qso_button.config(text=f"Log QSO to {logger_name} & ADIF")
            # Auto-fill county if set
            if self.current_county:
                self.manual_mygrid_var.set(self.current_county)
        else:
            # VHF/222 Up/Daily DX mode - use grid labels
            self.their_grid_label.config(text="Their Grid:")
            self.my_grid_label.config(text="My Grid:")
            self.my_grid_hint_label.config(text="(auto-filled from GPS)")
            self.log_qso_button.config(text=f"Log QSO to {logger_name} & ADIF")
            # Auto-fill grid from GPS
            if self.current_grid != "----":
                self.manual_mygrid_var.set(self.current_grid)
        
        # Update band dropdown (HF+VHF in QSO Party, VHF only in VHF contests)
        self._update_manual_entry_bands()
    
    def _update_manual_entry_bands(self):
        """Update Manual Entry band dropdown based on My Bands settings"""
        if not hasattr(self, 'manual_band_combo'):
            return
        
        # Always use configured "My Bands" - user only operates these bands
        my_bands = self.config.get('my_bands', ['6m', '2m', '1.25m', '70cm', '33cm', '23cm'])
        self.manual_band_combo['values'] = my_bands
        
        # Keep current selection if still valid
        current = self.manual_band_var.get()
        if current not in my_bands and my_bands:
            self.manual_band_var.set(my_bands[0])
    
    def _update_grid_corner_bands(self):
        """Update Grid Corner available bands based on My Bands settings"""
        my_bands = self.config.get('my_bands', ['6m', '2m', '1.25m', '70cm', '33cm', '23cm'])
        self.gc_available_bands = my_bands
        
        # Update the "Add Rover" band checkboxes
        if hasattr(self, 'gc_add_band_vars'):
            # Clear old checkboxes - they were created with all bands
            # For simplicity, just update the frequency maps
            pass
        
        # Update frequency maps for any new bands
        for band in my_bands:
            if band not in self.gc_ssb_freqs:
                self.gc_ssb_freqs[band] = '144.200'  # Default
            if band not in self.gc_fm_freqs:
                self.gc_fm_freqs[band] = '146.520'
            if band not in self.gc_cw_freqs:
                self.gc_cw_freqs[band] = '144.100'
    
    # Bands that are 6m (50 MHz) and above — VHF/UHF/Microwave
    VHF_BANDS = {'6m', '2m', '1.25m', '70cm', '33cm', '23cm',
                 '13cm', '9cm', '5cm', '3cm', '1.2cm', '6mm', '4mm', '2mm', '1mm'}

    def _update_vhf_tab_states(self):
        """Enable/disable Grid Corner and QSY Advisor tabs based on whether any VHF+ band is selected."""
        my_bands = set(self.config.get('my_bands', []))
        has_vhf = bool(my_bands & self.VHF_BANDS)

        for tab_widget in (self.qsy_tab, self.grid_corner_tab):
            try:
                if has_vhf:
                    self.notebook.tab(tab_widget, state='normal')
                else:
                    self.notebook.tab(tab_widget, state='disabled')
            except Exception:
                pass  # Tab not yet added to notebook

    def _browse_qsoparty_file(self):
        """Browse for QSOParty.sec file"""
        filepath = filedialog.askopenfilename(
            title="Select N1MM+ QSOParty.sec file",
            filetypes=[("SEC files", "*.sec"), ("All files", "*.*")],
            initialfile="QSOParty.sec"
        )
        if filepath:
            self.qsoparty_file_var.set(filepath)
            self.config['qsoparty_file'] = filepath
            self.save_config()
            self._reload_qsoparty_file()
    
    def _reload_qsoparty_file(self):
        """Reload QSO party data from file"""
        filepath = self.qsoparty_file_var.get()
        self.config['qsoparty_file'] = filepath
        self.save_config()
        
        self.qso_parties = parse_qsoparty_file(filepath)
        
        # Update QSO party dropdown
        if self.qso_parties:
            self.qso_party_combo['values'] = sorted(self.qso_parties.keys())
            self.add_alert(f"Loaded {len(self.qso_parties)} QSO parties from file")
        else:
            self.qso_party_combo['values'] = []
            self.add_alert("No QSO parties found in file")
        
        # Update county list
        self._update_county_list()
    
    def _browse_scp_file(self):
        """Browse for Super Check Partial master.dta file"""
        filepath = filedialog.askopenfilename(
            title="Select Super Check Partial master.dta file",
            filetypes=[("DTA files", "*.dta"), ("SCP files", "*.scp"), ("All files", "*.*")],
            initialfile="master.dta"
        )
        if filepath:
            self.scp_file_var.set(filepath)
            self.config['scp_file'] = filepath
            self.save_config()
            self._reload_scp_file()
    
    def _download_scp_file(self):
        """Download Super Check Partial master.dta from supercheckpartial.com"""
        import urllib.request
        import threading
        
        url = "http://www.supercheckpartial.com/MASTER.SCP"
        dest_path = Path("data/master.dta")
        
        def download():
            try:
                self.add_alert("Downloading SCP database...")
                if hasattr(self, 'scp_count_label'):
                    self.root.after(0, lambda: self.scp_count_label.config(text="(downloading...)"))
                
                # Ensure data directory exists
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Download the file
                urllib.request.urlretrieve(url, dest_path)
                
                # Update config to use this path
                self.config['scp_file'] = str(dest_path)
                self.save_config()
                
                # Reload the database
                self.root.after(0, lambda: self.scp_file_var.set(str(dest_path)))
                self.root.after(0, self._reload_scp_file)
                
                self.root.after(0, lambda: self.add_alert(f"SCP database downloaded to {dest_path}"))
                
            except Exception as e:
                self.root.after(0, lambda: self.add_alert(f"SCP download failed: {e}"))
                if hasattr(self, 'scp_count_label'):
                    self.root.after(0, lambda: self.scp_count_label.config(text="(download failed)"))
        
        # Run download in background thread
        threading.Thread(target=download, daemon=True).start()
    
    def _reload_scp_file(self):
        """Reload Super Check Partial database from file"""
        filepath = self.scp_file_var.get()
        self.config['scp_file'] = filepath
        self.save_config()
        
        try:
            if Path(filepath).exists():
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    self.scp_calls = [line.strip().upper() for line in f if line.strip() and not line.startswith('#')]
                count = len(self.scp_calls)
                self.add_alert(f"Loaded {count} callsigns from SCP database")
                
                # Update labels
                if hasattr(self, 'scp_count_label'):
                    self.scp_count_label.config(text=f"({count} callsigns loaded)")
                if hasattr(self, 'scp_count_var'):
                    self.scp_count_var.set(f"Loaded: {count} calls")
            else:
                self.scp_calls = []
                self.add_alert("SCP file not found")
                if hasattr(self, 'scp_count_label'):
                    self.scp_count_label.config(text="(file not found)")
        except Exception as e:
            self.scp_calls = []
            self.add_alert(f"Error loading SCP: {e}")
    
    def _on_qsoparty_change(self, event=None):
        """Handle QSO party selection change"""
        party_code = self.qso_party_code_var.get()
        self.config['qso_party_code'] = party_code
        self.save_config()
        
        # Update county dropdown
        self._update_county_list()
        self.county_display_var.set("----")
        
        # Clear current county so GPS can re-detect with new party's abbreviations
        self.current_county = ""
        
        # Re-check county from current GPS position for new party
        if self.current_lat and self.current_lon:
            self._check_county_change(self.current_lat, self.current_lon)
            self._update_logger_button()
        
        print(f"QSO Party: Changed to {party_code}")
    
    def _update_county_list(self):
        """Update county dropdown based on selected QSO party"""
        party_code = self.qso_party_code_var.get()
        
        if party_code in self.qso_parties:
            counties = get_county_list_for_display(self.qso_parties[party_code])
            self.county_combo['values'] = counties
            if counties:
                print(f"QSO Party: {party_code} has {len(counties)} counties")
        else:
            self.county_combo['values'] = []
        
        self.county_combo.set('')
        self.qso_party_county_var.set('')
    
    def _apply_county(self):
        """Apply the selected county and send to N1MM+"""
        county_input = self.qso_party_county_var.get()
        party_code = self.qso_party_code_var.get()
        
        if not county_input:
            messagebox.showwarning("No County", "Please select a county first")
            return
        
        if party_code not in self.qso_parties:
            messagebox.showerror("Error", f"QSO Party '{party_code}' not found")
            return
        
        # Get canonical county abbreviation
        party_data = self.qso_parties[party_code]
        canonical = get_canonical_county(party_data, county_input)
        
        if not canonical:
            # If not found, just use what they typed (might be valid)
            canonical = county_input.upper()
        
        # Update state
        self.current_county = canonical
        self.config['qso_party_county'] = canonical
        self.save_config()
        
        # Update displays
        self.county_display_var.set(canonical)
        self.county_label.config(text=canonical)  # Top bar county display
        
        # Update logger button to show county (in QSO Party mode)
        self.logger_button.config(text=f"Send to N1MM+: {canonical}", state='normal')
        
        # Send to N1MM+ via RoverQTH
        if hasattr(self, 'radio_updater') and self.radio_updater:
            self.radio_updater.send_n1mm_roverqth_county(canonical)
        
        # Voice announcement
        self.voice.announce(f"County set to {canonical}", category="operational")
        self.add_alert(f"QSO Party: {party_code} - {canonical}")
        
        # Update Manual Entry "My County" field
        self.manual_mygrid_var.set(canonical)
        
        print(f"QSO Party: Set county to {canonical} for {party_code}")
    
    def on_aprs_nearby_station(self, callsign, lat, lon, distance_mi, bearing, symbol_desc):
        """Called when a mobile APRS station is detected nearby"""
        import time as _time
        msg = f"APRS: {callsign} ({symbol_desc}) {distance_mi:.1f} mi {bearing}"
        self.add_alert(msg, priority=True)
        self.voice.announce(f"APRS station {callsign}, {distance_mi:.0f} miles {bearing}", category="aprs_nearby")

        # Track for rover broadcast list (aged out after 30 min)
        # Keep full callsign-SSID so messages go to the active node (e.g. -9 mobile),
        # not the home station
        self.nearby_aprs_stations[callsign.upper()] = (
            distance_mi, bearing, _time.time()
        )
        # Update button label if Notify tab exists
        if hasattr(self, '_aprs_send_btn'):
            count = self._count_nearby_aprs()
            self.root.after(0, lambda: self._aprs_send_btn.config(
                text=f"Send APRS ({count})"))
    
    def on_aprs_message(self, from_call, message, msgno):
        """Called when an APRS message is received"""
        # Display in APRS Messages tab inbox
        self.root.after(0, lambda: self._display_aprs_message(from_call, message, msgno))
    
    def on_qsy_opportunity(self, callsign, worked_band, available_bands, message):
        """Called when we work a station that has other bands available"""
        # Build voice message
        band_names = []
        for band in available_bands[:3]:  # Limit to first 3 bands to keep it short
            name = self.qsy_advisor.BAND_NAMES.get(band, band)
            band_names.append(name)
        
        if len(available_bands) > 3:
            bands_str = f"{', '.join(band_names)} and more"
        else:
            bands_str = ' and '.join(band_names) if len(band_names) <= 2 else f"{', '.join(band_names[:-1])}, and {band_names[-1]}"
        
        # Add to alerts (visual only - voice disabled during grid dance)
        self.add_alert(f"QSY: {callsign} -> {', '.join([self.qsy_advisor.BAND_NAMES.get(b, b) for b in available_bands])}", priority=True)
        
        # Voice announcement disabled - too chatty during grid corner operations
        # self.root.after(1500, lambda: self.voice.announce(f"QSY opportunity. {callsign} also has {bands_str}"))
    
    def on_boundary_announcement(self, message):
        """Called when approaching a grid boundary"""
        self.add_alert(f"Grid: {message}", priority=False)
        self.voice.announce(message, category="grid_boundary")
    
    def on_new_decode(self, band, callsign, grid, is_new_grid, is_calling_me):
        """Called when new decode is found in WSJT-X logs"""
        import time
        
        # Check if station is ignored (and clean up expired ignores)
        self._cleanup_expired_ignores()
        if callsign.upper() in self.ignored_stations:
            return  # Skip this station
        
        if is_new_grid:
            msg = f"New grid {grid} on {band} from {callsign}"
            self.add_alert(msg, priority=True, callsign=callsign)
            self.voice.announce(f"New grid {grid} on {band}", category="new_grid")
            # SMS notification for new grid
            if self.config.get('sms_on_new_grid', False):
                self.send_sms(msg)

        if is_calling_me:
            msg = f"{callsign} calling you on {band}"
            self.add_alert(msg, priority=True, callsign=callsign)
            self.voice.announce(f"{callsign} calling on {band}", category="calling_me")

    def _on_decode_check(self, band, callsign, freq_mhz, is_transmitting=True):
        """Dynamic DX2/DX3 check for ALL.TXT decodes not in the DX! station list.

        Called for every decoded transmitter so PriorityEngine can check against
        LoTW data for new DXCC entity (DX2) or new DXCC on band (DX3).
        """
        import time as _time
        if not callsign or not band:
            return

        # 2-minute per-callsign dedup to avoid running PriorityEngine every 15s
        now = _time.time()
        last = self._decode_check_recent.get(callsign.upper(), 0)
        if now - last < 120:
            return
        self._decode_check_recent[callsign.upper()] = now

        # Clean old entries (> 5 min) to prevent unbounded dict growth
        cutoff = now - 300
        self._decode_check_recent = {
            k: v for k, v in self._decode_check_recent.items() if v > cutoff
        }

        # Run PriorityEngine check (DX2/DX3 require LoTW data)
        if not hasattr(self, 'priority_engine') or not self.priority_engine:
            return
        priority_result = self.priority_engine.check(callsign, band, '')
        if priority_result and priority_result.code in ('DX2', 'DX3'):
            # Route to on_priority_decode for alert + SMS + voice
            self.root.after(0, lambda: self.on_priority_decode(
                band, callsign, freq_mhz, is_transmitting))

    def on_priority_decode(self, band, callsign, freq_mhz, is_transmitting=True):
        """Called when a priority station is decoded in ALL.TXT.

        is_transmitting: True if WE heard the priority station transmit,
                         False if we heard someone else call them.
        """
        # Note: intentionally NOT gated on psk_priority_enabled — ALL.TXT priority
        # detection (DX!, DX2, DX3) should work independently of PSK Monitor.

        from datetime import datetime
        time_str = datetime.now().strftime('%H:%M')

        # Use PriorityEngine if available
        priority_result = None
        if hasattr(self, 'priority_engine') and self.priority_engine:
            priority_result = self.priority_engine.check(callsign, band, '')

        if priority_result:
            if is_transmitting:
                msg = f"{priority_result.code} {callsign} decoded on {band}"
            else:
                msg = f"{priority_result.code} {callsign} called on {band} (not heard directly)"
            if priority_result.entity_name:
                msg += f" ({priority_result.entity_name})"
            self.add_alert(msg, priority=True, callsign=callsign)

            # Only voice alert when hearing the station directly
            if is_transmitting:
                self._priority_voice_alert(callsign, band, voice_msg=priority_result.voice_msg)

            # Insert into Priority pane
            # For ALL.TXT decodes: Nearby=our call, QSO Dist/Prop=blank
            my_dist_str = ''
            my_dir_str = ''
            if hasattr(self, 'cty_lookup') and self.cty_lookup and self.cty_lookup._loaded:
                if hasattr(self, 'current_lat') and self.current_lat:
                    bd = self.cty_lookup.get_bearing_distance(
                        self.current_lat, self.current_lon, callsign)
                    if bd:
                        my_dist_str = str(int(bd[1]))
                        my_dir_str = self.cty_lookup.bearing_to_compass(bd[0])

            spot_data = {
                'band': band,
                'nearby_call': self.config.get('aprs_callsign', 'N5ZY') if is_transmitting else '',
                'far_call': callsign,
                'qso_distance': '',
                'my_distance': my_dist_str,
                'bearing': my_dir_str,
                'mode': '',
            }
            self._insert_priority_spot(time_str, priority_result.code, priority_result.tag,
                                       spot_data, '')

            # SMS notification — only when hearing the station directly transmit
            if is_transmitting:
                code = priority_result.code  # 'DX!', 'DX2', 'DX3'
                sms_send = False
                if code == 'DX!' and self.config.get('sms_on_priority', False):
                    sms_send = True
                elif code == 'DX2' and self.config.get('sms_on_dx2', False):
                    sms_send = True
                elif code == 'DX3' and self.config.get('sms_on_dx3', False):
                    sms_send = True
                if sms_send:
                    self.send_sms(msg, callsign=callsign)
        else:
            # Fallback: legacy DX! behavior
            if is_transmitting:
                msg = f"DX! {callsign} decoded on {band}"
            else:
                msg = f"DX! {callsign} called on {band} (not heard directly)"
            self.add_alert(msg, priority=True, callsign=callsign)
            if is_transmitting:
                self._priority_voice_alert(callsign, band)
                # SMS for legacy DX! fallback
                if self.config.get('sms_on_priority', False):
                    self.send_sms(msg, callsign=callsign)

    def _priority_voice_alert(self, callsign, band, voice_msg=None):
        """Fire voice alert for priority station if not recently voiced (shared across sources)"""
        import time as _time
        key = (callsign.upper(), band)
        now = _time.time()
        if not hasattr(self, '_priority_voice_times'):
            self._priority_voice_times = {}
        if key in self._priority_voice_times and now - self._priority_voice_times[key] < 120:
            return  # Already voiced within 2 minutes
        self._priority_voice_times[key] = now
        if voice_msg:
            self.voice.announce(voice_msg, category="priority_dx")
        else:
            self.voice.announce(f"D X Priority {callsign} on {band}", category="priority_dx")

    def _parse_priority_stations(self, stations_str):
        """Parse comma-separated callsign list into a set of uppercase callsigns"""
        if not stations_str:
            return set()
        return {s.strip().upper() for s in stations_str.split(',') if s.strip()}

    def ignore_station(self, callsign, duration_minutes=None):
        """Add a station to the ignore list"""
        import time
        if duration_minutes is None:
            duration_minutes = self.ignore_duration_minutes
        
        callsign = callsign.upper().strip()
        if not callsign:
            return
            
        expire_time = time.time() + (duration_minutes * 60)
        self.ignored_stations[callsign] = expire_time
        self.add_alert(f"Ignoring {callsign} for {duration_minutes} min")
        print(f"Ignore: {callsign} until {time.ctime(expire_time)}")
    
    def unignore_station(self, callsign):
        """Remove a station from the ignore list"""
        callsign = callsign.upper().strip()
        if callsign in self.ignored_stations:
            del self.ignored_stations[callsign]
            self.add_alert(f"Un-ignored {callsign}")
    
    def _cleanup_expired_ignores(self):
        """Remove expired entries from ignore list"""
        import time
        now = time.time()
        expired = [call for call, exp in self.ignored_stations.items() if exp < now]
        for call in expired:
            del self.ignored_stations[call]
            print(f"Ignore expired: {call}")
    
    def add_alert(self, message, priority=False, callsign=None):
        """Add alert to the alerts display"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        if priority:
            formatted = f"[{timestamp}] *** {message} ***\n"
        else:
            formatted = f"[{timestamp}] {message}\n"
        
        self.alerts_text.insert(tk.END, formatted)
        self.alerts_text.see(tk.END)  # Auto-scroll
        
        # Store last alerting callsign for quick ignore
        if callsign:
            self.last_alert_callsign = callsign.upper()
    
    def update_status(self, message):
        """Update status bar text"""
        self.status_text.config(text=message)
    
    def force_grid_update(self):
        """Manually force grid update to all radios"""
        if self.current_grid != "----":
            self.radio_updater.update_grid(self.current_grid)
            self.add_alert(f"Forced grid update: {self.current_grid}")
        else:
            messagebox.showwarning("No GPS", "No GPS position available yet")
    
    def send_grid_to_logger(self):
        """Send current grid/county to the configured contest logger"""
        mode = self.config.get('contest_mode', 'vhf')
        logger = self.config.get('contest_logger', 'n1mm')
        logger_name = LOGGER_NAMES.get(logger, 'N1MM+')
        
        # In QSO Party mode with N1MM+, send county instead of grid
        if mode == 'qso_party' and logger == 'n1mm':
            if not self.current_county:
                messagebox.showwarning("No County", "Please set a county first in Settings → QSO Party")
                return
            
            if hasattr(self, 'radio_updater') and self.radio_updater:
                self.radio_updater.send_n1mm_roverqth_county(self.current_county)
                self.add_alert(f"Sent to {logger_name}: {self.current_county}")
                self.voice.announce(f"{logger_name} updated to {self.current_county}", category="operational")
            else:
                messagebox.showerror("Error", "Radio updater not initialized")
        else:
            # Normal grid mode
            if self.current_grid == "----":
                messagebox.showwarning("No GPS", "No GPS position available yet")
                return
            
            if hasattr(self, 'radio_updater') and self.radio_updater:
                self.radio_updater.send_logger_grid(self.current_grid)
                self.add_alert(f"Sent to {logger_name}: {self.current_grid}")
                self.voice.announce(f"{logger_name} updated to {self.current_grid}", category="operational")
            else:
                messagebox.showerror("Error", "Radio updater not initialized")
    
    def _refresh_com_ports(self):
        """Refresh list of available COM ports"""
        try:
            import serial.tools.list_ports
            ports = [port.device for port in serial.tools.list_ports.comports()]
            ports.sort()
            
            if hasattr(self, 'gps_port_combo'):
                current = self.gps_port_var.get()
                self.gps_port_combo['values'] = ports
                # Keep current selection if still valid
                if current and current not in ports:
                    # Add current value even if not detected (might be valid but not showing)
                    self.gps_port_combo['values'] = [current] + ports
        except ImportError:
            # pyserial not installed - just allow manual entry
            if hasattr(self, 'gps_port_combo'):
                self.gps_port_combo['values'] = ['COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8']
    
    def _reconnect_gps(self):
        """Reconnect GPS with new port setting"""
        new_port = self.gps_port_var.get()

        if not new_port:
            messagebox.showwarning("GPS", "Please select a COM port")
            return

        # Save port and baud rate to config
        self.config['gps_port'] = new_port
        baud_str = self.gps_baudrate_var.get() if hasattr(self, 'gps_baudrate_var') else 'Auto'
        self.config['gps_baudrate'] = None if baud_str == 'Auto' else int(baud_str)
        self.save_config()

        self._reconnect_gps_internal()

    def _reconnect_gps_internal(self):
        """Internal GPS reconnect using current config settings."""
        port = self.config.get('gps_port', 'COM3')

        # Stop existing GPS monitor
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            try:
                self.gps_monitor.stop()
                self.add_alert(f"GPS: Disconnected")
            except Exception as e:
                print(f"GPS: Error stopping: {e}")

        # Start new GPS monitor
        try:
            grid_precision = self.config.get('grid_precision', 4)
            self.gps_monitor = GPSMonitor(
                port,
                self.on_gps_update,
                grid_precision,
                lock_callback=self.on_gps_lock_change,
                baudrate=self.config.get('gps_baudrate', None),
                status_callback=self._on_gps_status_update
            )
            self.gps_monitor.start()
            self.add_alert(f"GPS: Connecting to {port}...")
        except Exception as e:
            self.add_alert(f"GPS: Failed to connect - {e}")
            messagebox.showerror("GPS Error", f"Could not connect to {port}:\n{e}")
    
    def _on_gps_baud_changed(self, event=None):
        """Handle baud rate dropdown change — sends UBX command to GPS device."""
        selected = self.gps_baudrate_var.get()

        if selected == 'Auto':
            self.config['gps_baudrate'] = None
            self.save_config()
            self._update_rate_combo_state()
            self.add_alert("GPS: Baud rate set to Auto-detect")
            return

        new_rate = int(selected)

        if not hasattr(self, 'gps_monitor') or not self.gps_monitor:
            # No active GPS — just save preference
            self.config['gps_baudrate'] = new_rate
            self.save_config()
            self._update_rate_combo_state()
            self.add_alert(f"GPS: Baud rate preference saved as {new_rate}")
            return

        current_rate = self.gps_monitor.detected_baudrate or 9600
        if new_rate == current_rate:
            self.config['gps_baudrate'] = new_rate
            self.save_config()
            self._update_rate_combo_state()
            return

        # Confirm before reprogramming the GPS device
        if not messagebox.askyesno("Change GPS Baud Rate",
                f"This will reprogram the GPS device to output at {new_rate} baud.\n\n"
                f"The GPS will briefly disconnect during the change.\n\n"
                f"Proceed?"):
            # Revert dropdown
            self.gps_baudrate_var.set(str(current_rate) if current_rate else 'Auto')
            return

        self.add_alert(f"GPS: Changing baud rate to {new_rate}...")
        self.gps_baud_status_var.set(f"Changing to {new_rate}...")

        def do_change():
            success = False
            try:
                self.gps_monitor.stop()
                time.sleep(1.0)  # Give Windows time to release COM port
                success = self.gps_monitor.change_baudrate(new_rate)
            except Exception as e:
                print(f"GPS: Baud rate change error: {e}")
                success = False

            def on_complete():
                if success:
                    self.config['gps_baudrate'] = new_rate
                    self.save_config()
                    self.gps_baud_status_var.set(f"Active: {new_rate} baud")
                    self.add_alert(f"GPS: Baud rate changed to {new_rate}")
                    if hasattr(self, 'voice') and self.voice:
                        self.voice.announce(f"GPS baud rate changed to {new_rate}", category="operational")
                    self._update_rate_combo_state()
                else:
                    self.gps_baud_status_var.set("Change failed!")
                    self.add_alert(f"GPS: Baud rate change FAILED")
                    messagebox.showerror("GPS Error",
                        f"Failed to change GPS baud rate to {new_rate}.\n"
                        f"The GPS may need to be power-cycled.")
                    # Revert dropdown
                    self.gps_baudrate_var.set(str(current_rate) if current_rate else 'Auto')

                # Restart GPS monitor
                self._reconnect_gps_internal()

            self.root.after(0, on_complete)

        threading.Thread(target=do_change, daemon=True).start()

    def _on_gps_rate_changed(self, event=None):
        """Handle update rate dropdown change."""
        hz = int(self.gps_update_rate_var.get())

        if not hasattr(self, 'gps_monitor') or not self.gps_monitor:
            self.config['gps_update_rate_hz'] = hz
            self.save_config()
            return

        current_baud = self.gps_monitor.detected_baudrate or 9600
        if hz > 1 and current_baud < 19200:
            messagebox.showwarning("GPS Update Rate",
                f"Update rates above 1Hz require at least 19200 baud.\n"
                f"Current baud rate is {current_baud}.\n"
                f"Please increase the baud rate first.")
            self.gps_update_rate_var.set('1')
            return

        # Stop monitor, send command, restart
        def do_rate_change():
            success = False
            try:
                self.gps_monitor.stop()
                time.sleep(1.0)  # Give Windows time to release COM port
                success = self.gps_monitor.change_update_rate(hz)
            except Exception as e:
                print(f"GPS: Update rate change error: {e}")
                success = False

            def on_complete():
                if success:
                    self.config['gps_update_rate_hz'] = hz
                    self.save_config()
                    self.add_alert(f"GPS: Update rate set to {hz}Hz")
                else:
                    self.add_alert(f"GPS: Failed to set update rate")
                    self.gps_update_rate_var.set('1')

                self._reconnect_gps_internal()

            self.root.after(0, on_complete)

        threading.Thread(target=do_rate_change, daemon=True).start()

    def _auto_detect_gps_baud(self):
        """Run baud rate auto-detection in background thread."""
        port = self.gps_port_var.get() if hasattr(self, 'gps_port_var') else self.config.get('gps_port', 'COM3')
        if not port:
            messagebox.showwarning("GPS", "Please select a COM port first")
            return

        self.gps_baud_status_var.set("Detecting...")
        self.add_alert(f"GPS: Auto-detecting baud rate on {port}...")

        # Stop existing monitor if running
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            try:
                self.gps_monitor.stop()
                time.sleep(0.5)
            except Exception:
                pass

        def do_detect():
            from modules.gps_monitor import GPSMonitor as TempGPS
            temp_monitor = TempGPS(port, lambda *a: None)
            temp_monitor.running = True
            detected = temp_monitor.auto_detect_baudrate()
            temp_monitor.running = False

            def on_result():
                if detected:
                    self.gps_baudrate_var.set(str(detected))
                    self.gps_baud_status_var.set(f"Detected: {detected} baud")
                    self.config['gps_baudrate'] = detected
                    self.save_config()
                    self.add_alert(f"GPS: Auto-detected {detected} baud")
                    self._update_rate_combo_state()
                else:
                    self.gps_baud_status_var.set("Detection failed!")
                    self.add_alert(f"GPS: Auto-detect failed - check connection")

                # Restart monitor with detected rate
                self._reconnect_gps_internal()

            self.root.after(0, on_result)

        threading.Thread(target=do_detect, daemon=True).start()

    def _update_rate_combo_state(self):
        """Enable/disable update rate dropdown based on current baud rate."""
        if not hasattr(self, 'gps_rate_combo'):
            return

        baud_str = self.gps_baudrate_var.get() if hasattr(self, 'gps_baudrate_var') else 'Auto'
        if baud_str == 'Auto':
            # Check actual detected rate
            if hasattr(self, 'gps_monitor') and self.gps_monitor:
                baud = self.gps_monitor.detected_baudrate or 9600
            else:
                baud = 9600
        else:
            baud = int(baud_str)

        if baud >= 19200:
            self.gps_rate_combo.config(state='readonly')
        else:
            self.gps_rate_combo.config(state='disabled')
            self.gps_update_rate_var.set('1')

    def _on_gps_status_update(self, status):
        """Called from GPSMonitor thread with baud rate detection status.
        Schedules UI updates via root.after."""
        def update():
            if not hasattr(self, 'gps_baud_status_var'):
                return

            if status.get('detecting'):
                rate = status.get('trying_rate', '?')
                self.gps_baud_status_var.set(f"Trying {rate}...")
            elif 'detected_rate' in status:
                rate = status['detected_rate']
                if rate:
                    self.gps_baud_status_var.set(f"Active: {rate} baud")
                    if hasattr(self, 'gps_baudrate_var'):
                        self.gps_baudrate_var.set(str(rate))
                    self._update_rate_combo_state()
                else:
                    self.gps_baud_status_var.set("No GPS detected")
            elif status.get('connected'):
                rate = status.get('detected_rate', '?')
                self.gps_baud_status_var.set(f"Active: {rate} baud")
                self._update_rate_combo_state()

        self.root.after(0, update)

    # ── GPS Time Sync Methods ──────────────────────────────────────────────

    def _check_time_sync_admin(self):
        """Check admin privileges and show warning if GPS Time Sync enabled without admin."""
        from modules.gps_monitor import GPSMonitor
        enabled = self.config.get('gps_time_sync_enabled', False)

        if enabled and not GPSMonitor.is_admin():
            self.gps_admin_warning_var.set(
                "⚠ GPS Time Sync requires Co-Pilot to be launched as Administrator")
            self.gps_time_sync_var.set(False)
        else:
            self.gps_admin_warning_var.set("")

    def _on_time_sync_toggle(self):
        """Handle GPS Time Sync enable/disable toggle."""
        from modules.gps_monitor import GPSMonitor
        enabled = self.gps_time_sync_var.get()

        if enabled and not GPSMonitor.is_admin():
            self.gps_admin_warning_var.set(
                "⚠ GPS Time Sync requires Co-Pilot to be launched as Administrator")
            self.gps_time_sync_var.set(False)
            self.add_alert("GPS Time Sync: Requires Administrator privileges — feature disabled")
            messagebox.showwarning("GPS Time Sync",
                "GPS Time Sync requires Administrator privileges to set the system clock.\n\n"
                "Please restart Co-Pilot as Administrator (right-click → Run as administrator).")
            return

        self.gps_admin_warning_var.set("")
        self.config['gps_time_sync_enabled'] = enabled
        self.save_config()

        if enabled:
            self.add_alert("GPS Time Sync: Enabled")
            # Sync immediately, then start scheduled timer
            self._time_sync_now()
            self._start_time_sync_timer()
        else:
            self._stop_time_sync_timer()
            # If intermittent mode had closed the port, reopen it
            if self._intermittent_port_closed:
                self._intermittent_port_closed = False
                self._reconnect_gps_internal()
            self.add_alert("GPS Time Sync: Disabled")
            self.gps_sync_status_var.set("Disabled")

    def _on_sync_interval_changed(self, event=None):
        """Handle sync interval dropdown change."""
        val = self.gps_sync_interval_var.get()
        if val == 'Manual':
            self.config['gps_time_sync_interval_minutes'] = 0
        else:
            self.config['gps_time_sync_interval_minutes'] = int(val)
        self.save_config()

        # Restart timer with new interval
        if self.config.get('gps_time_sync_enabled', False):
            self._stop_time_sync_timer()
            if val != 'Manual':
                self._start_time_sync_timer()
            self.add_alert(f"GPS Time Sync: Interval set to {val}")

    def _on_intermittent_toggle(self):
        """Handle intermittent mode toggle."""
        enabled = self.gps_intermittent_var.get()
        self.config['gps_time_sync_intermittent'] = enabled
        self.save_config()

        if not enabled and self._intermittent_port_closed:
            # Re-open GPS port
            self._intermittent_port_closed = False
            self._reconnect_gps_internal()
            self.add_alert("GPS Time Sync: Intermittent mode off — GPS port reopened")
        elif enabled:
            self.add_alert("GPS Time Sync: Intermittent mode enabled")

    def _update_intermittent_state(self):
        """Enable/disable intermittent mode checkbox based on conditions.

        Intermittent mode is only available when:
        - Contest Mode is Daily DX
        - GPS Logger is not active
        - Speed is effectively zero (< 2 mph)
        """
        if not hasattr(self, 'gps_intermittent_cb'):
            return

        suppressed = False
        reason = ""

        mode = self.config.get('contest_mode', 'vhf')
        if mode in ('vhf', '222up', 'qso_party'):
            suppressed = True
            reason = f"Intermittent mode disabled: {CONTEST_MODES.get(mode, mode)} mode"
        elif hasattr(self, 'gps_logger_active') and self.gps_logger_active:
            suppressed = True
            reason = "Intermittent mode disabled: GPS Logger active"
        elif hasattr(self, 'gps_monitor') and self.gps_monitor:
            speed = self.gps_monitor.speed_mph
            if speed is not None and speed > 2.0:
                suppressed = True
                reason = "Intermittent mode disabled: Vehicle in motion"

        if suppressed:
            self.gps_intermittent_cb.config(state='disabled')
            self.gps_intermittent_hint.config(text=f"({reason})")
            # If currently in intermittent mode with port closed, reopen
            if self._intermittent_port_closed:
                self._intermittent_port_closed = False
                self._reconnect_gps_internal()
        else:
            self.gps_intermittent_cb.config(state='normal')
            self.gps_intermittent_hint.config(
                text="(close GPS between syncs to reduce RF noise)")

    def _start_time_sync_timer(self):
        """Start the periodic time sync timer."""
        self._stop_time_sync_timer()

        interval_min = self.config.get('gps_time_sync_interval_minutes', 5)
        if interval_min <= 0:
            return  # Manual only

        interval_ms = interval_min * 60 * 1000

        def tick():
            if not self.config.get('gps_time_sync_enabled', False):
                return
            self._perform_time_sync()
            self._time_sync_timer_id = self.root.after(interval_ms, tick)

        self._time_sync_timer_id = self.root.after(interval_ms, tick)

    def _stop_time_sync_timer(self):
        """Cancel the periodic time sync timer."""
        if self._time_sync_timer_id is not None:
            try:
                self.root.after_cancel(self._time_sync_timer_id)
            except Exception:
                pass
            self._time_sync_timer_id = None

    def _time_sync_now(self):
        """Manual 'Sync Now' button handler."""
        if not self.config.get('gps_time_sync_enabled', False):
            # Allow manual sync even when disabled — just check admin
            from modules.gps_monitor import GPSMonitor
            if not GPSMonitor.is_admin():
                messagebox.showwarning("GPS Time Sync",
                    "Administrator privileges required to set the system clock.")
                return

        self.gps_sync_status_var.set("Syncing...")
        self._perform_time_sync()

    def _perform_time_sync(self):
        """Execute a single GPS time sync operation in a background thread."""
        intermittent = (self.config.get('gps_time_sync_intermittent', False)
                        and not self._is_intermittent_suppressed())

        def do_sync():
            result = None
            try:
                monitor = self.gps_monitor if hasattr(self, 'gps_monitor') else None

                if monitor is None:
                    result = {'success': False, 'error': 'GPS monitor not running'}
                    return

                # If intermittent mode had port closed, reopen and wait for fix
                if intermittent and self._intermittent_port_closed:
                    self.root.after(0, lambda: self.gps_sync_status_var.set(
                        "Opening GPS port..."))
                    self._intermittent_port_closed = False
                    self.root.after(0, self._reconnect_gps_internal)
                    # Wait up to 15s for a FRESH GPS fix (must have recent GPRMC)
                    for _ in range(30):
                        time.sleep(0.5)
                        if (monitor.gps_fix_quality >= 1
                                and monitor.gps_datetime_utc is not None
                                and monitor._gps_datetime_monotonic > 0
                                and (time.monotonic() - monitor._gps_datetime_monotonic) < 5.0):
                            break
                    else:
                        result = {'success': False,
                                  'error': 'Timeout waiting for fresh GPS fix'}
                        return

                # Need fix to sync
                if monitor.gps_fix_quality < 1 or monitor.gps_datetime_utc is None:
                    result = {'success': False,
                              'error': 'No GPS fix — waiting for next cycle'}
                    return

                # Verify GPS data is fresh before syncing
                gps_age = time.monotonic() - monitor._gps_datetime_monotonic
                if monitor._gps_datetime_monotonic == 0 or gps_age > 30.0:
                    result = {'success': False,
                              'error': f'GPS time data is stale ({gps_age:.0f}s old)'}
                    return

                result = monitor.sync_system_clock()

            except Exception as e:
                result = {'success': False, 'error': str(e)}
            finally:
                # Schedule UI update
                sync_result = result or {'success': False, 'error': 'Unknown error'}

                def on_complete():
                    self._on_time_sync_complete(sync_result)

                    # Intermittent: close port after sync
                    if (intermittent
                            and self.config.get('gps_time_sync_enabled', False)
                            and not self._is_intermittent_suppressed()):
                        self._close_gps_for_intermittent()

                self.root.after(0, on_complete)

        threading.Thread(target=do_sync, daemon=True).start()

    def _on_time_sync_complete(self, result):
        """Handle time sync result — update UI, log, update WSJT-X grid."""
        if result.get('success'):
            offset_ms = result.get('offset_ms', 0)
            self._time_sync_last_sync = datetime.now()
            self._time_sync_last_offset_ms = offset_ms

            fix_labels = {0: 'None', 1: 'GPS', 2: 'DGPS'}
            fix_label = fix_labels.get(result.get('fix_quality', 0), '?')
            sats = result.get('satellites', 0)

            status = (f"Last sync: {self._time_sync_last_sync.strftime('%H:%M:%S')} "
                      f"({offset_ms:+.0f}ms) | Fix: {fix_label} | Sats: {sats}")
            self.gps_sync_status_var.set(status)
            self.add_alert(f"GPS Time Sync: Clock set ({offset_ms:+.0f}ms offset)")

            # Log sync event
            self._log_time_sync(result)

            # Update WSJT-X grid squares if enabled
            if self.config.get('gps_time_sync_update_grid', True):
                self._update_wsjtx_grid_squares()

            # Update intermittent state (speed may have changed)
            self._update_intermittent_state()

        else:
            error = result.get('error', 'Unknown error')
            self.gps_sync_status_var.set(f"Sync failed: {error}")
            print(f"GPS Time Sync: Failed — {error}")
            # Show safety-blocked syncs in Alerts tab so user can see
            if 'safety limit' in error or 'stale' in error.lower():
                self.add_alert(f"⚠ GPS Time Sync BLOCKED: {error}")

    def _log_time_sync(self, result):
        """Append sync event to the time sync log file."""
        try:
            self._time_sync_log_path.parent.mkdir(exist_ok=True)
            with open(self._time_sync_log_path, 'a') as f:
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                gps_time = result.get('gps_time', '')
                if hasattr(gps_time, 'strftime'):
                    gps_time = gps_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                offset = result.get('offset_ms', 0)
                fix = result.get('fix_quality', 0)
                sats = result.get('satellites', 0)
                fix_labels = {0: 'None', 1: 'GPS', 2: 'DGPS'}
                f.write(f"{ts} | GPS={gps_time} | offset={offset:+.0f}ms "
                        f"| fix={fix_labels.get(fix, '?')} | sats={sats}\n")
        except Exception as e:
            print(f"GPS Time Sync: Log write error: {e}")

    def _update_wsjtx_grid_squares(self):
        """Write current 4-char grid to all active WSJT-X instance INI files."""
        if not hasattr(self, 'gps_monitor') or not self.gps_monitor:
            return

        grid_4char = self.current_grid
        if not grid_4char or grid_4char == '----' or len(grid_4char) < 4:
            return

        grid_4char = grid_4char[:4]  # Ensure 4-char
        updated = 0

        for instance in self.config.get('wsjt_instances', []):
            log_path = instance.get('log_path', '').strip()
            if not log_path:
                continue

            # WSJT-X INI file is in the parent of the log directory
            # Log path might be the log directory itself or contain wsjtx_log.adi
            # The INI file is at the same level: e.g., %LOCALAPPDATA%/WSJT-X/WSJT-X.ini
            try:
                log_dir = Path(log_path)
                if log_dir.is_file():
                    log_dir = log_dir.parent

                # Try common INI locations relative to log dir
                ini_candidates = [
                    log_dir / 'WSJT-X.ini',
                    log_dir.parent / 'WSJT-X.ini',
                ]

                for ini_path in ini_candidates:
                    if ini_path.exists():
                        self._write_grid_to_ini(ini_path, grid_4char)
                        updated += 1
                        break

            except Exception as e:
                print(f"GPS Time Sync: Error updating WSJT-X grid for {instance.get('name', '?')}: {e}")

        if updated > 0:
            print(f"GPS Time Sync: Updated MyGrid={grid_4char} in {updated} WSJT-X INI file(s)")

    def _write_grid_to_ini(self, ini_path, grid):
        """Write MyGrid= value to [Common] section of a WSJT-X INI file."""
        try:
            lines = ini_path.read_text(encoding='utf-8').splitlines(keepends=True)
            in_common = False
            found_mygrid = False
            new_lines = []

            for line in lines:
                stripped = line.strip()
                if stripped.startswith('['):
                    if in_common and not found_mygrid:
                        # End of [Common] without finding MyGrid — add it
                        new_lines.append(f'MyGrid={grid}\n')
                        found_mygrid = True
                    in_common = (stripped == '[Common]')
                    new_lines.append(line)
                elif in_common and stripped.startswith('MyGrid='):
                    new_lines.append(f'MyGrid={grid}\n')
                    found_mygrid = True
                else:
                    new_lines.append(line)

            # If [Common] was last section and MyGrid not found
            if in_common and not found_mygrid:
                new_lines.append(f'MyGrid={grid}\n')

            ini_path.write_text(''.join(new_lines), encoding='utf-8')

        except Exception as e:
            print(f"GPS Time Sync: Error writing grid to {ini_path}: {e}")

    def _is_intermittent_suppressed(self):
        """Check if intermittent mode should be suppressed."""
        mode = self.config.get('contest_mode', 'vhf')
        if mode in ('vhf', '222up', 'qso_party'):
            return True
        if hasattr(self, 'gps_logger_active') and self.gps_logger_active:
            return True
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            speed = self.gps_monitor.speed_mph
            if speed is not None and speed > 2.0:
                return True
        return False

    def _close_gps_for_intermittent(self):
        """Close GPS serial port for intermittent mode (RF noise reduction)."""
        if self._intermittent_port_closed:
            return

        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            try:
                self.gps_monitor.stop()
                self._intermittent_port_closed = True
                self.gps_sync_status_var.set(
                    self.gps_sync_status_var.get() + " | Port closed (intermittent)")
                print("GPS Time Sync: Port closed (intermittent mode)")
            except Exception as e:
                print(f"GPS Time Sync: Error closing port: {e}")

    # ── Voice Alert Control Methods ──────────────────────────────────────

    def _on_voice_master_toggle(self):
        """Handle master voice enable/disable toggle."""
        enabled = self.voice_enabled_var.get()
        self.voice.set_enabled(enabled)
        self.config['voice_enabled'] = enabled
        self.save_config()
        self._update_voice_category_states()
        self.add_alert(f"Voice alerts {'enabled' if enabled else 'disabled'}")

    def _on_voice_category_change(self):
        """Handle individual voice category toggle."""
        disabled = set()
        for key, var in self.voice_category_vars.items():
            if not var.get():
                disabled.add(key)
        self.voice.disabled_categories = disabled
        self.config['voice_disabled_categories'] = sorted(disabled)
        self.save_config()

    def _update_voice_category_states(self):
        """Grey out / enable individual category checkbuttons based on master switch."""
        if not hasattr(self, 'voice_category_cbs'):
            return
        state = 'normal' if self.voice_enabled_var.get() else 'disabled'
        for key, cb in self.voice_category_cbs.items():
            cb.config(state=state)

    def discover_victron(self):
        """Discover Victron devices via Bluetooth"""
        # This will be implemented in battery_monitor module
        self.add_alert("Victron discovery not yet implemented")
    
    def browse_log_path(self, index):
        """Browse for WSJT-X log file location"""
        from tkinter import filedialog
        path = filedialog.askdirectory(title=f"Select {self.config['wsjt_instances'][index]['name']} Log Directory")
        if path:
            self.wsjt_path_vars[index].set(path)
    
    def _test_slack_webhooks(self):
        """Test all configured Slack webhooks"""
        import urllib.request
        import json
        
        tested = 0
        succeeded = 0
        
        for i in range(3):
            name = self.slack_name_vars[i].get().strip()
            url = self.slack_webhook_vars[i].get().strip()
            
            if not url:
                continue
            
            tested += 1
            display_name = name if name else f"Webhook #{i+1}"
            
            try:
                # Send test message
                my_call = self.config.get('my_call', '') or 'Unknown'
                test_msg = {
                    "text": f"🧪 Test from {my_call} Co-Pilot - Slack integration working!"
                }
                
                req = urllib.request.Request(
                    url,
                    data=json.dumps(test_msg).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        self.add_alert(f"Slack: {display_name} ✓ Test sent successfully")
                        succeeded += 1
                    else:
                        self.add_alert(f"Slack: {display_name} ✗ HTTP {response.status}")
                        
            except Exception as e:
                self.add_alert(f"Slack: {display_name} ✗ {str(e)[:50]}")
        
        if tested == 0:
            messagebox.showinfo("Slack Test", "No webhook URLs configured")
        elif succeeded == tested:
            messagebox.showinfo("Slack Test", f"All {tested} webhook(s) working!")
        else:
            messagebox.showwarning("Slack Test", f"{succeeded}/{tested} webhooks succeeded. Check Alerts tab for details.")
    
    def post_to_slack(self, message):
        """Post a message to all configured Slack webhooks"""
        import urllib.request
        import json
        import threading
        
        if not self.config.get('slack_enabled', False):
            return
        
        webhooks = self.config.get('slack_webhooks', [])
        if not webhooks:
            return
        
        def send_to_webhook(name, url):
            try:
                msg_data = {"text": message}
                req = urllib.request.Request(
                    url,
                    data=json.dumps(msg_data).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        print(f"Slack: Posted to {name}")
                    else:
                        print(f"Slack: {name} returned HTTP {response.status}")
            except Exception as e:
                print(f"Slack: Error posting to {name}: {e}")
        
        # Send to all webhooks in background threads
        for webhook in webhooks:
            url = webhook.get('url', '').strip()
            name = webhook.get('name', 'unnamed').strip()
            if url:
                threading.Thread(target=send_to_webhook, args=(name, url), daemon=True).start()
    
    def save_settings(self):
        """Save settings from GUI to config"""
        # Station info
        self.config['my_call'] = self.my_call_var.get().strip().upper()
        
        self.config['gps_port'] = self.gps_port_var.get()
        # GPS baud rate and update rate
        if hasattr(self, 'gps_baudrate_var'):
            baud_str = self.gps_baudrate_var.get()
            self.config['gps_baudrate'] = None if baud_str == 'Auto' else int(baud_str)
        if hasattr(self, 'gps_update_rate_var'):
            self.config['gps_update_rate_hz'] = int(self.gps_update_rate_var.get())
        # GPS Time Sync settings
        if hasattr(self, 'gps_time_sync_var'):
            self.config['gps_time_sync_enabled'] = self.gps_time_sync_var.get()
        if hasattr(self, 'gps_sync_interval_var'):
            val = self.gps_sync_interval_var.get()
            self.config['gps_time_sync_interval_minutes'] = 0 if val == 'Manual' else int(val)
        if hasattr(self, 'gps_intermittent_var'):
            self.config['gps_time_sync_intermittent'] = self.gps_intermittent_var.get()
        if hasattr(self, 'gps_sync_grid_var'):
            self.config['gps_time_sync_update_grid'] = self.gps_sync_grid_var.get()
        # Voice alert settings
        if hasattr(self, 'voice_enabled_var'):
            self.config['voice_enabled'] = self.voice_enabled_var.get()
            self.voice.set_enabled(self.config['voice_enabled'])
        if hasattr(self, 'voice_category_vars'):
            disabled = [k for k, v in self.voice_category_vars.items() if not v.get()]
            self.config['voice_disabled_categories'] = sorted(disabled)
            self.voice.disabled_categories = set(disabled)
        self.config['victron_address'] = self.victron_addr_var.get()
        self.config['victron_key'] = self.victron_key_var.get()
        
        # QRZ credentials
        self.config['qrz_username'] = self.qrz_username_var.get()
        self.config['qrz_password'] = self.qrz_password_var.get()
        
        # Logger settings
        self.config['contest_logger'] = self.logger_var.get()
        self.config['n1mm_udp_port'] = int(self.n1mm_port_var.get())
        self.config['n3fjp_port'] = int(self.n3fjp_port_var.get())
        self.config['log4om_host'] = self.log4om_host_var.get().strip()
        self.config['log4om_port'] = int(self.log4om_port_var.get())

        # My Bands
        my_bands = [band for band, var in self.band_check_vars.items() if var.get()]
        self.config['my_bands'] = my_bands
        
        # APRS settings
        self.config['aprs_enabled'] = self.aprs_enabled_var.get()
        self.config['aprs_callsign'] = self.aprs_call_var.get()
        self.config['aprs_beacon_interval'] = int(self.aprs_beacon_var.get())
        self.config['aprs_alert_radius'] = int(self.aprs_radius_var.get())
        self.config['aprs_comment'] = self.aprs_comment_var.get()
        
        # PSK Reporter settings
        self.config['psk_enabled'] = self.psk_enabled_var.get()
        self.config['psk_vhf_radius'] = int(self.psk_vhf_radius_var.get())
        self.config['psk_hf_radius'] = int(self.psk_hf_radius_var.get())
        self.config['psk_baseline_minutes'] = int(self.psk_baseline_var.get())
        self.config['psk_alert_openings'] = self.psk_alert_openings_var.get()
        self.config['psk_alert_mspe'] = self.psk_alert_mspe_var.get()
        self.config['psk_alert_spe'] = self.psk_alert_spe_var.get()
        self.config['psk_alert_modes'] = self.psk_alert_modes_var.get()
        self.config['psk_crossref_qsy'] = self.psk_crossref_var.get()
        self.config['psk_priority_enabled'] = self.psk_priority_enabled_var.get()
        self.config['dx_priority_stations'] = self.dx_priority_stations_var.get().strip()
        self.config['ap_priority_stations'] = self.ap_priority_stations_var.get().strip()
        self.config['dx2_enabled'] = self.dx2_enabled_var.get()
        self.config['dx3_enabled'] = self.dx3_enabled_var.get()
        self.config['dx3_granularity'] = self.dx3_granularity_var.get()
        self.config['lotw_username'] = self.lotw_username_var.get().strip()
        self.config['lotw_password'] = self.lotw_password_var.get().strip()
        self.config['lotw_auto_refresh'] = self.lotw_auto_refresh_var.get()
        # Keep legacy key in sync for backward compat
        self.config['psk_priority_stations'] = self.config['dx_priority_stations']

        # SMS/Twilio settings (also saved by Notify tab's own Save button)
        if hasattr(self, 'sms_enabled_var'):
            self.config['sms_enabled'] = self.sms_enabled_var.get()
            self.config['twilio_account_sid'] = self.twilio_sid_var.get().strip()
            self.config['twilio_auth_token'] = self.twilio_token_var.get().strip()
            self.config['twilio_from_number'] = self.twilio_from_var.get().strip()
            self.config['twilio_to_number'] = self.twilio_to_var.get().strip()
            self.config['sms_on_priority'] = self.sms_priority_var.get()
            self.config['sms_on_dx2'] = self.sms_dx2_var.get()
            self.config['sms_on_dx3'] = self.sms_dx3_var.get()
            self.config['sms_on_new_grid'] = self.sms_new_grid_var.get()
            if hasattr(self, 'sms_subscribers_text'):
                self.config['sms_subscribers'] = self.sms_subscribers_text.get('1.0', tk.END).strip()

        # Slack settings
        self.config['slack_enabled'] = self.slack_enabled_var.get()
        slack_webhooks = []
        for i in range(3):
            name = self.slack_name_vars[i].get().strip()
            url = self.slack_webhook_vars[i].get().strip()
            if url:  # Only save if URL is set
                slack_webhooks.append({'name': name, 'url': url})
        self.config['slack_webhooks'] = slack_webhooks
        
        # WSJT-X instances - build from name/path/port vars
        new_instances = []
        for i in range(4):
            name = self.wsjt_name_vars[i].get().strip()
            path = self.wsjt_path_vars[i].get().strip()
            try:
                port = int(self.wsjt_port_vars[i].get())
            except ValueError:
                port = 2237 + i
            
            # Only add if name or path is set
            if name or path:
                new_instances.append({
                    'name': name,
                    'log_path': path,
                    'udp_port': port
                })
        
        self.config['wsjt_instances'] = new_instances
        
        # Update Manual Entry band dropdown
        self._update_manual_entry_bands()
        
        # Update Grid Corner available bands
        self._update_grid_corner_bands()

        # Enable/disable VHF-only tabs based on My Bands
        self._update_vhf_tab_states()

        # Rebuild PSK Band Activity panel to reflect My Bands
        if hasattr(self, 'psk_band_rows_frame'):
            self._rebuild_band_activity_labels()
        
        self.save_config()
        messagebox.showinfo("Settings", "Settings saved successfully")
        self.add_alert("Settings saved")
        
        # Stop old radio updater before creating new one
        if hasattr(self, 'radio_updater') and self.radio_updater:
            self.radio_updater.stop_listener()
        
        # Restart radio updater with new settings
        self.radio_updater = RadioUpdater(
            self.config['wsjt_instances'],
            n1mm_host=self.config.get('n1mm_udp_host', '127.0.0.1'),
            n1mm_port=self.config.get('n1mm_udp_port', 52001),
            n3fjp_host=self.config.get('n3fjp_host', '127.0.0.1'),
            n3fjp_port=self.config.get('n3fjp_port', 1100),
            log4om_host=self.config.get('log4om_host', '127.0.0.1'),
            log4om_port=self.config.get('log4om_port', 2333),
            contest_logger=self.config.get('contest_logger', 'n1mm'),
            qso_callback=self.on_qso_logged,
            location_stamper=self._stamp_qso_location
        )
        self.add_alert("Radio updater restarted with new settings")
        
        # Start/stop APRS based on settings
        if self.config['aprs_enabled']:
            self._start_aprs()
        elif hasattr(self, 'aprs_client') and self.aprs_client:
            self.aprs_client.stop()
            self.aprs_client = None
            self.add_alert("APRS stopped")
        
        # Update PSK monitor settings and sync enable state
        if self.psk_monitor:
            self.psk_monitor.vhf_radius = self.config['psk_vhf_radius']
            self.psk_monitor.hf_radius = self.config['psk_hf_radius']
            self.psk_monitor.baseline_minutes = self.config['psk_baseline_minutes']
            self.psk_monitor.alert_band_openings = self.config['psk_alert_openings']
            self.psk_monitor.alert_mspe = self.config['psk_alert_mspe']
            self.psk_monitor.alert_spe = self.config['psk_alert_spe']
            self.psk_monitor.alert_unusual_modes = self.config['psk_alert_modes']
            self.psk_monitor.alert_crossref_qsy = self.config['psk_crossref_qsy']
            self.psk_monitor.priority_enabled = self.config['psk_priority_enabled']
            self.psk_monitor.priority_stations = self._parse_priority_stations(
                self.config.get('dx_priority_stations', ''))

        # Sync priority stations to log_monitor
        if hasattr(self, 'log_monitor') and self.log_monitor:
            self.log_monitor.priority_stations = self._parse_priority_stations(
                self.config.get('dx_priority_stations', ''))
            self.log_monitor.contest_mode = self.config.get('contest_mode', 'vhf')
            self.log_monitor.priority_enabled = self.config.get('psk_priority_enabled', False)

        # Sync PriorityEngine
        if hasattr(self, 'priority_engine') and self.priority_engine:
            self.priority_engine.configure(self.config,
                                           getattr(self, 'cty_lookup', None),
                                           getattr(self, 'lotw_client', None))

        # Update priority pane visibility
        if hasattr(self, '_update_priority_pane_visibility'):
            self._update_priority_pane_visibility()

        # Start/stop PSK monitor based on settings
        if self.config['psk_enabled'] and (self.psk_monitor is None or not self.psk_monitor.running):
            self._start_psk_monitor()
        elif not self.config['psk_enabled'] and self.psk_monitor and self.psk_monitor.running:
            self._stop_psk_monitor()
        
        # Update PSK settings summary
        self.psk_settings_var.set(
            f"VHF radius: {self.config['psk_vhf_radius']} mi | "
            f"HF radius: {self.config['psk_hf_radius']} mi | "
            f"Baseline: {self.config['psk_baseline_minutes']} min"
        )
    
    def log_manual_qso(self):
        """Log manual QSO to N1MM+ and ADIF backup"""
        import datetime
        
        band = self.manual_band_var.get()
        mode = self.manual_mode_var.get()
        freq_str = self.manual_freq_var.get().strip()
        call = self.manual_call_var.get().strip().upper()
        grid = self.manual_grid_var.get().strip().upper()
        rst_sent = self.manual_rst_sent_var.get().strip()
        rst_rcvd = self.manual_rst_rcvd_var.get().strip()
        my_grid = self.manual_mygrid_var.get().strip().upper()
        
        # Use current GPS grid if form shows ---- or is empty
        if not my_grid or my_grid == '----':
            my_grid = self.current_grid if self.current_grid != '----' else ''
        
        # Validation
        if not band:
            messagebox.showwarning("Incomplete", "Please select a band")
            return
        if not call:
            messagebox.showwarning("Incomplete", "Please enter a callsign")
            return
        
        # Grid/Exchange validation depends on contest mode
        contest_mode = self.config.get('contest_mode', 'vhf')
        if contest_mode == 'qso_party':
            # QSO Party: exchange can be anything (state, serial, etc.)
            if not grid:
                messagebox.showwarning("Incomplete", "Please enter their exchange (state, serial, etc.)")
                return
        else:
            # VHF contests: require 4 or 6 char grid
            if not grid or len(grid) < 4:
                messagebox.showwarning("Incomplete", "Please enter their grid (4 or 6 chars)")
                return
        
        if not freq_str:
            messagebox.showwarning("Incomplete", "Please enter frequency")
            return
        
        try:
            freq_mhz = float(freq_str)
        except ValueError:
            messagebox.showwarning("Invalid Frequency", "Frequency must be a number (MHz)")
            return
        
        # Build QSO data structure (same format as WSJT-X QSOs)
        now = datetime.datetime.utcnow()
        qso_data = {
            'dx_call': call,
            'dx_grid': grid,
            'mode': mode,
            'freq_mhz': freq_mhz,
            'freq_hz': int(freq_mhz * 1_000_000),
            'band': band,
            'report_sent': rst_sent,
            'report_rcvd': rst_rcvd,
            'datetime_on': now,
            'datetime_off': now,
            'my_call': self.config.get('my_call', ''),
            'my_grid': my_grid or self.current_grid,
            'wsjtx_id': 'Manual',  # Source identifier for QSO Log display
        }
        
        # Send to N1MM+ via the radio updater's relay queue
        if self.radio_updater:
            self.radio_updater.queue_qso_for_relay(qso_data)
        
        # Write to ADIF backup with GPS-stamped location fields
        if self.radio_updater:
            self._stamp_qso_location(qso_data)  # Add MY_STATE, MY_CNTY, etc.
            self.radio_updater._write_qso_to_adif(qso_data)
        
        # Update QSO display
        self.on_qso_logged(qso_data)
        
        # Alert (voice announcement is handled by on_qso_logged)
        self.add_alert(f"Manual QSO: {call} on {band} {mode} - {grid}")
        
        # Clear call and exchange/grid for next QSO, keep rest
        self.manual_call_var.set('')
        self.manual_grid_var.set('')
        
        # Clear SCP results
        if hasattr(self, 'scp_listbox'):
            self.scp_listbox.delete(0, tk.END)
        if hasattr(self, 'scp_match_var'):
            self.scp_match_var.set("Type 2+ chars to search...")
        
        # Update my_grid/county based on contest mode
        contest_mode = self.config.get('contest_mode', 'vhf')
        if contest_mode == 'qso_party' and self.current_county:
            self.manual_mygrid_var.set(self.current_county)
        elif self.current_grid != '----':
            self.manual_mygrid_var.set(self.current_grid)
    
    
    def send_test_grid(self):
        """Send manually entered test grid to WSJT-X and contest logger"""
        test_grid = self.test_grid_var.get().strip().upper()
        
        if not test_grid:
            messagebox.showwarning("Invalid Grid", "Please enter a grid square")
            return
        
        # Basic validation
        if len(test_grid) not in [4, 6]:
            messagebox.showwarning("Invalid Grid", "Grid must be 4 or 6 characters (e.g., EM15 or EM15fp)")
            return
        
        # Update current grid
        self.current_grid = test_grid
        self.grid_label.config(text=test_grid)
        
        # Update logger button (main window)
        logger = self.config.get('contest_logger', 'n1mm')
        logger_name = LOGGER_NAMES.get(logger, 'N1MM+')
        self.logger_button.config(text=f"Send to {logger_name}: {test_grid}", state='normal')
        
        # Send to WSJT-X AND logger
        print(f"Test Mode: Sending test grid '{test_grid}' to WSJT-X and {logger_name}")
        self.radio_updater.update_grid(test_grid)
        self.voice.announce(f"Test grid {test_grid}")
        self.add_alert(f"TEST: Sent grid {test_grid} to WSJT-X + {logger_name}")
    
    
    def test_voice(self):
        """Test voice announcement"""
        self.voice.announce("This is a test announcement")
        self.add_alert("TEST: Voice announcement triggered")
    
    def test_victron(self):
        """Test Victron connection"""
        if self.battery_monitor:
            self.add_alert("TEST: Victron monitor is running")
        else:
            self.add_alert("TEST: Victron monitor not configured")
    
    def reload_logs(self):
        """Reload all WSJT-X logs"""
        if self.log_monitor:
            self.log_monitor.reload_logs()
            self.add_alert("Reloading WSJT-X contest logs...")
        else:
            self.add_alert("Log monitor not initialized")
    
    def send_aprs_beacon(self):
        """Send an APRS beacon immediately"""
        if hasattr(self, 'aprs_client') and self.aprs_client:
            self.aprs_client.send_beacon_now()
            self.add_alert("APRS beacon sent")
        else:
            self.add_alert("APRS not enabled - enable in Settings")
    
    def show_aprs_stats(self):
        """Show APRS connection statistics"""
        if hasattr(self, 'aprs_client') and self.aprs_client:
            stats = self.aprs_client.get_stats()
            status = "Connected" if stats['connected'] else "Disconnected"
            last_beacon = stats['last_beacon'].strftime('%H:%M:%S') if stats['last_beacon'] else "Never"
            
            msg = (f"APRS Status: {status}\n"
                   f"Packets received: {stats['packets_received']}\n"
                   f"Beacons sent: {stats['beacons_sent']}\n"
                   f"Last beacon: {last_beacon}\n"
                   f"Stations seen: {stats['stations_seen']}")
            
            messagebox.showinfo("APRS Statistics", msg)
            
            # Update status labels (both Settings tab and APRS Messages tab)
            self.aprs_status_var.set(f"APRS: {status} | RX:{stats['packets_received']} TX:{stats['beacons_sent']}")
            if hasattr(self, 'aprs_msg_status_var'):
                callsign = self.config.get('aprs_callsign', 'N5ZY')
                self.aprs_msg_status_var.set(f"Connected as {callsign}" if stats['connected'] else "Disconnected")
        else:
            messagebox.showinfo("APRS Statistics", "APRS not enabled.\n\nEnable in Settings tab.")
            self.aprs_status_var.set("APRS: Disabled")
            if hasattr(self, 'aprs_msg_status_var'):
                self.aprs_msg_status_var.set("Not connected")

def main():
    root = tk.Tk()
    app = CoPilotApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
