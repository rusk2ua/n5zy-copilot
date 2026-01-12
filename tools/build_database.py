#!/usr/bin/env python3
"""
Build comprehensive VHF station database from contest results.
Extracts callsigns, grids, and band capabilities from published results.
"""

import json
import os
from datetime import datetime

# Database file location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'data')
DB_FILE = os.path.join(DATA_DIR, 'station_bands.json')

def load_existing_db():
    """Load existing database if it exists."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_db(db):
    """Save database to file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=2)
    print(f"Database saved: {len(db)} stations")

def add_station(db, call, bands, grids, contest=None, notes=None):
    """Add or update a station in the database."""
    call = call.upper().replace('/R', '').strip()
    
    if call not in db:
        db[call] = {
            'bands': [],
            'grids': [],
            'contests': [],
            'notes': '',
            'last_seen': ''
        }
    
    # Add bands (convert to list if needed)
    if isinstance(bands, str):
        bands = [bands]
    for band in bands:
        if band and band not in db[call]['bands']:
            db[call]['bands'].append(band)
    
    # Add grids
    if isinstance(grids, str):
        grids = [grids]
    for grid in grids:
        if grid and len(grid) >= 4 and grid not in db[call]['grids']:
            db[call]['grids'].append(grid)
    
    # Add contest
    if contest and contest not in db[call]['contests']:
        db[call]['contests'].append(contest)
        # Keep only last 5 contests
        db[call]['contests'] = db[call]['contests'][-5:]
    
    # Update notes
    if notes:
        db[call]['notes'] = notes
    
    # Update last seen
    db[call]['last_seen'] = datetime.now().strftime('%Y-%m-%d')
    
    return db

# ============================================================================
# DATA FROM JUNE 2025 VHF CONTEST RESULTS PDF
# ============================================================================

# Multi-band stations (SO-HP, SO-LP operate 6m through microwaves typically)
# Format: (call, grid, category_hint)

