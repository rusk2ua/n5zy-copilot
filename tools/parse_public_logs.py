#!/usr/bin/env python3
"""
Parse ARRL Public VHF Contest Logs

This tool parses Cabrillo log data from ARRL's public logs page to extract
which bands each station operates. Use this to build your QSY Advisor database.

USAGE:
    Method 1 - Paste log content:
        python parse_public_logs.py --paste
        (then paste the log content and press Ctrl+D or Ctrl+Z)
    
    Method 2 - From a text file with one or more logs:
        python parse_public_logs.py logs.txt
    
    Method 3 - Interactive mode (paste multiple logs):
        python parse_public_logs.py --interactive

HOW TO GET LOG DATA:
    1. Go to https://contests.arrl.org/publiclogs.php?eid=12&iid=1094
    2. Click on a callsign (e.g., K5QE)
    3. Select all the log text (Ctrl+A) and copy (Ctrl+C)
    4. Run this tool with --paste and paste the content

The tool extracts:
    - Callsign
    - All bands they made QSOs on
    - Their grid square
    - Contest name
"""

import sys
import os
import re
import json
from datetime import datetime
from pathlib import Path

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def freq_to_band(freq_str):
    """Convert frequency (in MHz or kHz) to band identifier"""
    try:
        freq = float(freq_str)
        # If it looks like kHz (> 1000), convert to MHz
        if freq > 1000:
            freq = freq / 1000
        
        if 50 <= freq < 54:
            return '50'
        elif 144 <= freq < 148:
            return '144'
        elif 222 <= freq < 225:
            return '222'
        elif 420 <= freq < 450:
            return '432'
        elif 902 <= freq < 928:
            return '902'
        elif 1240 <= freq < 1300:
            return '1296'
        elif 2300 <= freq < 2450:
            return '2304'
        elif 3300 <= freq < 3500:
            return '3456'
        elif 5650 <= freq < 5925:
            return '5760'
        elif 10000 <= freq < 10500:
            return '10368'
        else:
            return None
    except:
        return None


def parse_cabrillo_log(log_text):
    """
    Parse a Cabrillo format log and extract station info
    
    Returns dict with:
        - callsign: str
        - bands: set of band strings
        - grid: str (4-char)
        - contest: str
        - qso_count: int
    """
    result = {
        'callsign': None,
        'bands': set(),
        'grid': None,
        'contest': None,
        'qso_count': 0
    }
    
    lines = log_text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        
        # Parse header fields
        if line.startswith('CALLSIGN:'):
            call = line.split(':', 1)[1].strip()
            # Remove /R, /P suffixes for base call
            result['callsign'] = re.sub(r'/[RPM]$', '', call.upper())
        
        elif line.startswith('CONTEST:'):
            result['contest'] = line.split(':', 1)[1].strip()
        
        elif line.startswith('GRID-LOCATOR:') or line.startswith('LOCATION:'):
            grid = line.split(':', 1)[1].strip()
            if len(grid) >= 4 and grid[:2].isalpha() and grid[2:4].isdigit():
                result['grid'] = grid[:4].upper()
        
        # Parse QSO lines
        elif line.startswith('QSO:'):
            parts = line.split()
            if len(parts) >= 2:
                freq = parts[1]
                band = freq_to_band(freq)
                if band:
                    result['bands'].add(band)
                    result['qso_count'] += 1
                    
                    # Try to extract grid from QSO line (usually position 8 or 9)
                    # Format: QSO: freq mode date time mycall mygrid theircall theirgrid
                    if not result['grid']:
                        for part in parts[4:8]:
                            if len(part) >= 4 and part[:2].isalpha() and part[2:4].isdigit():
                                result['grid'] = part[:4].upper()
                                break
    
    return result


def parse_multiple_logs(text):
    """Parse text that may contain multiple Cabrillo logs"""
    # Split on START-OF-LOG markers
    logs = re.split(r'(?=START-OF-LOG:)', text)
    
    results = []
    for log in logs:
        if 'START-OF-LOG:' in log and 'QSO:' in log:
            parsed = parse_cabrillo_log(log)
            if parsed['callsign'] and parsed['bands']:
                results.append(parsed)
    
    return results


