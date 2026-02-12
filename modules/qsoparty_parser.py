"""
QSO Party County Parser Module
Parses N1MM+ QSOParty.sec file for county abbreviations
"""

import os
from pathlib import Path


def get_default_qsoparty_path():
    """Get the default path to QSOParty.sec file"""
    # Default N1MM+ location
    docs = Path.home() / "Documents" / "N1MM Logger+" / "SupportFiles" / "QSOParty.sec"
    return str(docs)


def parse_qsoparty_file(filepath):
    """
    Parse N1MM+ QSOParty.sec file
    
    Returns:
        dict: {subtype: {'name': subtype, 'counties': [list of canonical abbreviations], 
                         'aliases': {alias: canonical}}}
    
    Example return:
        {
            'OK': {
                'name': 'OK',
                'counties': ['ADA', 'ALF', 'ATO', ...],
                'aliases': {'ADAIR': 'ADA', ...}
            },
            'MAQP': {
                'name': 'MAQP', 
                'counties': [...],
                'aliases': {...}
            }
        }
    """
    qso_parties = {}
    
    if not os.path.exists(filepath):
        print(f"QSOParty: File not found: {filepath}")
        return qso_parties
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"QSOParty: Error reading file: {e}")
        return qso_parties
    
    current_subtype = None
    current_data = None
    
    for line in lines:
        line = line.strip()
        
        # Skip empty lines and comments
        if not line or line.startswith("'"):
            continue
        
        # Check for new section
        if line.startswith("Type=QSOPARTY") and "SubType=" in line:
            # Save previous section if exists
            if current_subtype and current_data:
                qso_parties[current_subtype] = current_data
            
            # Extract SubType
            parts = line.split("SubType=")
            if len(parts) >= 2:
                current_subtype = parts[1].strip()
                current_data = {
                    'name': current_subtype,
                    'counties': [],
                    'aliases': {}
                }
            continue
        
        # Skip if we're not in a section or it's REVISION
        if not current_subtype or current_subtype == 'REVISION':
            continue
        
        # Parse county line
        parts = line.split()
        
        if len(parts) == 1:
            # Single abbreviation - this is the canonical form
            abbrev = parts[0].upper()
            if abbrev not in current_data['counties']:
                current_data['counties'].append(abbrev)
        
        elif len(parts) >= 2:
            # Alias line: "AZAPH APH" means AZAPH -> APH
            alias = parts[0].upper()
            canonical = parts[1].upper()
            
            # Make sure canonical is in the list
            if canonical not in current_data['counties']:
                current_data['counties'].append(canonical)
            
            # Add alias mapping
            current_data['aliases'][alias] = canonical
    
    # Don't forget the last section
    if current_subtype and current_data and current_subtype != 'REVISION':
        qso_parties[current_subtype] = current_data
    
    print(f"QSOParty: Loaded {len(qso_parties)} QSO parties from {filepath}")
    
    return qso_parties


def get_canonical_county(qso_party_data, input_abbrev):
    """
    Get the canonical county abbreviation from user input
    
    Args:
        qso_party_data: dict with 'counties' and 'aliases' keys
        input_abbrev: What the user typed/selected
    
    Returns:
        Canonical abbreviation or None if not found
    """
    input_upper = input_abbrev.upper()
    
    # Check if it's already canonical
    if input_upper in qso_party_data['counties']:
        return input_upper
    
    # Check aliases
    if input_upper in qso_party_data['aliases']:
        return qso_party_data['aliases'][input_upper]
    
    return None


# For display in dropdown - combine counties and show sorted
def get_county_list_for_display(qso_party_data):
    """Get sorted list of counties for dropdown display"""
    return sorted(qso_party_data.get('counties', []))


# Test code
if __name__ == '__main__':
    # Test with default path
    default_path = get_default_qsoparty_path()
    print(f"Default path: {default_path}")
    print()
    
    # Try to parse
    parties = parse_qsoparty_file(default_path)
    
    if parties:
        print(f"\nFound {len(parties)} QSO parties:")
        for code in sorted(parties.keys())[:20]:  # Show first 20
            data = parties[code]
            num_counties = len(data['counties'])
            num_aliases = len(data['aliases'])
            print(f"  {code}: {num_counties} counties, {num_aliases} aliases")
        
        if len(parties) > 20:
            print(f"  ... and {len(parties) - 20} more")
        
        # Test Oklahoma if available
        if 'OK' in parties:
            print(f"\nOklahoma QSO Party:")
            ok_data = parties['OK']
            print(f"  Counties: {ok_data['counties'][:10]}...")
            print(f"  Sample aliases: {dict(list(ok_data['aliases'].items())[:5])}")
    else:
        print("No QSO parties found. File may not exist at default location.")
        print("This is normal if N1MM+ is not installed or installed elsewhere.")
