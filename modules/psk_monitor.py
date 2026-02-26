"""
PSK Reporter Monitor Module

Monitors PSK Reporter for band activity, propagation events, and openings.
Designed for VHF/UHF rover operations to detect:
- Multi-hop Sporadic-E (MSp-E) - CRITICAL for 2m/70cm/1.25m
- Sporadic-E openings
- Tropo ducting
- Band openings (increased activity)
- Unusual mode activity (Q65, MSK144, FT4)

API: https://pskreporter.info/cgi-bin/pskquery5.pl
Rate limit: No more than once per 5 minutes!
"""

import threading
import time
import math
import os
import urllib.request
import urllib.parse
import json
from datetime import datetime, timedelta
from collections import defaultdict


class PSKMonitor:
    """Monitor PSK Reporter for band activity and propagation events"""
    
    # Band definitions (frequency ranges in MHz)
    BANDS = {
        '6m': (50, 54),
        '2m': (144, 148),
        '1.25m': (222, 225),
        '70cm': (420, 450),
        '33cm': (902, 928),
        '23cm': (1240, 1300),
        '13cm': (2300, 2450),
    }
    
    # HF bands for QSO Party mode
    HF_BANDS = {
        '160m': (1.8, 2.0),
        '80m': (3.5, 4.0),
        '60m': (5.3, 5.4),
        '40m': (7.0, 7.3),
        '30m': (10.1, 10.15),
        '20m': (14.0, 14.35),
        '17m': (18.068, 18.168),
        '15m': (21.0, 21.45),
        '12m': (24.89, 24.99),
        '10m': (28.0, 29.7),
    }
    
    # Priority levels for alerts (VHF contest)
    PRIORITY_CRITICAL = 1   # MSp-E on 70cm/1.25m/23cm+, any 23cm+ activity
    PRIORITY_HIGH = 2       # MSp-E on 2m, Sp-E on 70cm
    PRIORITY_MEDIUM = 3     # Sp-E on 2m, Tropo
    PRIORITY_LOW = 4        # Sp-E on 6m, band openings
    PRIORITY_INFO = 5       # General activity
    
    # Propagation distance ranges (miles)
    PROP_RANGES = {
        'line_of_sight': (0, 100),
        'tropo': (100, 500),
        'sporadic_e': (500, 1400),
        'multi_hop_e': (1400, 3000),
        'tep': (2000, 5000),  # Trans-Equatorial Propagation
    }
    
    # TEP latitude bounds (stations must be relatively close to equator)
    TEP_LAT_RANGE = (-35, 35)
    
    def __init__(self, my_grid, config, alert_callback=None, voice=None):
        """
        Initialize PSK Monitor
        
        Args:
            my_grid: Current Maidenhead grid square (4 or 6 char)
            config: Configuration dict with settings
            alert_callback: Function to call with alerts (message, priority)
            voice: VoiceAlerts instance for announcements
        """
        self.my_grid = my_grid
        self.my_lat, self.my_lon = self._grid_to_latlon(my_grid) if my_grid else (None, None)
        self.config = config
        self.alert_callback = alert_callback
        self.spot_callback = None  # Set externally for displaying all spots in UI
        self.poll_complete_callback = None  # Set externally to notify when poll finishes
        self.voice = voice
        
        # Settings with defaults
        self.vhf_radius = config.get('psk_vhf_radius', 250)
        self.hf_radius = config.get('psk_hf_radius', 100)
        self.baseline_minutes = config.get('psk_baseline_minutes', 15)
        self.poll_interval = 300  # 5 minutes - PSK Reporter limit!
        
        # Alert toggles
        self.alert_band_openings = config.get('psk_alert_openings', True)
        self.alert_mspe = config.get('psk_alert_mspe', True)
        self.alert_spe = config.get('psk_alert_spe', True)
        self.alert_unusual_modes = config.get('psk_alert_modes', True)
        self.alert_crossref_qsy = config.get('psk_crossref_qsy', True)
        
        # Contest mode affects which bands we care about
        self.contest_mode = config.get('contest_mode', 'vhf')
        
        # State tracking
        self.running = False
        self.thread = None
        self.last_poll = None
        
        # Spot history for baseline calculations
        # {band: [(timestamp, count), ...]}
        self.spot_history = defaultdict(list)
        
        # Recent alerts to prevent spam
        # {alert_key: timestamp}
        self.recent_alerts = {}
        self.alert_cooldown = 300  # 5 minutes between same alert
        
        # QSY Advisor reference (set externally)
        self.qsy_advisor = None
        
        # Spots for UI display
        self.recent_spots = []  # [(timestamp, band, call, grid, distance, bearing, prop_mode, mode)]
        self.max_recent_spots = 50
        
        # Band activity counts for UI
        self.band_activity = defaultdict(int)  # {band: count in last poll}
        
        # Lock for thread safety
        self.lock = threading.Lock()
    
    def _get_bands_from_config(self):
        """Build bands_to_check dict from my_bands config setting"""
        # Get configured bands
        my_bands = self.config.get('my_bands', ['6m', '2m', '1.25m', '70cm', '33cm', '23cm'])
        
        # Combine all known bands
        all_bands = {}
        all_bands.update(self.HF_BANDS)
        all_bands.update(self.BANDS)
        
        # Filter to only configured bands
        bands_to_check = {}
        for band in my_bands:
            if band in all_bands:
                bands_to_check[band] = all_bands[band]
        
        return bands_to_check
    
    def _get_frequency_range(self, bands_to_check):
        """Calculate frequency range for API query based on selected bands"""
        if not bands_to_check:
            return '50000000-1300000000'  # Default VHF range
        
        # Find min and max frequencies across all selected bands
        min_freq = float('inf')
        max_freq = 0
        
        for band, (low, high) in bands_to_check.items():
            # Convert to Hz
            low_hz = low * 1_000_000
            high_hz = high * 1_000_000
            min_freq = min(min_freq, low_hz)
            max_freq = max(max_freq, high_hz)
        
        # Add some margin
        min_freq = int(min_freq * 0.99)  # 1% below
        max_freq = int(max_freq * 1.01)  # 1% above
        
        return f'{min_freq}-{max_freq}'
    
    def set_grid(self, grid):
        """Update current grid position"""
        self.my_grid = grid
        if grid:
            self.my_lat, self.my_lon = self._grid_to_latlon(grid)
        else:
            self.my_lat, self.my_lon = None, None
    
    def set_qsy_advisor(self, qsy_advisor):
        """Set reference to QSY Advisor for cross-referencing"""
        self.qsy_advisor = qsy_advisor
    
    def start(self):
        """Start the monitoring thread"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        print("PSK Monitor: Started monitoring thread (5-minute intervals)")
    
    def stop(self):
        """Stop the monitoring thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        print("PSK Monitor: Stopped")
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        # Initial delay to let app start up
        time.sleep(10)
        
        while self.running:
            try:
                if self.my_lat is not None and self.my_lon is not None:
                    self._poll_psk_reporter()
                else:
                    print("PSK Monitor: Waiting for GPS position...")
                
                self.last_poll = datetime.now()
                
            except Exception as e:
                print(f"PSK Monitor: Error in monitor loop: {e}")
            
            # Wait for next poll (5 minutes)
            for _ in range(self.poll_interval):
                if not self.running:
                    break
                time.sleep(1)
    
    def _poll_psk_reporter(self):
        """Poll PSK Reporter API for recent spots"""
        try:
            # Get bands from config (respects "My Bands" setting)
            bands_to_check = self._get_bands_from_config()
            
            # Check if we have any HF bands configured
            has_hf = any(b in self.HF_BANDS for b in bands_to_check)
            has_vhf = any(b in self.BANDS for b in bands_to_check)
            
            # Determine radius based on contest mode and bands
            if self.contest_mode == 'qso_party' or has_hf:
                radius_miles = self.hf_radius
            else:
                radius_miles = self.vhf_radius
            
            # For 222up mode, filter out 6m and 2m
            if self.contest_mode == '222up':
                bands_to_check = {k: v for k, v in bands_to_check.items() 
                                 if k not in ['6m', '2m']}
            
            # Calculate frequency range based on selected bands
            frange = self._get_frequency_range(bands_to_check)
            
            # Build API URL - query by grid field (first 2 chars = field)
            # This gets all activity in our general area
            grid_field = self.my_grid[:2] if self.my_grid else 'EM'
            
            # PSK Reporter API parameters
            # appcontact per PSK Reporter request for frequent API users
            params = {
                'encap': '0',
                'callback': '',
                'statistics': '0',
                'noactive': '1',
                'nolocator': '0',
                'flowStartSeconds': '-900',  # Last 15 minutes
                'rronly': '0',
                'frange': frange,  # Dynamic based on My Bands
                'appcontact': 'copilot-pskr@n5zy.org',  # Contact for API issues
            }
            
            url = f"https://retrieve.pskreporter.info/query?{urllib.parse.urlencode(params)}"
            
            bands_str = ', '.join(sorted(bands_to_check.keys()))
            print(f"PSK Monitor: Polling PSK Reporter for {bands_str} activity...")
            print(f"PSK Monitor: My grid: {self.my_grid}, Radius: {radius_miles} mi, frange: {frange}")
            print(f"PSK Monitor: URL: {url[:100]}...")
            
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'N5ZY-VHF-CoPilot/1.7')
            
            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read().decode('utf-8')
            
            # Log response size
            print(f"PSK Monitor: Received {len(data)} bytes")
            
            # Save raw response to file for debugging (optional)
            try:
                debug_file = 'logs/psk_debug.xml'
                os.makedirs('logs', exist_ok=True)
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(data)
                print(f"PSK Monitor: Saved raw response to {debug_file}")
            except:
                pass
            
            # Parse the response (it's XML but we'll extract what we need)
            spots = self._parse_psk_response(data, bands_to_check, radius_miles)
            
            # Process spots
            self._process_spots(spots, bands_to_check)
            
            # Notify that poll is complete (for UI timestamp update)
            if self.poll_complete_callback:
                try:
                    self.poll_complete_callback(len(spots))
                except Exception as e:
                    print(f"PSK Monitor: Error in poll_complete_callback: {e}")
            
        except urllib.error.URLError as e:
            print(f"PSK Monitor: Network error: {e}")
        except Exception as e:
            print(f"PSK Monitor: Error polling: {e}")
            import traceback
            traceback.print_exc()
    
    def _parse_psk_response(self, data, bands_to_check, radius_miles):
        """Parse PSK Reporter XML response"""
        spots = []
        
        # Simple XML parsing (avoiding external dependencies)
        # Look for receptionReport elements
        import re
        
        # Find all reception reports - try self-closing tags first (more common)
        reports = re.findall(r'<receptionReport\s+([^>]+)/>', data)
        
        if not reports:
            # Try nested element format
            reports = re.findall(r'<receptionReport[^>]*>(.*?)</receptionReport>', data, re.DOTALL)
        
        print(f"PSK Monitor: Found {len(reports)} reception reports in response")
        
        # Count spots by band for logging
        band_counts = defaultdict(int)
        nearby_count = 0
        
        for report in reports:
            try:
                # Extract fields from attributes
                def get_attr(name):
                    # Try attribute format (most common in PSK Reporter)
                    match = re.search(rf'{name}="([^"]*)"', report)
                    if match:
                        return match.group(1)
                    return None
                
                sender_call = get_attr('senderCallsign')
                sender_grid = get_attr('senderLocator')
                receiver_call = get_attr('receiverCallsign')
                receiver_grid = get_attr('receiverLocator')
                freq = get_attr('frequency')
                mode = get_attr('mode')
                snr = get_attr('sNR')
                timestamp = get_attr('flowStartSeconds')
                
                if not all([sender_call, freq]):
                    continue
                
                # Convert frequency to MHz
                try:
                    freq_hz = int(freq)
                    freq_mhz = freq_hz / 1_000_000
                except:
                    continue
                
                # Determine band
                band = self._freq_to_band(freq_mhz, bands_to_check)
                if not band:
                    continue
                
                band_counts[band] += 1
                
                # Calculate distances
                sender_dist = None
                receiver_dist = None
                sender_bearing = None
                receiver_bearing = None
                
                if sender_grid and len(sender_grid) >= 4:
                    s_lat, s_lon = self._grid_to_latlon(sender_grid)
                    if s_lat is not None:
                        sender_dist = self._haversine(self.my_lat, self.my_lon, s_lat, s_lon)
                        sender_bearing = self._bearing(self.my_lat, self.my_lon, s_lat, s_lon)
                
                if receiver_grid and len(receiver_grid) >= 4:
                    r_lat, r_lon = self._grid_to_latlon(receiver_grid)
                    if r_lat is not None:
                        receiver_dist = self._haversine(self.my_lat, self.my_lon, r_lat, r_lon)
                        receiver_bearing = self._bearing(self.my_lat, self.my_lon, r_lat, r_lon)
                
                # We want spots where at least one end is near us
                near_us = False
                nearby_call = None
                nearby_grid = None
                far_dist = None
                far_bearing = None
                far_grid = None
                
                if sender_dist is not None and sender_dist <= radius_miles:
                    near_us = True
                    nearby_call = sender_call
                    nearby_grid = sender_grid
                    far_call = receiver_call
                    far_dist = receiver_dist
                    far_bearing = receiver_bearing
                    far_grid = receiver_grid
                elif receiver_dist is not None and receiver_dist <= radius_miles:
                    near_us = True
                    nearby_call = receiver_call
                    nearby_grid = receiver_grid
                    far_call = sender_call
                    far_dist = sender_dist
                    far_bearing = sender_bearing
                    far_grid = sender_grid
                
                if near_us and far_dist is not None:
                    # Calculate QSO distance (between sender and receiver)
                    qso_dist = None
                    if sender_grid and receiver_grid:
                        s_lat, s_lon = self._grid_to_latlon(sender_grid)
                        r_lat, r_lon = self._grid_to_latlon(receiver_grid)
                        if s_lat and r_lat:
                            qso_dist = self._haversine(s_lat, s_lon, r_lat, r_lon)
                    
                    spots.append({
                        'nearby_call': nearby_call,
                        'nearby_grid': nearby_grid,
                        'far_call': far_call,
                        'far_grid': far_grid,
                        'far_dist': far_dist,
                        'far_bearing': far_bearing,
                        'qso_dist': qso_dist,
                        'band': band,
                        'freq_mhz': freq_mhz,
                        'mode': mode or 'FT8',
                        'snr': snr,
                        'timestamp': timestamp,
                        'sender_call': sender_call,
                        'sender_grid': sender_grid,
                        'receiver_call': receiver_call,
                        'receiver_grid': receiver_grid,
                    })
                    nearby_count += 1
                    
            except Exception as e:
                continue
        
        # Log summary
        print(f"PSK Monitor: Total VHF+ spots by band: {dict(band_counts)}")
        print(f"PSK Monitor: Spots within {radius_miles} mi radius: {nearby_count}")
        print(f"PSK Monitor: Parsed {len(spots)} relevant spots for analysis")
        return spots
    
    def _process_spots(self, spots, bands_to_check):
        """Process spots and generate alerts"""
        now = datetime.now()
        
        # Count spots per band
        band_counts = defaultdict(int)
        band_spots = defaultdict(list)
        
        for spot in spots:
            band = spot['band']
            band_counts[band] += 1
            band_spots[band].append(spot)
        
        with self.lock:
            self.band_activity = dict(band_counts)
        
        # Update spot history for baseline
        for band, count in band_counts.items():
            self.spot_history[band].append((now, count))
            # Keep only recent history
            cutoff = now - timedelta(minutes=self.baseline_minutes * 2)
            self.spot_history[band] = [(t, c) for t, c in self.spot_history[band] if t > cutoff]
        
        # Analyze each band
        for band, band_spot_list in band_spots.items():
            self._analyze_band_activity(band, band_spot_list, band_counts[band])
    
    def _analyze_band_activity(self, band, spots, count):
        """Analyze activity on a specific band"""
        
        # Check for propagation events
        for spot in spots:
            qso_dist = spot.get('qso_dist')
            if qso_dist is None:
                continue
            
            # Determine propagation mode
            prop_mode = self._classify_propagation(qso_dist, band, spot)
            
            # Log all nearby spots for debugging
            print(f"PSK Monitor: {band} spot - {spot['nearby_call']} QSO {int(qso_dist)}mi ({prop_mode})")
            
            # Store ALL spots for UI display (not just propagation events)
            self._add_recent_spot(spot, band, qso_dist, prop_mode)
            
            if prop_mode == 'line_of_sight':
                continue  # Don't alert for line-of-sight, but still shown in UI
            
            # Check for MSp-E (CRITICAL)
            if prop_mode == 'multi_hop_e' and band in ['2m', '1.25m', '70cm', '33cm', '23cm']:
                self._alert_mspe(spot, band, qso_dist, prop_mode)
            
            # Check for Sp-E
            elif prop_mode == 'sporadic_e' and band in ['6m', '2m', '1.25m', '70cm']:
                self._alert_spe(spot, band, qso_dist, prop_mode)
            
            # Check for Tropo
            elif prop_mode == 'tropo' and band in ['2m', '1.25m', '70cm', '33cm', '23cm']:
                self._alert_tropo(spot, band, qso_dist, prop_mode)
        
        # Check for band opening (increased activity)
        if self.alert_band_openings:
            self._check_band_opening(band, count)
        
        # Check for unusual modes
        if self.alert_unusual_modes:
            self._check_unusual_modes(band, spots)
    
    def _classify_propagation(self, distance, band, spot):
        """Classify the likely propagation mode based on distance and band"""
        
        # Check for TEP (Trans-Equatorial Propagation)
        if distance > 2000:
            sender_lat, _ = self._grid_to_latlon(spot.get('sender_grid', ''))
            receiver_lat, _ = self._grid_to_latlon(spot.get('receiver_grid', ''))
            if sender_lat and receiver_lat:
                # TEP requires one station in each hemisphere near tropics
                if ((self.TEP_LAT_RANGE[0] <= sender_lat <= self.TEP_LAT_RANGE[1]) and
                    (self.TEP_LAT_RANGE[0] <= receiver_lat <= self.TEP_LAT_RANGE[1])):
                    if sender_lat * receiver_lat < 0:  # Different hemispheres
                        return 'tep'
        
        # Distance-based classification
        if distance < 100:
            return 'line_of_sight'
        elif distance < 500:
            return 'tropo'
        elif distance < 1400:
            return 'sporadic_e'
        else:
            return 'multi_hop_e'
    
    def _alert_mspe(self, spot, band, distance, prop_mode):
        """Generate MSp-E alert (CRITICAL)"""
        if not self.alert_mspe:
            return
        
        alert_key = f"mspe_{band}_{spot['nearby_call']}"
        if self._is_alert_recent(alert_key):
            return
        
        bearing = self._bearing_to_compass(spot.get('far_bearing', 0))
        bearing_voice = self._bearing_to_voice(spot.get('far_bearing', 0))
        mode = spot.get('mode', 'FT8')
        
        # Format band name for voice
        band_voice = self._band_to_voice(band)
        
        message = f"🔴 MULTI-HOP Sp-E on {band}! {spot['nearby_call']} QSO {int(distance)} mi to {bearing}"
        voice_msg = f"Alert! Multi-hop Sporadic E on {band_voice}! {int(distance)} miles to your {bearing_voice}. Pull over!"
        
        self._send_alert(message, self.PRIORITY_CRITICAL, voice_msg, alert_key)
        
        # Cross-reference with QSY Advisor
        self._crossref_qsy(spot['nearby_call'], band)
    
    def _alert_spe(self, spot, band, distance, prop_mode):
        """Generate Sp-E alert"""
        if not self.alert_spe:
            return
        
        # 6m Sp-E is common in summer - only alert on band opening, not individual spots
        if band == '6m':
            return  # Handled by band opening detection
        
        alert_key = f"spe_{band}_{spot['nearby_call']}"
        if self._is_alert_recent(alert_key):
            return
        
        bearing = self._bearing_to_compass(spot.get('far_bearing', 0))
        bearing_voice = self._bearing_to_voice(spot.get('far_bearing', 0))
        mode = spot.get('mode', 'FT8')
        band_voice = self._band_to_voice(band)
        
        if band in ['70cm', '1.25m']:
            priority = self.PRIORITY_HIGH
            message = f"🟠 Sp-E on {band}! {spot['nearby_call']} QSO {int(distance)} mi to {bearing}"
            voice_msg = f"Alert! Sporadic E on {band_voice}! {int(distance)} miles to your {bearing_voice}"
        else:
            priority = self.PRIORITY_MEDIUM
            message = f"🟡 Sp-E on {band}: {spot['nearby_call']} QSO {int(distance)} mi to {bearing}"
            voice_msg = f"Sporadic E on {band_voice}, {int(distance)} miles to your {bearing_voice}"
        
        self._send_alert(message, priority, voice_msg, alert_key)
        self._crossref_qsy(spot['nearby_call'], band)
    
    def _alert_tropo(self, spot, band, distance, prop_mode):
        """Generate Tropo alert"""
        alert_key = f"tropo_{band}"
        if self._is_alert_recent(alert_key):
            return
        
        bearing = self._bearing_to_compass(spot.get('far_bearing', 0))
        bearing_voice = self._bearing_to_voice(spot.get('far_bearing', 0))
        band_voice = self._band_to_voice(band)
        
        message = f"🟢 Tropo on {band}: {spot['nearby_call']} {int(distance)} mi to {bearing}"
        voice_msg = f"Tropo on {band_voice}, {int(distance)} miles to your {bearing_voice}"
        
        self._send_alert(message, self.PRIORITY_MEDIUM, voice_msg, alert_key)
    
    def _check_band_opening(self, band, current_count):
        """Check if band is opening (significant increase in activity)"""
        if len(self.spot_history[band]) < 2:
            return  # Not enough history
        
        # Calculate baseline (average of previous readings)
        history = self.spot_history[band][:-1]  # Exclude current
        if not history:
            return
        
        baseline = sum(c for _, c in history) / len(history)
        
        # Opening = at least 3x baseline AND at least 5 spots
        if current_count >= 5 and baseline > 0 and current_count >= baseline * 3:
            alert_key = f"opening_{band}"
            if self._is_alert_recent(alert_key):
                return
            
            band_voice = self._band_to_voice(band)
            
            # Priority based on band
            if band in ['23cm', '13cm', '33cm']:
                priority = self.PRIORITY_HIGH
                emoji = "🟠"
            elif band in ['70cm', '1.25m']:
                priority = self.PRIORITY_MEDIUM
                emoji = "🟡"
            else:
                priority = self.PRIORITY_LOW
                emoji = "🔵"
            
            message = f"{emoji} {band} band opening! {current_count} spots (baseline: {baseline:.0f})"
            voice_msg = f"{band_voice} band opening, {current_count} stations active"
            
            self._send_alert(message, priority, voice_msg, alert_key)
    
    def _check_unusual_modes(self, band, spots):
        """Check for activity on unusual modes"""
        unusual = ['Q65', 'MSK144', 'JT65', 'FT4', 'FSK441']
        
        mode_counts = defaultdict(int)
        for spot in spots:
            mode = spot.get('mode', '').upper()
            if mode in [m.upper() for m in unusual]:
                mode_counts[mode] += 1
        
        for mode, count in mode_counts.items():
            if count >= 2:  # At least 2 spots on unusual mode
                alert_key = f"mode_{band}_{mode}"
                if self._is_alert_recent(alert_key):
                    continue
                
                band_voice = self._band_to_voice(band)
                
                message = f"📡 {mode} activity on {band}: {count} stations"
                voice_msg = f"{mode} activity on {band_voice}"
                
                self._send_alert(message, self.PRIORITY_INFO, voice_msg, alert_key)
    
    def _crossref_qsy(self, callsign, band):
        """Cross-reference with QSY Advisor database"""
        if not self.alert_crossref_qsy or not self.qsy_advisor:
            return
        
        # Check if station has multiple bands in database
        station_info = self.qsy_advisor.get_station_info(callsign)
        if station_info and len(station_info.get('bands', [])) > 1:
            bands_str = ', '.join(sorted(station_info['bands']))
            message = f"   ↳ {callsign} has multiple bands: {bands_str}"
            self._send_alert(message, self.PRIORITY_INFO, None, None)
    
    def _add_recent_spot(self, spot, band, distance, prop_mode):
        """Add spot to recent spots list and notify UI"""
        spot_data = {
            'timestamp': datetime.now(),
            'band': band,
            'nearby_call': spot['nearby_call'],  # Station near you
            'far_call': spot.get('far_call', ''),  # Station to try!
            'grid': spot.get('far_grid', ''),
            'distance': int(distance) if distance else 0,
            'bearing': self._bearing_to_compass(spot.get('far_bearing', 0)),
            'prop_mode': prop_mode,
            'mode': spot.get('mode', 'FT8'),
        }
        
        with self.lock:
            self.recent_spots.insert(0, spot_data)
            
            # Trim to max size
            if len(self.recent_spots) > self.max_recent_spots:
                self.recent_spots = self.recent_spots[:self.max_recent_spots]
        
        # Notify UI to display this spot
        if self.spot_callback:
            try:
                self.spot_callback(spot_data)
            except Exception as e:
                print(f"PSK Monitor: Error in spot callback: {e}")
    
    def _send_alert(self, message, priority, voice_msg, alert_key):
        """Send alert to callback and voice"""
        # Mark alert as sent
        if alert_key:
            self.recent_alerts[alert_key] = datetime.now()
        
        # Send to callback
        if self.alert_callback:
            self.alert_callback(message, priority)
        
        print(f"PSK Monitor: {message}")
        
        # Voice announcement
        if voice_msg and self.voice:
            self.voice.announce(voice_msg)
    
    def _is_alert_recent(self, alert_key):
        """Check if alert was sent recently (within cooldown)"""
        if alert_key not in self.recent_alerts:
            return False
        
        elapsed = (datetime.now() - self.recent_alerts[alert_key]).total_seconds()
        return elapsed < self.alert_cooldown
    
    def get_recent_spots(self):
        """Get recent spots for UI display"""
        with self.lock:
            return list(self.recent_spots)
    
    def get_band_activity(self):
        """Get current band activity counts"""
        with self.lock:
            return dict(self.band_activity)
    
    # === Utility Methods ===
    
    def _grid_to_latlon(self, grid):
        """Convert Maidenhead grid to lat/lon"""
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
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lon = math.radians(lon2 - lon1)
        
        x = math.sin(delta_lon) * math.cos(lat2_rad)
        y = (math.cos(lat1_rad) * math.sin(lat2_rad) - 
             math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon))
        
        bearing = math.atan2(x, y)
        return (math.degrees(bearing) + 360) % 360
    
    def _bearing_to_compass(self, bearing):
        """Convert bearing in degrees to compass direction"""
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        index = round(bearing / 45) % 8
        return directions[index]
    
    def _bearing_to_voice(self, bearing):
        """Convert bearing in degrees to voice-friendly compass direction"""
        directions = ['North', 'North East', 'East', 'South East', 
                      'South', 'South West', 'West', 'North West']
        index = round(bearing / 45) % 8
        return directions[index]
    
    def _freq_to_band(self, freq_mhz, bands_to_check):
        """Convert frequency in MHz to band name"""
        for band, (low, high) in bands_to_check.items():
            if low <= freq_mhz <= high:
                return band
        return None
    
    def _band_to_voice(self, band):
        """Convert band name to voice-friendly format"""
        voice_map = {
            # HF bands
            '160m': '160 meters',
            '80m': '80 meters',
            '60m': '60 meters',
            '40m': '40 meters',
            '30m': '30 meters',
            '20m': '20 meters',
            '17m': '17 meters',
            '15m': '15 meters',
            '12m': '12 meters',
            '10m': '10 meters',
            # VHF/UHF bands
            '6m': '6 meters',
            '2m': '2 meters',
            '1.25m': '1.25 meters',
            '70cm': '70 centimeters',
            '33cm': '33 centimeters',
            '23cm': '23 centimeters',
            '13cm': '13 centimeters',
            '9cm': '9 centimeters',
            '5cm': '5 centimeters',
            '3cm': '3 centimeters',
        }
        return voice_map.get(band, band)