def update_database(parsed_logs, db_path=None):
    """Update the station_bands.json database with parsed log data"""
    if db_path is None:
        db_path = Path(__file__).parent.parent / 'data' / 'station_bands.json'
    
    db_path = Path(db_path)
    
    # Load existing database
    if db_path.exists():
        with open(db_path, 'r') as f:
            database = json.load(f)
    else:
        database = {}
    
    # Update with new data
    stations_added = 0
    stations_updated = 0
    
    for log in parsed_logs:
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
        if log['contest']:
            contests = database[call].get('contests', [])
            if log['contest'] not in contests:
                contests.append(log['contest'])
            database[call]['contests'] = contests
    
    # Save database
    db_path.parent.mkdir(exist_ok=True)
    with open(db_path, 'w') as f:
        json.dump(database, f, indent=2)
    
    return stations_added, stations_updated, len(database)


def print_parsed_log(log):
    """Pretty print a parsed log"""
    band_names = {
        '50': '6m', '144': '2m', '222': '1.25m', '432': '70cm',
        '902': '33cm', '1296': '23cm', '2304': '13cm', '3456': '9cm',
        '5760': '6cm', '10368': '3cm'
    }
    
    bands = sorted(log['bands'], key=lambda b: int(b) if b.isdigit() else 0)
    band_str = ', '.join([band_names.get(b, b) for b in bands])
    
    print(f"  {log['callsign']}: {band_str}")
    if log['grid']:
        print(f"    Grid: {log['grid']}")
    print(f"    QSOs: {log['qso_count']}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Parse ARRL public VHF contest logs to build station database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('files', nargs='*', help='Log files to parse')
    parser.add_argument('--paste', '-p', action='store_true',
                        help='Read log from stdin (paste mode)')
    parser.add_argument('--interactive', '-i', action='store_true',
                        help='Interactive mode - parse multiple pasted logs')
    parser.add_argument('--no-update', '-n', action='store_true',
                        help='Parse only, do not update database')
    parser.add_argument('--database', '-d', 
                        help='Path to station database JSON file')
    
    args = parser.parse_args()
    
    all_parsed = []
    
    if args.paste:
        print("Paste the Cabrillo log content, then press Ctrl+D (Unix) or Ctrl+Z (Windows):")
        text = sys.stdin.read()
        logs = parse_multiple_logs(text)
        all_parsed.extend(logs)
        
    elif args.interactive:
        print("Interactive mode. Paste each log and press Enter twice when done.")
        print("Type 'quit' or 'done' to finish.\n")
        
        while True:
            print("-" * 40)
            print("Paste log (or 'done' to finish):")
            
            lines = []
            empty_count = 0
            
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                    
                if line.lower() in ('quit', 'done', 'exit'):
                    break
                    
                if line == '':
                    empty_count += 1
                    if empty_count >= 2:
                        break
                else:
                    empty_count = 0
                    
                lines.append(line)
            
            if not lines or lines[0].lower() in ('quit', 'done', 'exit'):
                break
            
            text = '\n'.join(lines)
            logs = parse_multiple_logs(text)
            
            if logs:
                print(f"\nParsed {len(logs)} log(s):")
                for log in logs:
                    print_parsed_log(log)
                all_parsed.extend(logs)
            else:
                print("No valid log data found in that paste.")
    
    elif args.files:
        for filepath in args.files:
            if os.path.exists(filepath):
                print(f"Parsing: {filepath}")
                with open(filepath, 'r') as f:
                    text = f.read()
                logs = parse_multiple_logs(text)
                all_parsed.extend(logs)
                print(f"  Found {len(logs)} log(s)")
            else:
                print(f"File not found: {filepath}")
    
    else:
        parser.print_help()
        return
    
    # Summary
    print("\n" + "=" * 50)
    print(f"Total logs parsed: {len(all_parsed)}")
    
    if all_parsed:
        print("\nStations found:")
        for log in all_parsed:
            print_parsed_log(log)
        
        # Update database unless --no-update
        if not args.no_update:
            print("\n" + "-" * 50)
            added, updated, total = update_database(all_parsed, args.database)
            print(f"Database updated:")
            print(f"  New stations: {added}")
            print(f"  Updated stations: {updated}")
            print(f"  Total in database: {total}")
        else:
            print("\n(Database not updated - use without --no-update to save)")


if __name__ == '__main__':
    main()
