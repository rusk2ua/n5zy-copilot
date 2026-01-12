#!/usr/bin/env python3
"""
Import Station Band Data from Cabrillo Logs

This tool parses Cabrillo log files from VHF contests and extracts
which bands each station operated. This data is used by the QSY Advisor
to suggest band changes when you work a multi-band capable station.

Usage:
    python import_cabrillo.py mylog1.log mylog2.log ...
    python import_cabrillo.py *.log

The tool will:
1. Parse each Cabrillo file
2. Extract callsigns and the bands they worked you on
3. Add them to the station database (data/station_bands.json)
"""

import sys
import os
import glob

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.qsy_advisor import QSYAdvisor


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nUsage: python import_cabrillo.py <cabrillo_file(s)>")
        print("\nExample:")
        print("  python import_cabrillo.py n5zy_jan2025.log")
        print("  python import_cabrillo.py *.log")
        return
    
    # Expand wildcards
    files = []
    for pattern in sys.argv[1:]:
        if '*' in pattern or '?' in pattern:
            files.extend(glob.glob(pattern))
        else:
            files.append(pattern)
    
    if not files:
        print("No matching files found")
        return
    
    # Initialize advisor
    advisor = QSYAdvisor()
    
    print(f"\nStation database before import: {len(advisor.stations)} stations")
    
    # Import each file
    for filepath in files:
        if os.path.exists(filepath):
            print(f"\nImporting: {filepath}")
            
            # Derive contest name from filename
            basename = os.path.basename(filepath)
            contest_name = os.path.splitext(basename)[0]
            
            advisor.import_from_cabrillo(filepath, contest_name)
        else:
            print(f"File not found: {filepath}")
    
    print(f"\nStation database after import: {len(advisor.stations)} stations")
    
    # Show statistics
    stats = advisor.get_stats()
    print(f"\nBand distribution:")
    for num_bands, count in sorted(stats['band_distribution'].items()):
        print(f"  {num_bands} band(s): {count} stations")
    
    # Show some multi-band stations
    print("\nMulti-band stations (3+ bands):")
    multi_band = [(call, info) for call, info in advisor.stations.items() 
                  if len(info['bands']) >= 3]
    multi_band.sort(key=lambda x: -len(x[1]['bands']))
    
    for call, info in multi_band[:20]:
        bands = sorted(info['bands'], key=lambda b: int(b) if b.isdigit() else 0)
        band_names = [advisor.BAND_NAMES.get(b, b) for b in bands]
        print(f"  {call}: {', '.join(band_names)}")


if __name__ == '__main__':
    main()
