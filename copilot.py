#!/usr/bin/env python3
"""
N5ZY VHF Contest Co-Pilot
Main application
"""

import tkinter as tk
from tkinter import ttk, messagebox
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

class CoPilotApp:
    def __init__(self, root):
        self.root = root
        self.root.title("N5ZY VHF Contest Co-Pilot")
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
        self.voice = VoiceAlerter()
        self.qsy_advisor = QSYAdvisor()
        self.qsy_advisor.set_qsy_callback(self.on_qsy_opportunity)
        
        # Current state
        self.current_grid = "----"
        self.battery_voltage = 0.0
        self.battery_current = 0.0
        
        self.create_gui()
        self.start_monitoring()
    
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
                'grid_precision': 4,  # 4-char for VHF contests, 6-char for testing/distance
                'wsjt_instances': [
                    {'name': '6m', 'log_path': '', 'udp_port': 2237},
                    {'name': '2m', 'log_path': '', 'udp_port': 2238},
                    {'name': '222/902', 'log_path': '', 'udp_port': 2239}
                ],
                'n1mm_udp_host': '127.0.0.1',
                'n1mm_udp_port': 52001,  # N1MM+ JTDX TCP port (Config → Configure Ports → WSJT/JTDX Setup)
                'active_bands': ['50', '144', '222', '432', '902', '1296', '10368'],
                # APRS-IS settings
                'aprs_enabled': False,
                'aprs_callsign': 'N5ZY',  # Your callsign (add -9 for mobile SSID if desired)
                'aprs_beacon_interval': 600,  # 10 minutes
                'aprs_alert_radius': 10,  # miles
                'aprs_comment': 'N5ZY.ORG Rover!',  # Beacon comment
            }
            self.save_config()
    
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
        
        # Battery voltage (always visible)
        ttk.Label(status_frame, text="Battery:", font=('Arial', 12)).pack(side=tk.LEFT, padx=20)
        self.voltage_label = ttk.Label(status_frame, text="--.-V", 
                                        font=('Arial', 18, 'bold'), foreground='green')
        self.voltage_label.pack(side=tk.LEFT, padx=5)
        
        # N1MM+ ROVERQTH button (always visible at top)
        self.n1mm_button = ttk.Button(status_frame, text="Send to N1MM+: ----", 
                                       command=self.send_roverqth_to_n1mm, state='disabled')
        self.n1mm_button.pack(side=tk.RIGHT, padx=10)
        
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
        
        # Tab 2: Settings
        self.settings_tab = self.create_settings_tab(notebook)
        notebook.add(self.settings_tab, text="Settings")
        
        # Tab 3: Manual Entry
        self.manual_tab = self.create_manual_entry_tab(notebook)
        notebook.add(self.manual_tab, text="Manual Entry")
        
        # Tab 4: Test Mode
        self.test_tab = self.create_test_tab(notebook)
        notebook.add(self.test_tab, text="Test Mode")
        
        # Tab 5: QSO Log (from all WSJT-X instances)
        self.qso_log_tab = self.create_qso_log_tab(notebook)
        notebook.add(self.qso_log_tab, text="QSO Log")
        
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
        
        return frame
    
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
        
        # GPS Settings
        gps_frame = ttk.LabelFrame(frame, text="GPS Settings", padding=10)
        gps_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(gps_frame, text="GPS COM Port:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.gps_port_var = tk.StringVar(value=self.config['gps_port'])
        ttk.Entry(gps_frame, textvariable=self.gps_port_var, width=15).grid(row=0, column=1, pady=2)
        
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
        
        # N1MM+ Settings
        n1mm_frame = ttk.LabelFrame(frame, text="N1MM+ QSO Relay (TCP)", padding=10)
        n1mm_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(n1mm_frame, text="TCP Port:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.n1mm_port_var = tk.StringVar(value=str(self.config.get('n1mm_udp_port', 52001)))
        ttk.Entry(n1mm_frame, textvariable=self.n1mm_port_var, width=15).grid(row=0, column=1, pady=2)
        ttk.Label(n1mm_frame, text="(N1MM+: Config → Configure Ports → WSJT/JTDX Setup)", 
                 foreground="gray").grid(row=0, column=2, sticky=tk.W, pady=2, padx=5)
        
        ttk.Label(n1mm_frame, text="Sends ADIF via TCP. Each QSO = separate connection with 500ms delay.",
                 foreground="gray").grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=2)
        
        # WSJT-X Log Paths
        wsjt_frame = ttk.LabelFrame(frame, text="WSJT-X Log Locations", padding=10)
        wsjt_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.wsjt_path_vars = []
        for i, instance in enumerate(self.config['wsjt_instances']):
            ttk.Label(wsjt_frame, text=f"{instance['name']} Log:").grid(row=i, column=0, sticky=tk.W, pady=2)
            var = tk.StringVar(value=instance['log_path'])
            self.wsjt_path_vars.append(var)
            ttk.Entry(wsjt_frame, textvariable=var, width=50).grid(row=i, column=1, pady=2, padx=5)
            ttk.Button(wsjt_frame, text="Browse...", 
                       command=lambda idx=i: self.browse_log_path(idx)).grid(row=i, column=2, pady=2)
        
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
        
        # Save button
        ttk.Button(frame, text="Save Settings", command=self.save_settings).pack(pady=10)
        
        return outer_frame
    
    def create_manual_entry_tab(self, parent):
        """Create manual QSO entry tab for phone/CW contacts"""
        frame = ttk.Frame(parent)
        
        entry_frame = ttk.LabelFrame(frame, text="Manual QSO Entry (Phone/CW)", padding=10)
        entry_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Row 0: Band selection
        ttk.Label(entry_frame, text="Band:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.manual_band_var = tk.StringVar()
        band_values = ['6m', '2m', '1.25m', '70cm', '33cm', '23cm', '13cm', '9cm', '6cm', '3cm']
        band_combo = ttk.Combobox(entry_frame, textvariable=self.manual_band_var, 
                                   values=band_values, width=10, state='readonly')
        band_combo.grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        band_combo.bind('<<ComboboxSelected>>', self._on_band_select)
        
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
        
        # Row 4: Grid
        ttk.Label(entry_frame, text="Their Grid:").grid(row=4, column=0, sticky=tk.W, pady=5)
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
        
        # Row 6: My Grid (read-only, from GPS)
        ttk.Label(entry_frame, text="My Grid:").grid(row=6, column=0, sticky=tk.W, pady=5)
        self.manual_mygrid_var = tk.StringVar(value=self.current_grid)
        mygrid_entry = ttk.Entry(entry_frame, textvariable=self.manual_mygrid_var, width=10)
        mygrid_entry.grid(row=6, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Label(entry_frame, text="(auto-filled from GPS)", foreground='gray').grid(row=6, column=2, sticky=tk.W)
        
        # Row 7: Log button
        button_frame = ttk.Frame(entry_frame)
        button_frame.grid(row=7, column=0, columnspan=3, pady=15)
        
        ttk.Button(button_frame, text="Log QSO to N1MM+ & ADIF", 
                   command=self.log_manual_qso).pack(side=tk.LEFT, padx=5)
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
        manual_frame = ttk.LabelFrame(frame, text="Manual Grid Test - Two Step Process", padding=10)
        manual_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Grid entry
        ttk.Label(manual_frame, text="Test Grid:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=5)
        self.test_grid_var = tk.StringVar(value="EM15")
        ttk.Entry(manual_frame, textvariable=self.test_grid_var, width=15).grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        
        # Step 1 button
        ttk.Button(manual_frame, text="Step 1: Send to WSJT-X", 
                   command=self.send_test_grid).grid(row=1, column=0, columnspan=2, pady=5, padx=5, sticky=tk.EW)
        
        # Step 2 button (references the button variable from main window)
        self.test_n1mm_button = ttk.Button(manual_frame, text="Step 2: Send to N1MM+: ----", 
                   command=self.send_roverqth_to_n1mm, state='disabled')
        self.test_n1mm_button.grid(row=2, column=0, columnspan=2, pady=5, padx=5, sticky=tk.EW)
        
        ttk.Label(manual_frame, text="Enter grid → Step 1 updates WSJT-X TX6 → Step 2 updates N1MM+ ROVERQTH",
                 foreground="gray").grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=5, padx=5)
        
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
        
        # Treeview for QSO log
        columns = ('time', 'call', 'grid', 'band', 'mode', 'rst_s', 'rst_r', 'source')
        self.qso_tree = ttk.Treeview(frame, columns=columns, show='headings', height=15)
        
        # Column headers
        self.qso_tree.heading('time', text='Time (UTC)')
        self.qso_tree.heading('call', text='Callsign')
        self.qso_tree.heading('grid', text='Grid')
        self.qso_tree.heading('band', text='Band')
        self.qso_tree.heading('mode', text='Mode')
        self.qso_tree.heading('rst_s', text='RST Sent')
        self.qso_tree.heading('rst_r', text='RST Rcvd')
        self.qso_tree.heading('source', text='Source')
        
        # Column widths
        self.qso_tree.column('time', width=80)
        self.qso_tree.column('call', width=100)
        self.qso_tree.column('grid', width=70)
        self.qso_tree.column('band', width=60)
        self.qso_tree.column('mode', width=60)
        self.qso_tree.column('rst_s', width=60)
        self.qso_tree.column('rst_r', width=60)
        self.qso_tree.column('source', width=120)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.qso_tree.yview)
        self.qso_tree.configure(yscrollcommand=scrollbar.set)
        
        self.qso_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
        
        # Control buttons
        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(button_frame, text="Reload Contest Log", 
                   command=self.reload_contest_log).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(button_frame, text="Open ADIF Log Folder", 
                   command=self.open_adif_folder).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(button_frame, text="Clear Display", 
                   command=self.clear_qso_display).pack(side=tk.LEFT, padx=5)
        
        # Status label
        self.qso_status_var = tk.StringVar(value="ADIF log: logs/n5zy_copilot_YYYYMMDD.adi")
        ttk.Label(button_frame, textvariable=self.qso_status_var, 
                 foreground="gray").pack(side=tk.RIGHT, padx=5)
        
        # Track QSO count
        self.qso_count = 0
        
        return frame
    
    def on_qso_logged(self, qso_data):
        """Called when a QSO is logged from any WSJT-X instance"""
        try:
            # Update count
            self.qso_count += 1
            self.qso_count_var.set(f"QSOs: {self.qso_count}")
            
            # Add to treeview (at the top)
            time_str = qso_data['datetime_off'].strftime('%H:%M:%S') if qso_data['datetime_off'] else ''
            
            self.qso_tree.insert('', 0, values=(
                time_str,
                qso_data['dx_call'],
                qso_data['dx_grid'],
                qso_data['band'],
                qso_data['mode'],
                qso_data['report_sent'],
                qso_data['report_rcvd'],
                qso_data['wsjtx_id']
            ))
            
            # Add alert
            self.add_alert(f"QSO: {qso_data['dx_call']} on {qso_data['band']} via {qso_data['wsjtx_id']}")
            
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
                        
                        # Add to display
                        self.qso_tree.insert('', 'end', values=(
                            time_display,
                            callsign,
                            their_grid,
                            band,
                            mode,
                            my_grid[:4] if my_grid else ""
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
        for item in self.qso_tree.get_children():
            self.qso_tree.delete(item)
        self.qso_count = 0
        self.qso_count_var.set("QSOs: 0")
    
    def start_monitoring(self):
        """Start all monitoring threads"""
        try:
            # Start GPS monitoring with configured precision
            grid_precision = self.config.get('grid_precision', 4)
            self.gps_monitor = GPSMonitor(self.config['gps_port'], self.on_gps_update, grid_precision)
            self.gps_monitor.start()
            
            # Start battery monitoring
            if self.config['victron_address'] and self.config['victron_key']:
                self.battery_monitor = BatteryMonitor(
                    self.config['victron_address'],
                    self.config['victron_key'],
                    self.on_battery_update
                )
                self.battery_monitor.start()
            
            # Initialize radio updater with QSO callback
            self.radio_updater = RadioUpdater(
                self.config['wsjt_instances'],
                self.config['n1mm_udp_host'],
                self.config['n1mm_udp_port'],
                qso_callback=self.on_qso_logged
            )
            
            # Start log monitoring
            self.log_monitor = LogMonitor(self.config['wsjt_instances'], self.on_new_decode)
            self.log_monitor.start()
            
            # Start APRS if enabled
            if self.config.get('aprs_enabled', False):
                self._start_aprs()
            
            self.add_alert("System started successfully")
            self.update_status("Running")
            
        except Exception as e:
            messagebox.showerror("Startup Error", f"Failed to start monitoring: {e}")
            self.update_status(f"Error: {e}")
    
    def on_gps_update(self, grid, lat, lon):
        """Called when GPS position updates"""
        # Always update APRS position (even if grid hasn't changed)
        if hasattr(self, 'aprs_client') and self.aprs_client:
            self.aprs_client.set_position(lat, lon, grid)
        
        if grid != self.current_grid:
            old_grid = self.current_grid
            self.current_grid = grid
            self.grid_label.config(text=grid)
            
            # Update QSY Advisor with new grid (tracks per-grid for rovers!)
            if self.qsy_advisor:
                self.qsy_advisor.set_my_grid(grid)
            
            # Update N1MM+ buttons (main window and test tab)
            self.n1mm_button.config(text=f"Send to N1MM+: {grid}", state='normal')
            self.test_n1mm_button.config(text=f"Step 2: Send to N1MM+: {grid}", state='normal')
            
            # Update radios
            self.radio_updater.update_grid(grid)
            
            # Voice announcement
            if old_grid != "----":
                self.voice.announce(f"Grid change. Entering {grid}")
                self.add_alert(f"GRID CHANGE: {old_grid} → {grid}")
            else:
                self.voice.announce(f"Current grid is {grid}")
                self.add_alert(f"GPS acquired. Current grid: {grid}")
    
    def on_battery_update(self, voltage, current, soc, remaining_mins):
        """Called when battery data updates"""
        self.battery_voltage = voltage
        self.battery_current = current
        
        self.voltage_label.config(text=f"{voltage:.1f}V")
        
        # Color code based on voltage
        if voltage < 12.0:
            self.voltage_label.config(foreground='red')
            if voltage < 11.5:
                self.voice.announce("Warning: Battery voltage critical")
        elif voltage < 12.5:
            self.voltage_label.config(foreground='orange')
        else:
            self.voltage_label.config(foreground='green')
    
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
        
        voice_msg = f"QSY opportunity. {callsign} also has {bands_str}"
        
        # Add to alerts
        self.add_alert(f"QSY: {callsign} -> {', '.join([self.qsy_advisor.BAND_NAMES.get(b, b) for b in available_bands])}", priority=True)
        
        # Voice announcement (delayed slightly so it comes after QSO logged)
        self.root.after(1500, lambda: self.voice.announce(voice_msg))
    
    def on_new_decode(self, band, callsign, grid, is_new_grid, is_calling_me):
        """Called when new decode is found in WSJT-X logs"""
        if is_new_grid:
            msg = f"New grid {grid} on {band}MHz from {callsign}"
            self.add_alert(msg, priority=True)
            self.voice.announce(f"New grid {grid} on {band} meters")
        
        if is_calling_me:
            msg = f"{callsign} calling you on {band}MHz"
            self.add_alert(msg, priority=True)
            self.voice.announce(f"{callsign} calling on {band} meters")
    
    def add_alert(self, message, priority=False):
        """Add alert to the alerts display"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        if priority:
            formatted = f"[{timestamp}] *** {message} ***\n"
        else:
            formatted = f"[{timestamp}] {message}\n"
        
        self.alerts_text.insert(tk.END, formatted)
        self.alerts_text.see(tk.END)  # Auto-scroll
    
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
    
    def send_roverqth_to_n1mm(self):
        """Send ROVERQTH command to N1MM+ via keyboard automation"""
        if self.current_grid == "----":
            messagebox.showwarning("No GPS", "No GPS position available yet")
            return
        
        import pyautogui
        import time
        
        grid = self.current_grid
        
        # Give user 3 seconds to click N1MM+ Entry window callsign box
        result = messagebox.askokcancel(
            "N1MM+ ROVERQTH Update",
            f"Will update N1MM+ to: {grid}\n\n"
            "Step 1: Type ROVERQTH command\n"
            "Step 2: Fill in dialog with grid\n\n"
            "Click OK, then quickly click in the N1MM+ callsign box.\n"
            "You have 3 seconds after clicking OK."
        )
        
        if not result:
            return
        
        # Countdown
        time.sleep(3)
        
        # Step 1: Type ROVERQTH and press Enter to open dialog
        pyautogui.typewrite('ROVERQTH', interval=0.05)
        pyautogui.press('enter')
        
        # Wait for dialog to appear
        time.sleep(0.5)
        
        # Step 2: Type the grid square in the dialog
        pyautogui.typewrite(grid, interval=0.05)
        pyautogui.press('enter')
        
        # Wait for confirmation dialog
        time.sleep(0.5)
        
        # Step 3: Press Enter to confirm 'Yes' (default button)
        pyautogui.press('enter')
        
        self.add_alert(f"Sent to N1MM+: ROVERQTH → {grid}")
        self.voice.announce(f"N1MM updated to {grid}")
    
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
    
    def save_settings(self):
        """Save settings from GUI to config"""
        self.config['gps_port'] = self.gps_port_var.get()
        self.config['victron_address'] = self.victron_addr_var.get()
        self.config['victron_key'] = self.victron_key_var.get()
        self.config['n1mm_udp_port'] = int(self.n1mm_port_var.get())
        
        # APRS settings
        self.config['aprs_enabled'] = self.aprs_enabled_var.get()
        self.config['aprs_callsign'] = self.aprs_call_var.get()
        self.config['aprs_beacon_interval'] = int(self.aprs_beacon_var.get())
        self.config['aprs_alert_radius'] = int(self.aprs_radius_var.get())
        self.config['aprs_comment'] = self.aprs_comment_var.get()
        
        for i, var in enumerate(self.wsjt_path_vars):
            self.config['wsjt_instances'][i]['log_path'] = var.get()
        
        self.save_config()
        messagebox.showinfo("Settings", "Settings saved successfully")
        self.add_alert("Settings saved")
        
        # Restart radio updater with new settings
        self.radio_updater = RadioUpdater(
            self.config['wsjt_instances'],
            self.config['n1mm_udp_host'],
            self.config['n1mm_udp_port']
        )
        self.add_alert("Radio updater restarted with new settings")
        
        # Start/stop APRS based on settings
        if self.config['aprs_enabled']:
            self._start_aprs()
        elif hasattr(self, 'aprs_client') and self.aprs_client:
            self.aprs_client.stop()
            self.aprs_client = None
            self.add_alert("APRS stopped")
    
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
            'my_call': 'N5ZY',
            'my_grid': my_grid or self.current_grid,
        }
        
        # Send to N1MM+ via the radio updater's relay queue
        if self.radio_updater:
            self.radio_updater.queue_qso_for_relay(qso_data)
        
        # Write to ADIF backup
        if self.radio_updater:
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
        """Send manually entered test grid to WSJT-X"""
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
        
        # Update N1MM+ buttons (main window and test tab)
        self.n1mm_button.config(text=f"Send to N1MM+: {test_grid}", state='normal')
        self.test_n1mm_button.config(text=f"Step 2: Send to N1MM+: {test_grid}", state='normal')
        
        # Send to WSJT-X
        print(f"Test Mode: Sending test grid '{test_grid}' to WSJT-X")
        self.radio_updater.update_grid(test_grid)
        self.voice.announce(f"Test grid {test_grid}")
        self.add_alert(f"TEST: Sent grid {test_grid} to WSJT-X - Check TX6 message!")
    
    
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