JUNE_2025_SOHP = [
    # Top SO-HP stations - typically 6m, 2m, 70cm, and often higher
    ('K1TEO', 'FN31', ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G']),
    ('N2JMH', 'FN12', ['50', '144', '222', '432', '902', '1296']),
    ('K1TO', 'EL87', ['50']),  # 6m only per soapbox
    ('WB2FKO', 'EL89', ['50', '144', '222', '432']),
    ('N4OGW', 'FN42', ['50', '144', '432']),
    ('K1KG', 'FN42', ['50', '144', '222', '432', '1296']),
    ('N5RZ', 'EM00', ['50', '144', '222', '432', '902', '1296']),
    ('N3MK', 'FM27', ['50', '144', '222', '432', '902', '1296']),
    ('N8LRG', 'EN80', ['50', '144', '222', '432', '902', '1296']),
    ('KR1ST', 'FN21', ['50', '144', '222', '432']),
    ('K5TR', 'EM00', ['50', '144', '222', '432', '902', '1296', '2304', '10G']),
    ('W2FU', 'FN13', ['50', '144', '222', '432', '1296']),
    ('WA1PBU', 'FN42', ['50', '144', '222', '432', '1296']),
    ('K1TR', 'FN42', ['50', '144', '222', '432', '1296']),
    ('XE2X', 'EL06', ['50', '144']),
    ('K6MI', 'DM05', ['50', '144', '222', '432', '1296']),
    ('WB4WXE', 'EM74', ['50', '144', '222', '432']),
    ('NE8P', 'EL99', ['50', '144']),
    ('W2KV', 'FN20', ['50', '144', '222', '432']),
    ('K5LLL', 'EM10', ['50', '144', '222', '432']),
    ('NJ6D', 'DM12', ['50', '144', '222', '432']),
    ('N7NT', 'DM33', ['50', '144', '222', '432']),
    ('KA6BIM', 'CN84', ['50']),
    ('N5KO', 'CM87', ['50', '144', '222', '432']),
    ('W6BVB', 'DM14', ['50', '144']),
    ('K9MU', 'EN52', ['50', '144', '222', '432', '902', '1296']),
    ('K9CT', 'EN50', ['50', '144', '222', '432']),
    ('N8HRZ', 'EN80', ['50', '144', '222', '432', '902', '1296']),
    ('N4SV', 'EM66', ['50', '144', '222', '432', '902', '1296']),
]

JUNE_2025_SOLP = [
    ('WN3A', 'FN10', ['50', '144', '222', '432']),
    ('NR2C', 'FN03', ['50', '144', '222', '432']),
    ('AG6X', 'DM12', ['50', '144', '222', '432']),
    ('W0UC', 'EN44', ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G']),
    ('NF3R', 'FN20', ['50', '144', '222', '432']),
    ('K6JO', 'DM13', ['50', '144', '222', '432']),
    ('N2WK', 'FN03', ['50', '144', '222', '432']),
    ('KA2ENE', 'FN13', ['50', '144', '222', '432']),
    ('N2SCJ', 'FM29', ['50', '144', '222', '432']),
    ('VE3VHF', 'FN03', ['50', '144', '222', '432']),
    ('AF1T', 'FN43', ['50', '144', '222', '432', '902', '1296', '2304']),
    ('VE3KH', 'FN03', ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G', '24G', '47G', '78G']),
    ('WB2VVV', 'FN41', ['50', '144', '222', '432']),
    ('WB2JAY', 'FN30', ['50', '144', '222', '432']),
    ('KG9AP', 'EM59', ['50', '144', '222', '432']),
]

# Single Op Portable
JUNE_2025_PORTABLE = [
    ('K5ND', 'EM01', ['50', '144', '222', '432', '902']),  # Your buddy!
    ('K7MWD', 'DM44', ['50', '144']),
    ('KF4VTT', 'FM13', ['50', '144']),
    ('KN6OKY', 'DM12', ['50', '144']),
    ('W7JET', 'DM43', ['50', '144', '222', '432']),
]

# 3-Band stations (6m, 2m, 70cm)
JUNE_2025_3BAND = [
    ('KO9A', 'EN52', ['50', '144', '432']),
    ('W5TRL', 'EM10', ['50', '144', '432']),
    ('K2PS', 'EL98', ['50', '144', '432']),
    ('WQ5L', 'EM50', ['50', '144', '432']),
    ('K1HC', 'FN53', ['50', '144', '432']),
    ('AA5PR', 'DL79', ['50', '144', '432']),
    ('K0NR', 'DM78', ['50', '144', '432']),
    ('NS4T', 'EM73', ['50', '144', '432']),
    ('KW2E', 'EM74', ['50', '144', '432']),
    ('VA2CY', 'FN46', ['50', '144', '432']),
    ('N1JD', 'FN44', ['50', '144', '432']),
    ('AA2A', 'FN32', ['50', '144', '432']),
    ('K8MR', 'EN91', ['50', '144', '432']),
    ('WB9HFK', 'EN50', ['50', '144', '432']),
    ('N0UI', 'EM38', ['50', '144', '432']),
    ('WA5LFD', 'EM12', ['50', '144', '432']),
]

# Classic Rovers with grids activated
JUNE_2025_ROVERS = [
    ('N7GP', ['DM31', 'DM32', 'DM33', 'DM34', 'DM35', 'DM42', 'DM43', 'DM44'], ['50', '144', '222', '432', '902', '1296', '2304', '10G']),
    ('NV4B', ['EM43', 'EM44', 'EM45', 'EM46', 'EM47', 'EM48', 'EM53', 'EM54', 'EM55', 'EM56', 'EM57', 'EM58', 'EM64', 'EM65', 'EM66', 'EM67', 'EM68', 'EM74', 'EM75', 'EM76', 'EM77', 'EM78'], ['50', '144', '222', '432', '902', '1296', '2304']),
    ('VE3OIL', ['EN81', 'EN82', 'EN92', 'EN93', 'FN02', 'FN03', 'FN04', 'FN13', 'FN14'], ['50', '144', '222', '432', '1296']),
    ('AG4V', ['EM44', 'EM45', 'EM54', 'EM55'], ['50', '144', '222', '432', '902', '1296']),
    ('K2QO', ['FN02', 'FN03', 'FN12', 'FN13', 'FN22', 'FN23'], ['50', '144', '222', '432', '1296']),
    ('N5ZY', ['EM25', 'EM26', 'EM35'], ['50', '144', '222', '902']),  # That's you!
    ('KF2MR', ['FN02', 'FN13'], ['50', '144', '222', '432']),
    ('VA3ELE', ['EN92', 'EN93', 'FN02', 'FN03', 'FN04', 'FN13', 'FN14'], ['50', '144', '222', '432']),
    ('W0AUS', ['EN23', 'EN24', 'EN33', 'EN34', 'EN43', 'EN44'], ['50', '144', '222', '432', '902', '1296', '5760', '10G']),
    ('K2ET', ['FN02', 'FN03', 'FN12', 'FN13'], ['50', '144', '222', '432', '1296']),
    ('KA5D', ['EL08', 'EL09', 'EL18', 'EL19', 'EM00', 'EM01', 'EM10', 'EM11'], ['50', '144', '222', '432']),
    ('KG9OV', ['EM47', 'EM49', 'EM59', 'EN40', 'EN50'], ['50', '144', '222', '432', '902', '1296']),
    ('W5TN', ['EL08', 'EL09', 'EL18', 'EL19', 'EM00', 'EM01', 'EM10', 'EM11'], ['50', '144', '222', '432']),
    ('N6GP', ['DM03', 'DM04', 'DM13', 'DM14'], ['50', '144', '222', '432', '902', '1296', '2304', '10G']),
    ('KC9NJZ', ['EN51', 'EN52', 'EN61', 'EN62'], ['50', '144', '222', '432']),
    ('W5HI', ['EM03', 'EM04', 'EM12', 'EM13', 'EM14'], ['50', '144', '222', '432']),
    ('KK6MC', ['DM32', 'DM33', 'DM41', 'DM42', 'DM43', 'DM44', 'DM52', 'DM62', 'DM63', 'DM64', 'DM65'], ['50', '144', '222', '432']),
    ('WR7X', ['DN04', 'DN05', 'DN14', 'DN15'], ['50', '144', '222', '432']),
    ('K4CNY', ['EM43', 'EM44', 'EM45', 'EM46', 'EM47', 'EM48', 'EM53', 'EM54', 'EM55', 'EM56', 'EM57', 'EM58', 'EM64', 'EM65', 'EM66', 'EM67', 'EM68', 'EM74', 'EM75', 'EM76', 'EM77', 'EM78'], ['50', '144', '222', '432', '902', '1296']),
    ('N0LD', ['EM15', 'EM24', 'EM25'], ['50', '144', '222', '432', '902', '1296', '2304', '5760', '10G']),
    ('K9JK', ['EN51', 'EN52', 'EN61', 'EN62'], ['50', '144', '222', '432', '902', '1296']),
    ('AA9RK', ['EN51', 'EN52', 'EN61', 'EN62'], ['50', '144', '222', '432']),
]

# Multi-op stations (typically full complement of bands)
JUNE_2025_MULTIOP = [
    ('N2NT', 'FN20', ['50', '144', '222', '432', '902', '1296', '2304', '5760', '10G']),
    ('AA4ZZ', 'EM96', ['50', '144', '222', '432', '902', '1296']),
    ('K5N', 'EM31', ['50', '144', '222', '432', '902', '1296', '2304', '10G']),  # K5QE's station!
    ('KE8FD', 'EN80', ['50', '144', '222', '432', '902', '1296']),
    ('AD4ES', 'EL98', ['50', '144', '222', '432', '902', '1296']),
    ('WB9Z', 'EN60', ['50', '144', '222', '432', '1296']),
    ('W2LV', 'FN21', ['50', '144', '222', '432']),
    ('W3SO', 'FN00', ['50', '144', '222', '432', '1296']),
    ('W2SZ', 'FN32', ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G']),
    ('W3CCX', 'FN21', ['50', '144', '222', '432', '902', '1296', '2304', '5760', '10G']),
    ('VE3WCC', 'FN15', ['50', '144', '222', '432', '902', '1296']),
    ('N8GA', 'EN80', ['50', '144', '222', '432', '902', '1296', '2304', '5760', '10G']),
    ('W4NH', 'EM84', ['50', '144', '222', '432', '902', '1296']),
    ('KD2LGX', 'FN13', ['50', '144', '222', '432', '1296']),
    ('WD9EXD', 'EM57', ['50', '144', '222', '432', '902', '1296', '2304', '10G']),
    ('KV1J', 'FN44', ['50', '144', '222', '432', '902', '1296', '2304', '5760', '10G']),
    ('N4QWZ', 'EM66', ['50', '144', '222', '432', '902', '1296']),
    ('WE1P', 'FN22', ['50', '144', '222', '432', '1296']),
]

# Additional key stations from central/south regions (your operating area)
CENTRAL_SOUTH_STATIONS = [
    ('K5QE', 'EM31', ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G'], 'East Texas superstation'),
    ('K5TRA', 'EM20', ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G'], 'West Texas microwave'),
    ('W5LUA', 'EM13', ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G'], 'Microwave pioneer'),
    ('AA5AM', 'EM12', ['50', '144', '222', '432'], 'Active on all VHF bands'),
    ('K5CM', 'EM25', ['50', '144', '222', '432', '902', '1296', '2304'], 'Central OK'),
    ('N0JK', 'EM28', ['50', '144', '222', '432'], 'Kansas rover legend'),
    ('W5PR', 'EM20', ['50', '144', '222', '432', '902', '1296', '2304', '5760', '10G'], 'West Texas multi-band'),
    ('AF5Q', 'EM12', ['50', '144', '222', '432'], 'North Texas'),
    ('WQ5S', 'EM35', ['50', '144', '222', '432'], 'Oklahoma'),
    ('N5JEH', 'EM13', ['50', '144', '222', '432', '902', '1296'], 'Dallas area'),
    ('K9JK', 'EN52', ['50', '144', '222', '432', '902', '1296'], 'Midwest rover'),
    ('WD5USA', 'EM22', ['50', '144', '222', '432'], 'Oklahoma'),
    ('W0ZQ', 'EN34', ['50', '144', '222', '432', '902', '1296'], 'Nebraska'),
    ('N5DG', 'EM00', ['50', '144', '222', '432', '902', '1296'], 'South Texas'),
    ('KG5VK', 'EM13', ['50', '144', '222', '432'], 'Dallas area'),
    ('K0GU', 'DM79', ['50', '144', '222', '432', '902', '1296'], 'Colorado'),
    ('W9RM', 'EN52', ['50', '144', '222', '432', '902', '1296', '2304', '10G'], 'Midwest'),
    ('N5LBZ', 'EM36', ['50', '144', '222', '432', '902', '1296'], 'Oklahoma panhandle area'),
]


def build_database():
    """Build the comprehensive database."""
    db = load_existing_db()
    
    # Add SO-HP stations
    for call, grid, bands in JUNE_2025_SOHP:
        add_station(db, call, bands, grid, 'Jun VHF 2025')
    
    # Add SO-LP stations
    for call, grid, bands in JUNE_2025_SOLP:
        add_station(db, call, bands, grid, 'Jun VHF 2025')
    
    # Add Portable stations
    for call, grid, bands in JUNE_2025_PORTABLE:
        add_station(db, call, bands, grid, 'Jun VHF 2025')
    
    # Add 3-Band stations
    for call, grid, bands in JUNE_2025_3BAND:
        add_station(db, call, bands, grid, 'Jun VHF 2025')
    
    # Add Rovers
    for entry in JUNE_2025_ROVERS:
        call, grids, bands = entry
        add_station(db, call, bands, grids, 'Jun VHF 2025')
    
    # Add Multi-op stations
    for call, grid, bands in JUNE_2025_MULTIOP:
        add_station(db, call, bands, grid, 'Jun VHF 2025')
    
    # Add Central/South key stations with notes
    for entry in CENTRAL_SOUTH_STATIONS:
        if len(entry) == 4:
            call, grid, bands, notes = entry
            add_station(db, call, bands, grid, 'Jun VHF 2025', notes)
        else:
            call, grid, bands = entry
            add_station(db, call, bands, grid, 'Jun VHF 2025')
    
    # Sort bands for each station
    band_order = ['50', '144', '222', '432', '902', '1296', '2304', '3456', '5760', '10G', '24G', '47G', '78G']
    for call in db:
        db[call]['bands'] = sorted(db[call]['bands'], 
                                   key=lambda x: band_order.index(x) if x in band_order else 99)
    
    save_db(db)
    
    # Print summary
    print("\n=== Database Summary ===")
    print(f"Total stations: {len(db)}")
    
    # Count by number of bands
    band_counts = {}
    for call, data in db.items():
        n = len(data['bands'])
        band_counts[n] = band_counts.get(n, 0) + 1
    
    print("\nStations by band count:")
    for n in sorted(band_counts.keys(), reverse=True):
        print(f"  {n} bands: {band_counts[n]} stations")
    
    # Show stations with 6+ bands (likely QSY targets)
    print("\nMulti-band stations (6+ bands) - great QSY targets:")
    for call, data in sorted(db.items()):
        if len(data['bands']) >= 6:
            bands_str = ', '.join(data['bands'])
            print(f"  {call}: {bands_str}")

if __name__ == '__main__':
    build_database()
