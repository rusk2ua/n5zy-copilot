#!/usr/bin/env python3
"""
N5ZY VHF Contest Co-Pilot
Main application
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json
import os
from pathlib import Path

# Import our modules (will create these next)
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
from modules.county_lookup import CountyLookupService

# Contest mode constants
CONTEST_MODES = {
    'vhf': 'VHF Contest (4-char grid)',
    '222up': '222 MHz and Up (6-char grid)',
    'qso_party': 'State QSO Party (County)',
}

# HF bands (including WARC bands) for QSO parties
HF_BANDS = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']
VHF_BANDS = ['6m', '2m', '1.25m', '70cm', '33cm', '23cm', '13cm', '9cm', '5cm', '3cm']
ALL_BANDS = HF_BANDS + VHF_BANDS

class CoPilotApp:
    VERSION = "1.8.31"
    
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
        self.qsy_advisor = QSYAdvisor()
        self.qsy_advisor.set_qsy_callback(self.on_qsy_opportunity)
        self.grid_boundary = GridBoundaryMonitor(self.on_boundary_announcement)
        
        # Current state
        self.current_grid = "----"
        self.current_county = ""  # For QSO Party mode (abbreviation sent to N1MM+)
        self.current_lat = None   # GPS latitude for ADIF stamping
        self.current_lon = None   # GPS longitude for ADIF stamping
        self.current_county_info = None  # Full CountyInfo from shapefile lookup
        self._last_county_name = ""      # For detecting county changes
        self.battery_voltage = 0.0
        self.battery_current = 0.0
        self.battery_soc = 100.0
        
        # QSO Party data (loaded from N1MM+ QSOParty.sec file)
        self.qso_parties = {}
        self._load_qsoparty_data()
        
        # County lookup service (for QSO Party mode auto-detection)
        self.county_lookup = None
        self._load_county_shapefile()
        
        # Ignore list for "calling me" alerts: {callsign: expire_timestamp}
        self.ignored_stations = {}
        self.ignore_duration_minutes = 30
        
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
                self.config = json.load(f)
        else:
            # Default configuration
            self.config = {
                'gps_port': 'COM3',
                'victron_address': '',
                'victron_key': '',
                'grid_precision': 4,  # 4-char for VHF contests, 6-char for 222 and Up
                # Contest mode settings
                'contest_mode': 'vhf',  # 'vhf', '222up', or 'qso_party'
                'qso_party_code': 'OK',  # QSO party code (e.g., OK, TX, 7QP, MAQP)
                'qso_party_county': '',  # Current county abbreviation
                'qsoparty_file': get_default_qsoparty_path(),  # N1MM+ QSOParty.sec file
                'county_shapefile': 'data/us_counties_10m.shp',  # US county boundaries shapefile
                'county_auto_detect': True,  # Auto-detect county from GPS in QSO Party mode
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
                'active_bands': ['50', '144', '222', '432', '902', '1296', '10368'],
                # APRS-IS settings
                'aprs_enabled': False,
                'aprs_callsign': 'N5ZY',  # Your callsign (add -9 for mobile SSID if desired)
                'aprs_beacon_interval': 600,  # 10 minutes
                'aprs_alert_radius': 10,  # miles
                'aprs_comment': 'N5ZY.ORG Rover!',  # Beacon comment
                # Grid boundary alerts
                'grid_boundary_alerts': False,  # Voice alerts when approaching grid edges
            }
            self.save_config()
    
    def _load_qsoparty_data(self):
        """Load QSO Party data from N1MM+ QSOParty.sec file"""
        filepath = self.config.get('qsoparty_file', get_default_qsoparty_path())
        self.qso_parties = parse_qsoparty_file(filepath)
        if self.qso_parties:
            print(f"Loaded {len(self.qso_parties)} QSO parties")
    
    def _load_county_shapefile(self):
        """Load county boundaries shapefile for QSO Party auto-detection"""
        shapefile_path = self.config.get('county_shapefile', 'data/us_counties_10m.shp')
        
        print(f"County Lookup: Looking for shapefile at: {shapefile_path}")
        
        if not Path(shapefile_path).exists():
            print(f"  ERROR: Shapefile not found!")
            print(f"  Full path: {Path(shapefile_path).absolute()}")
            print("  Auto county detection will be disabled.")
            self.county_lookup = None
            return
        
        try:
            from modules.county_lookup import CountyLookupService
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
        """Save configuration to JSON file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def create_gui(self):
        """Create the main GUI"""
        
        # Top status bar
        status_frame = ttk.Frame(self.root, relief=tk.RAISED, borderwidth=2)
        status_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Grid display (large)
        ttk.Label(status_frame, text="Current Grid:", font=('Arial', 12)).pack(side=tk.LEFT, padx=5)
        self.grid_label = ttk.Label(status_frame, text=self.current_grid, 
                                     font=('Arial', 24, 'bold'), foreground='blue')
        self.grid_label.pack(side=tk.LEFT, padx=10)
        
        # County display (for QSO Party mode - initially hidden)
        self.county_frame = ttk.Frame(status_frame)
        ttk.Label(self.county_frame, text="County:", font=('Arial', 12)).pack(side=tk.LEFT, padx=5)
        self.county_label = ttk.Label(self.county_frame, text="----", 
                                       font=('Arial', 18, 'bold'), foreground='purple')
        self.county_label.pack(side=tk.LEFT, padx=5)
        # Will be shown/hidden by _update_contest_mode_ui()
        
        # Battery voltage (always visible)
        ttk.Label(status_frame, text="Battery:", font=('Arial', 12)).pack(side=tk.LEFT, padx=20)
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
        logger_name = "N1MM+" if self.config.get('contest_logger', 'n1mm') == 'n1mm' else "N3FJP"
        self.logger_button = ttk.Button(status_frame, text=f"Send to {logger_name}: ----", 
                                       command=self.send_grid_to_logger, state='disabled')
        self.logger_button.pack(side=tk.RIGHT, padx=10)
        
        # APRS enable checkbox (mirrors the one in Settings)
        self.aprs_enabled_var = tk.BooleanVar(value=self.config.get('aprs_enabled', False))
        self.aprs_checkbox = ttk.Checkbutton(status_frame, text="APRS", 
                                              variable=self.aprs_enabled_var,
                                              command=self.toggle_aprs)
        self.aprs_checkbox.pack(side=tk.RIGHT, padx=5)
        
        # Notebook for tabs
        notebook = ttk.Notebook(self.root)
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
        
        # Tab 5: QSY Advisor - browse station database
        self.qsy_tab = self.create_qsy_advisor_tab(notebook)
        notebook.add(self.qsy_tab, text="QSY Advisor")
        
        # Tab 6: Grid Corner - rover-to-rover QSO tracker (special use at grid corners)
        self.grid_corner_tab = self.create_grid_corner_tab(notebook)
        notebook.add(self.grid_corner_tab, text="Grid Corner")
        
        # Tab 7: Settings
        self.settings_tab = self.create_settings_tab(notebook)
        notebook.add(self.settings_tab, text="Settings")
        
        # Tab 8: Test Mode
        self.test_tab = self.create_test_tab(notebook)
        notebook.add(self.test_tab, text="Test Mode")
        
        # Tab 9: About / Support
        self.about_tab = self.create_about_tab(notebook)
        notebook.add(self.about_tab, text="About")
        
        # Initialize bands from config after tabs are created
        self._update_manual_entry_bands()
        self._update_grid_corner_bands()
        
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
        
        # Auto-detect county from GPS checkbox
        self.county_auto_detect_var = tk.BooleanVar(value=self.config.get('county_auto_detect', True))
        auto_detect_cb = ttk.Checkbutton(self.qso_party_frame, text="Auto-detect county from GPS",
                                         variable=self.county_auto_detect_var,
                                         command=self._on_county_auto_detect_change)
        auto_detect_cb.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(5, 2))
        
        # Status of county shapefile
        shapefile_path = self.config.get('county_shapefile', 'data/us_counties_10m.shp')
        if self.county_lookup and self.county_lookup.is_loaded:
            shapefile_status = f"✓ {self.county_lookup.county_count} counties loaded"
            status_color = "green"
        else:
            shapefile_status = f"✗ Shapefile not found: {shapefile_path}"
            status_color = "red"
        ttk.Label(self.qso_party_frame, text=shapefile_status, 
                 foreground=status_color).grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=2)
        
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
        
        # GPS Settings
        gps_frame = ttk.LabelFrame(frame, text="GPS Settings", padding=10)
        gps_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(gps_frame, text="GPS COM Port:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.gps_port_var = tk.StringVar(value=self.config['gps_port'])
        ttk.Entry(gps_frame, textvariable=self.gps_port_var, width=15).grid(row=0, column=1, pady=2)
        
        # Grid boundary alerts toggle
        self.grid_boundary_var = tk.BooleanVar(value=self.config.get('grid_boundary_alerts', False))
        ttk.Checkbutton(gps_frame, text="Grid Boundary Alerts", 
                       variable=self.grid_boundary_var,
                       command=self.toggle_grid_boundary_alerts).grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Label(gps_frame, text="Voice alerts at 5mi, 2mi, 1mi, 100yd, 50yd when approaching boundary", 
                 foreground="gray").grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=5)
        
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
        
        # Contest Logger Settings (N1MM+ or N3FJP)
        logger_frame = ttk.LabelFrame(frame, text="Contest Logger", padding=10)
        logger_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Logger selection dropdown
        ttk.Label(logger_frame, text="Logger:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.logger_var = tk.StringVar(value=self.config.get('contest_logger', 'n1mm'))
        logger_combo = ttk.Combobox(logger_frame, textvariable=self.logger_var,
                                    values=['n1mm', 'n3fjp'], width=10, state='readonly')
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
        
        # Show/hide appropriate settings
        self._update_logger_ui()
        
        # My Bands (what bands you have equipment for)
        bands_frame = ttk.LabelFrame(frame, text="My Bands (equipment you have)", padding=10)
        bands_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(bands_frame, text="Check the bands you have equipment for. Used in Manual Entry and Grid Corner tabs.",
                 foreground="gray").grid(row=0, column=0, columnspan=8, sticky=tk.W, pady=(0,5))
        
        # All possible bands
        self.all_bands = ['160m', '80m', '40m', '20m', '15m', '10m',  # HF
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
        
        entry_frame = ttk.LabelFrame(frame, text="Manual QSO Entry (Phone/CW)", padding=10)
        entry_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Row 0: Band selection (includes HF for QSO parties)
        ttk.Label(entry_frame, text="Band:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.manual_band_var = tk.StringVar()
        self.manual_band_combo = ttk.Combobox(entry_frame, textvariable=self.manual_band_var, 
                                   values=ALL_BANDS, width=10, state='readonly')
        self.manual_band_combo.grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        self.manual_band_combo.bind('<<ComboboxSelected>>', self._on_band_select)
        
        # Row 1: Mode selection
        ttk.Label(entry_frame, text="Mode:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.manual_mode_var = tk.StringVar(value='SSB')
        mode_frame = ttk.Frame(entry_frame)
        mode_frame.grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Radiobutton(mode_frame, text="SSB", variable=self.manual_mode_var, 
                       value='SSB', command=self._on_mode_change).pack(side=tk.LEFT)
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
        ttk.Label(freq_frame, text="MHz (e.g. 144.200, 432.100, 1296.100)").pack(side=tk.LEFT, padx=5)
        
        # Row 3: Callsign
        ttk.Label(entry_frame, text="Callsign:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.manual_call_var = tk.StringVar()
        call_entry = ttk.Entry(entry_frame, textvariable=self.manual_call_var, width=15)
        call_entry.grid(row=3, column=1, sticky=tk.W, pady=5, padx=5)
        # Auto-uppercase
        self.manual_call_var.trace_add('write', lambda *args: self.manual_call_var.set(self.manual_call_var.get().upper()))
        
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
        ttk.Label(entry_frame, text="For SSB/FM contacts made outside WSJT-X. QSO is sent to N1MM+ and saved to ADIF backup.",
                 foreground="gray").grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=5)
        
        return frame
    
    def _on_band_select(self, event=None):
        """Auto-fill typical frequency when band is selected"""
        band = self.manual_band_var.get()
        mode = self.manual_mode_var.get()
        
        # Typical calling frequencies
        freq_map = {
            '6m': {'SSB': '50.125', 'FM': '52.525', 'CW': '50.090'},
            '2m': {'SSB': '144.200', 'FM': '146.520', 'CW': '144.050'},
            '1.25m': {'SSB': '222.100', 'FM': '223.500', 'CW': '222.050'},
            '70cm': {'SSB': '432.100', 'FM': '446.000', 'CW': '432.050'},
            '33cm': {'SSB': '903.100', 'FM': '903.125', 'CW': '902.050'},
            '23cm': {'SSB': '1296.100', 'FM': '1294.500', 'CW': '1296.050'},
            '13cm': {'SSB': '2304.100', 'FM': '2304.100', 'CW': '2304.050'},
            '9cm': {'SSB': '3456.100', 'FM': '3456.100', 'CW': '3456.050'},
            '6cm': {'SSB': '5760.100', 'FM': '5760.100', 'CW': '5760.050'},
            '3cm': {'SSB': '10368.100', 'FM': '10368.100', 'CW': '10368.050'},
        }
        
        if band in freq_map:
            self.manual_freq_var.set(freq_map[band].get(mode, freq_map[band]['SSB']))
    
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
        
        # Test button - sends to WSJT-X AND logger (N1MM+ or N3FJP)
        logger_name = "N1MM+" if self.config.get('contest_logger', 'n1mm') == 'n1mm' else "N3FJP"
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
                self.voice.announce(f"QSO logged. {qso_data['dx_call']}")
            
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
        """Create PSK Reporter Monitor tab for band activity and propagation"""
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
        
        # Left side - Propagation Alerts
        alerts_frame = ttk.LabelFrame(content_frame, text="Propagation Alerts", padding=5)
        alerts_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,5))
        
        # Alerts treeview - shows nearby station and far station they worked
        alert_columns = ('time', 'pri', 'band', 'nearby', 'far', 'dist', 'dir', 'prop', 'mode')
        self.psk_alert_tree = ttk.Treeview(alerts_frame, columns=alert_columns, 
                                           show='headings', height=12)
        
        self.psk_alert_tree.heading('time', text='Time')
        self.psk_alert_tree.heading('pri', text='P')
        self.psk_alert_tree.heading('band', text='Band')
        self.psk_alert_tree.heading('nearby', text='Nearby')
        self.psk_alert_tree.heading('far', text='Far (Try!)')
        self.psk_alert_tree.heading('dist', text='Dist')
        self.psk_alert_tree.heading('dir', text='Dir')
        self.psk_alert_tree.heading('prop', text='Prop')
        self.psk_alert_tree.heading('mode', text='Mode')
        
        self.psk_alert_tree.column('time', width=50)
        self.psk_alert_tree.column('pri', width=25)
        self.psk_alert_tree.column('band', width=45)
        self.psk_alert_tree.column('nearby', width=75)
        self.psk_alert_tree.column('far', width=75)
        self.psk_alert_tree.column('dist', width=45)
        self.psk_alert_tree.column('dir', width=30)
        self.psk_alert_tree.column('prop', width=50)
        self.psk_alert_tree.column('mode', width=45)
        
        # Configure row colors for priority levels
        self.psk_alert_tree.tag_configure('p1', foreground='red', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('p2', foreground='orange', font=('Arial', 9, 'bold'))
        self.psk_alert_tree.tag_configure('p3', foreground='goldenrod')
        self.psk_alert_tree.tag_configure('p4', foreground='green')
        self.psk_alert_tree.tag_configure('info', foreground='gray')
        
        psk_scrollbar = ttk.Scrollbar(alerts_frame, orient=tk.VERTICAL, 
                                      command=self.psk_alert_tree.yview)
        self.psk_alert_tree.configure(yscrollcommand=psk_scrollbar.set)
        
        self.psk_alert_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        psk_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Double-click to open PSK Reporter
        self.psk_alert_tree.bind('<Double-1>', self._psk_open_pskreporter)
        
        # Right side - Band Activity Summary
        activity_frame = ttk.LabelFrame(content_frame, text="Band Activity", padding=5)
        activity_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5,0))
        
        # Band activity labels
        self.band_activity_labels = {}
        bands_order = ['23cm', '33cm', '70cm', '1.25m', '2m', '6m']
        
        for band in bands_order:
            row_frame = ttk.Frame(activity_frame)
            row_frame.pack(fill=tk.X, pady=2)
            
            ttk.Label(row_frame, text=f"{band}:", width=6).pack(side=tk.LEFT)
            
            activity_var = tk.StringVar(value="--")
            self.band_activity_labels[band] = activity_var
            
            lbl = ttk.Label(row_frame, textvariable=activity_var, width=8,
                           font=('Arial', 10, 'bold'))
            lbl.pack(side=tk.LEFT, padx=5)
        
        # Legend
        ttk.Separator(activity_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        ttk.Label(activity_frame, text="Priority:", font=('Arial', 9, 'bold')).pack(anchor=tk.W)
        ttk.Label(activity_frame, text="P1! MSp-E UHF+", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(activity_frame, text="P2! MSp-E 2m", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(activity_frame, text="P2  Sp-E UHF", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(activity_frame, text="P3  Sp-E 2m/6m", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(activity_frame, text="P4  Tropo", font=('Arial', 8)).pack(anchor=tk.W)
        ttk.Label(activity_frame, text="--  LOS/Other", font=('Arial', 8)).pack(anchor=tk.W)
        
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
    
    def _on_psk_spot(self, spot_data):
        """Handle individual PSK spot (for display in tree)"""
        try:
            from datetime import datetime
            
            now = datetime.now()
            time_str = now.strftime('%H:%M')
            
            # Update last updated timestamp
            self.psk_last_update_var.set(f"Last updated: {now.strftime('%H:%M:%S')}")
            
            # Determine priority based on prop mode and band
            prop_mode = spot_data.get('prop_mode', '')
            band = spot_data.get('band', '')
            
            # Use text indicators instead of emojis (render better in treeview)
            if prop_mode == 'multi_hop_e' and band in ['70cm', '1.25m', '33cm', '23cm']:
                pri_text = 'P1!'  # Critical - MSp-E on UHF+
                row_tag = 'p1'
            elif prop_mode == 'multi_hop_e' and band == '2m':
                pri_text = 'P2!'  # High - MSp-E on 2m
                row_tag = 'p2'
            elif prop_mode == 'sporadic_e' and band in ['70cm', '1.25m']:
                pri_text = 'P2'   # High - Sp-E on UHF
                row_tag = 'p2'
            elif prop_mode == 'sporadic_e':
                pri_text = 'P3'   # Medium - Sp-E on 2m/6m
                row_tag = 'p3'
            elif prop_mode == 'tropo':
                pri_text = 'P4'   # Low - Tropo
                row_tag = 'p4'
            else:
                pri_text = '--'   # Info - LOS or unknown
                row_tag = 'info'
            
            # Format prop mode for display
            prop_display = {
                'multi_hop_e': 'MSp-E',
                'sporadic_e': 'Sp-E',
                'tropo': 'Tropo',
                'line_of_sight': 'LOS',
            }.get(prop_mode, prop_mode)
            
            self.psk_alert_tree.insert('', 0, values=(
                time_str,
                pri_text,
                band,
                spot_data.get('nearby_call', ''),   # Station near you
                spot_data.get('far_call', ''),      # Station to try!
                spot_data.get('distance', ''),
                spot_data.get('bearing', ''),
                prop_display,
                spot_data.get('mode', 'FT8')
            ), tags=(row_tag,))
            
            # Update band activity display
            self._update_psk_band_activity()
            
            # Trim old entries
            children = self.psk_alert_tree.get_children()
            if len(children) > 50:
                for child in children[50:]:
                    self.psk_alert_tree.delete(child)
                    
        except Exception as e:
            print(f"Error adding PSK spot to tree: {e}")
    
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
        """Clear PSK alert display"""
        for item in self.psk_alert_tree.get_children():
            self.psk_alert_tree.delete(item)
    
    def _psk_open_pskreporter(self, event):
        """Open PSK Reporter map for selected spot"""
        import webbrowser
        
        selection = self.psk_alert_tree.selection()
        if not selection:
            return
        
        item = selection[0]
        values = self.psk_alert_tree.item(item, 'values')
        # columns: time, pri, band, nearby, far, dist, dir, prop, mode
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
            # Stamp GPS location data for LoTW before writing ADIF
            self._stamp_qso_location(qso_data)
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
            self.voice.announce("No logs directory found")
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
            self.voice.announce("No recent log files found")
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
            self.voice.announce(f"Reloaded {qso_count} QSOs from {files_loaded} days")
            
        except Exception as e:
            self.add_alert(f"Error reloading logs: {e}")
            self.voice.announce("Error reloading logs")
    
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
        """Delete selected QSO(s) from display"""
        selected = self.qso_tree.selection()
        if not selected:
            messagebox.showinfo("No Selection", "Please select a QSO to delete")
            return
        
        # Get info for confirmation
        count = len(selected)
        if count == 1:
            values = self.qso_tree.item(selected[0])['values']
            call = values[1] if len(values) > 1 else "?"
            msg = f"Delete QSO with {call} from display?\n\n(You'll need to manually remove from logger if needed)"
        else:
            msg = f"Delete {count} selected QSOs from display?\n\n(You'll need to manually remove from logger if needed)"
        
        result = messagebox.askyesno("Delete QSO", msg)
        if not result:
            return
        
        for item in selected:
            self.qso_tree.delete(item)
            self.qso_count -= 1
        
        self.qso_count_var.set(f"QSOs: {self.qso_count}")
        self.add_alert(f"Deleted {count} QSO(s) from display")
    
    def start_monitoring(self):
        """Start all monitoring threads"""
        try:
            # Start GPS monitoring with configured precision
            grid_precision = self.config.get('grid_precision', 4)
            self.gps_monitor = GPSMonitor(
                self.config['gps_port'], 
                self.on_gps_update, 
                grid_precision,
                lock_callback=self.on_gps_lock_change
            )
            self.gps_monitor.start()
            
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
                contest_logger=self.config.get('contest_logger', 'n1mm'),
                qso_callback=self.on_qso_logged,
                location_stamper=self._stamp_qso_location
            )
            
            # Start log monitoring
            self.log_monitor = LogMonitor(self.config['wsjt_instances'], self.on_new_decode)
            self.log_monitor.start()
            
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
        # Store current position for ADIF stamping
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
            
            # Update QSY Advisor with new grid (tracks per-grid for rovers!)
            if self.qsy_advisor:
                self.qsy_advisor.set_my_grid(grid)
            
            # Update logger button (main window)
            logger = self.config.get('contest_logger', 'n1mm')
            logger_name = "N1MM+" if logger == 'n1mm' else "N3FJP"
            self.logger_button.config(text=f"Send to {logger_name}: {grid}", state='normal')
            
            # Update radios
            self.radio_updater.update_grid(grid)
            
            # Voice announcement
            if old_grid != "----":
                self.voice.announce(f"Grid change. Entering {grid}")
                self.add_alert(f"GRID CHANGE: {old_grid} → {grid}")
                
                # Post to Slack
                my_call = self.config.get('my_call', '') or 'NOCALL'
                my_bands = self.config.get('my_bands', ['6m', '2m', '70cm'])
                bands_str = ', '.join(my_bands[:6])  # Limit to first 6 bands for readability
                if len(my_bands) > 6:
                    bands_str += f" +{len(my_bands)-6} more"
                self.post_to_slack(f"📍 {my_call}/R now in {grid} on {bands_str}")
            else:
                self.voice.announce(f"Current grid is {grid}")
                self.add_alert(f"GPS acquired. Current grid: {grid}")
        
        # Auto-detect county in QSO Party mode
        self._check_county_change(lat, lon)
    
    def _check_county_change(self, lat, lon):
        """Check if we've crossed into a new county. Updates for ALL modes (ADIF stamping)."""
        if not self.county_lookup or not self.county_lookup.is_loaded:
            return
        
        # Look up county from GPS coordinates
        county_info = self.county_lookup.lookup(lat, lon)
        
        if not county_info:
            return
        
        # Store for ADIF stamping (works in ALL modes)
        self.current_county_info = county_info
        
        # Check if county name changed (for ADIF stamping display)
        if county_info.name != getattr(self, '_last_county_name', ''):
            old_county = getattr(self, '_last_county_name', '')
            self._last_county_name = county_info.name
            
            # Update status bar county display if it exists
            if hasattr(self, 'county_label'):
                self.county_label.config(text=f"{county_info.name}, {county_info.state_abbrev}")
            
            # Log county changes (only when actually changed)
            if old_county:
                print(f"County: {old_county} → {county_info.name}, {county_info.state_abbrev}")
        
        # === QSO Party Mode: Additional handling ===
        if self.config.get('contest_mode') != 'qso_party':
            return
        if not self.config.get('county_auto_detect', True):
            return
        
        # Get the QSO Party code and check if this county is in it
        party_code = self.config.get('qso_party_code', '').upper()
        if not party_code or party_code not in self.qso_parties:
            return
        
        party_data = self.qso_parties[party_code]
        
        # Try to map FIPS code to QSO Party abbreviation
        county_abbrev = self._fips_to_qsoparty_abbrev(county_info.fips, county_info.name, party_data)
        
        if not county_abbrev:
            return
        
        # Check if county changed
        if county_abbrev != self.current_county:
            old_county = self.current_county
            self.current_county = county_abbrev
            self.config['qso_party_county'] = county_abbrev
            
            # Update displays
            if hasattr(self, 'county_display_var'):
                self.county_display_var.set(county_abbrev)
            if hasattr(self, 'county_label'):
                self.county_label.config(text=county_abbrev)
            if hasattr(self, 'qso_party_county_var'):
                self.qso_party_county_var.set(county_abbrev)
            
            # Update logger button (in QSO Party mode shows county)
            logger = self.config.get('contest_logger', 'n1mm')
            logger_name = "N1MM+" if logger == 'n1mm' else "N3FJP"
            self.logger_button.config(text=f"Send to {logger_name}: {county_abbrev}", state='normal')
            
            # Send to N1MM+ via RoverQTH
            if hasattr(self, 'radio_updater') and self.radio_updater:
                self.radio_updater.send_n1mm_roverqth_county(county_abbrev)
            
            # Voice announcement
            if old_county:
                self.voice.announce(f"County change. Now in {county_info.contest_name}")
                self.add_alert(f"COUNTY CHANGE: {old_county} → {county_abbrev} ({county_info.contest_name})")
            else:
                self.voice.announce(f"Current county is {county_info.contest_name}")
                self.add_alert(f"County detected: {county_abbrev} ({county_info.contest_name})")
            
            # Update Manual Entry "My County" field
            if hasattr(self, 'manual_mygrid_var'):
                self.manual_mygrid_var.set(county_abbrev)
    
    def _fips_to_qsoparty_abbrev(self, fips, county_name, party_data):
        """Convert FIPS code or county name to QSO Party county abbreviation"""
        # Get state abbreviation - try party_data first, then derive from party code
        state_abbrev = party_data.get('state', '')
        if not state_abbrev:
            state_abbrev = self.config.get('qso_party_code', '').upper()
        
        # Try to load a FIPS mapping file (e.g., OK.json)
        fips_map_path = Path(f"data/county_mappings/{state_abbrev}.json")
        
        if fips_map_path.exists():
            try:
                import json
                with open(fips_map_path) as f:
                    fips_map = json.load(f)
                
                if fips in fips_map:
                    return fips_map[fips].get('code')
                    
                # Fallback: try name matching from JSON
                for fips_code, entry in fips_map.items():
                    if entry.get('name', '').upper() == county_name.upper():
                        return entry.get('code')
            except Exception as e:
                print(f"County mapping error: {e}")
        
        return None
    
    def _stamp_qso_location(self, qso_data):
        """
        Stamp GPS-derived location data onto a QSO for ADIF export.
        
        Adds MY_STATE, MY_CNTY, MY_LAT, MY_LON, MY_CQ_ZONE, MY_ITU_ZONE, MY_DXCC
        for proper LoTW upload via Log4OM.
        """
        # Use current GPS position
        lat = self.current_lat
        lon = self.current_lon
        
        # Add lat/lon in ADIF format
        if lat is not None and lon is not None:
            from modules.radio_updater import RadioUpdater
            qso_data['my_lat'] = RadioUpdater.to_adif_latitude(lat)
            qso_data['my_lon'] = RadioUpdater.to_adif_longitude(lon)
            
            # Use cached county info (updated on every GPS reading)
            county_info = getattr(self, 'current_county_info', None)
            if county_info:
                qso_data['my_state'] = county_info.state_abbrev
                qso_data['my_county'] = county_info.contest_name
            else:
                # Fallback: try fresh lookup
                if self.county_lookup and self.county_lookup.is_loaded:
                    county_info = self.county_lookup.lookup(lat, lon)
                    if county_info:
                        qso_data['my_state'] = county_info.state_abbrev
                        qso_data['my_county'] = county_info.contest_name
        
        # Default US station values (can be overridden in config later)
        qso_data.setdefault('my_country', 'United States')
        qso_data.setdefault('my_cq_zone', '4')   # Central US
        qso_data.setdefault('my_itu_zone', '7')  # Central US  
        qso_data.setdefault('my_dxcc', '291')    # USA
        
        return qso_data
    
    def on_gps_lock_change(self, has_lock, message):
        """Called when GPS lock status changes"""
        if has_lock:
            self.add_alert(f"GPS: Lock acquired ✓")
        else:
            self.add_alert(f"GPS: Lock lost ✗", priority=True)
            self.voice.announce("Warning: GPS lock lost")
    
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
                self.voice.announce("Warning: Battery voltage critical")
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
                            self.voice.announce(f"Warning: {short} is not responding")
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
    
    def _start_aprs(self):
        """Start APRS-IS client"""
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
            
        except Exception as e:
            print(f"APRS: Failed to start: {e}")
            self.add_alert(f"APRS error: {e}")
    
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
        
        self.save_config()
        
        # Update GPS monitor precision if running
        if hasattr(self, 'gps_monitor') and self.gps_monitor:
            self.gps_monitor.set_precision(self.config['grid_precision'])
        
        # If switching TO QSO Party mode, trigger county lookup BEFORE updating UI
        if mode_key == 'qso_party' and self.current_lat is not None and self.current_lon is not None:
            self._check_county_change(self.current_lat, self.current_lon)
        
        # Now update UI (button text will reflect county if in QSO Party mode)
        self._update_contest_mode_ui()
        
        self.add_alert(f"Contest mode: {CONTEST_MODES[mode_key]}")
        print(f"Contest Mode: Changed to {mode_key}, grid precision = {self.config['grid_precision']}")
    
    def _update_contest_mode_ui(self):
        """Update UI elements based on current contest mode"""
        mode = self.config.get('contest_mode', 'vhf')
        logger = self.config.get('contest_logger', 'n1mm')
        logger_name = "N1MM+" if logger == 'n1mm' else "N3FJP"
        
        # Update grid precision display
        precision = self.config.get('grid_precision', 4)
        self.grid_precision_label.config(text=f"{precision}-char")
        
        # Show/hide QSO party settings and county display
        # Also hide QSO party if using N3FJP (different apps per party)
        if mode == 'qso_party' and logger == 'n1mm':
            self.qso_party_frame.grid()
            self.county_frame.pack(side=tk.LEFT, padx=10)  # Show county in top bar
            # Button shows county in QSO Party mode
            county = self.current_county or self.config.get('qso_party_county', '')
            if county:
                self.logger_button.config(text=f"Send to {logger_name}: {county}")
        else:
            self.qso_party_frame.grid_remove()
            self.county_frame.pack_forget()  # Hide county in top bar
            # Button shows grid in VHF/222 modes
            grid = self.current_grid if self.current_grid != "----" else "----"
            self.logger_button.config(text=f"Send to {logger_name}: {grid}")
        
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
        
        # Update buttons and labels
        logger_name = "N1MM+" if logger == 'n1mm' else "N3FJP"
        grid = self.current_grid if self.current_grid != "----" else "----"
        self.logger_button.config(text=f"Send to {logger_name}: {grid}")
        
        # Update Test Mode button
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
        else:
            self.n1mm_settings_frame.grid_remove()
            self.n3fjp_settings_frame.grid()
    
    def _update_manual_entry_labels(self):
        """Update Manual Entry tab labels based on contest mode and logger"""
        mode = self.config.get('contest_mode', 'vhf')
        logger = self.config.get('contest_logger', 'n1mm')
        logger_name = "N1MM+" if logger == 'n1mm' else "N3FJP"
        
        if mode == 'qso_party' and logger == 'n1mm':
            # QSO Party mode with N1MM+ - use county labels
            self.their_grid_label.config(text="Their Exchange:")
            self.my_grid_label.config(text="My County:")
            self.my_grid_hint_label.config(text="(set via Settings → QSO Party)")
            self.log_qso_button.config(text=f"Log QSO to {logger_name} & ADIF")
            # Auto-fill county if set
            if self.current_county:
                self.manual_mygrid_var.set(self.current_county)
        else:
            # VHF/222 Up mode or N3FJP - use grid labels
            self.their_grid_label.config(text="Their Grid:")
            self.my_grid_label.config(text="My Grid:")
            self.my_grid_hint_label.config(text="(auto-filled from GPS)")
            self.log_qso_button.config(text=f"Log QSO to {logger_name} & ADIF")
            # Auto-fill grid from GPS
            if self.current_grid != "----":
                self.manual_mygrid_var.set(self.current_grid)
    
    def _update_manual_entry_bands(self):
        """Update Manual Entry band dropdown based on My Bands settings"""
        my_bands = self.config.get('my_bands', ['6m', '2m', '1.25m', '70cm', '33cm', '23cm'])
        if hasattr(self, 'manual_band_combo'):
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
    
    def _on_qsoparty_change(self, event=None):
        """Handle QSO party selection change"""
        party_code = self.qso_party_code_var.get()
        self.config['qso_party_code'] = party_code
        self.save_config()
        
        # Update county dropdown
        self._update_county_list()
        self.county_display_var.set("----")
        
        # Clear county name cache to rebuild for new party
        if hasattr(self, '_county_name_cache'):
            self._county_name_cache.clear()
        
        print(f"QSO Party: Changed to {party_code}")
    
    def _on_county_auto_detect_change(self):
        """Handle county auto-detect checkbox change"""
        enabled = self.county_auto_detect_var.get()
        self.config['county_auto_detect'] = enabled
        self.save_config()
        
        if enabled:
            self.add_alert("County auto-detection enabled")
            if self.county_lookup and self.county_lookup.is_loaded:
                self.voice.announce("County auto detection enabled")
            else:
                self.add_alert("Warning: County shapefile not loaded - auto-detect won't work")
        else:
            self.add_alert("County auto-detection disabled")
            self.voice.announce("County auto detection disabled")
    
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
        self.voice.announce(f"County set to {canonical}")
        self.add_alert(f"QSO Party: {party_code} - {canonical}")
        
        # Update Manual Entry "My County" field
        self.manual_mygrid_var.set(canonical)
        
        print(f"QSO Party: Set county to {canonical} for {party_code}")
    
    def on_aprs_nearby_station(self, callsign, lat, lon, distance_mi, bearing, symbol_desc):
        """Called when a mobile APRS station is detected nearby"""
        msg = f"APRS: {callsign} ({symbol_desc}) {distance_mi:.1f} mi {bearing}"
        self.add_alert(msg, priority=True)
        self.voice.announce(f"APRS station {callsign}, {distance_mi:.0f} miles {bearing}")
    
    def on_aprs_message(self, from_call, message, msgno):
        """Called when an APRS message is received"""
        msg = f"APRS MSG from {from_call}: {message}"
        self.add_alert(msg, priority=True)
        self.voice.announce(f"APRS message from {from_call}")
        
        # Show popup for messages
        self.root.after(0, lambda: messagebox.showinfo(
            f"APRS Message from {from_call}", 
            message
        ))
    
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
        self.voice.announce(message)
    
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
            self.voice.announce(f"New grid {grid} on {band}")
        
        if is_calling_me:
            msg = f"{callsign} calling you on {band}"
            self.add_alert(msg, priority=True, callsign=callsign)
            self.voice.announce(f"{callsign} calling on {band}")
    
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
        """Send current grid/county to the configured contest logger (N1MM+ or N3FJP)"""
        mode = self.config.get('contest_mode', 'vhf')
        logger = self.config.get('contest_logger', 'n1mm')
        logger_name = "N1MM+" if logger == 'n1mm' else "N3FJP"
        
        # In QSO Party mode with N1MM+, send county instead of grid
        if mode == 'qso_party' and logger == 'n1mm':
            if not self.current_county:
                messagebox.showwarning("No County", "Please set a county first in Settings → QSO Party")
                return
            
            if hasattr(self, 'radio_updater') and self.radio_updater:
                self.radio_updater.send_n1mm_roverqth_county(self.current_county)
                self.add_alert(f"Sent to {logger_name}: {self.current_county}")
                self.voice.announce(f"{logger_name} updated to {self.current_county}")
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
                self.voice.announce(f"{logger_name} updated to {self.current_grid}")
            else:
                messagebox.showerror("Error", "Radio updater not initialized")
    
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
        self.config['victron_address'] = self.victron_addr_var.get()
        self.config['victron_key'] = self.victron_key_var.get()
        
        # Logger settings
        self.config['contest_logger'] = self.logger_var.get()
        self.config['n1mm_udp_port'] = int(self.n1mm_port_var.get())
        self.config['n3fjp_port'] = int(self.n3fjp_port_var.get())
        
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
        
        # Validation
        if not band:
            messagebox.showwarning("Incomplete", "Please select a band")
            return
        if not call:
            messagebox.showwarning("Incomplete", "Please enter a callsign")
            return
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
        
        # Write to ADIF backup
        if self.radio_updater:
            # Stamp GPS location data for LoTW before writing ADIF
            self._stamp_qso_location(qso_data)
            self.radio_updater._write_qso_to_adif(qso_data)
        
        # Update QSO display
        self.on_qso_logged(qso_data)
        
        # Voice announcement
        self.voice.announce(f"QSO logged. {call}")
        
        # Alert
        self.add_alert(f"Manual QSO: {call} on {band} {mode} - {grid}")
        
        # Clear call and grid for next QSO, keep rest
        self.manual_call_var.set('')
        self.manual_grid_var.set('')
        
        # Update my_grid in case GPS changed
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
        logger_name = "N1MM+" if logger == 'n1mm' else "N3FJP"
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
            
            # Update status label
            self.aprs_status_var.set(f"APRS: {status} | RX:{stats['packets_received']} TX:{stats['beacons_sent']}")
        else:
            messagebox.showinfo("APRS Statistics", "APRS not enabled.\n\nEnable in Settings tab.")
            self.aprs_status_var.set("APRS: Disabled")

def main():
    root = tk.Tk()
    app = CoPilotApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
