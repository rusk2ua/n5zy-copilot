#!/usr/bin/env python3
"""
Test the QSY Advisor functionality
"""

import sys
import os

# Add the project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Import QSY Advisor directly (avoid modules/__init__.py which needs serial)
import importlib.util
spec = importlib.util.spec_from_file_location("qsy_advisor", 
    os.path.join(project_root, "modules", "qsy_advisor.py"))
qsy_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qsy_module)
QSYAdvisor = qsy_module.QSYAdvisor


def qsy_alert(callsign, worked_band, available_bands, message):
    """Example callback for QSY alerts"""
    print(f"\n🔔 QSY ALERT: {message}")
    print(f"   Worked: {worked_band} MHz")
    print(f"   Available: {', '.join(available_bands)}")


def main():
    print("=" * 60)
    print("QSY Advisor Test")
    print("=" * 60)
    
    # Initialize advisor
    advisor = QSYAdvisor()
    advisor.set_qsy_callback(qsy_alert)
    
    # Show database stats
    stats = advisor.get_stats()
    print(f"\nDatabase contains {stats['total_stations']} stations")
    
    # Start a new contest
    advisor.start_contest()
    
    print("\n" + "-" * 60)
    print("Simulating contest QSOs...")
    print("-" * 60)
    
    # Simulate working some stations
    test_qsos = [
        ('K5QE', '144', 'EM31'),      # Big multi-band station
        ('W5ZN', '50', 'EM35'),       # Another multi-band
        ('N5XYZ', '144', 'EM15'),     # Unknown station - no alert
        ('K5QE', '432', 'EM31'),      # Work K5QE again on 70cm - should still have more
        ('W5LUA', '1296', 'EM13'),    # Microwave station
    ]
    
    for call, band, grid in test_qsos:
        print(f"\n📻 Logged QSO: {call} on {band} MHz from {grid}")
        result = advisor.log_qso(call, band, grid)
        
        if not result:
            info = advisor.get_station_info(call)
            if info:
                print(f"   (Already worked on all known bands or no new bands)")
            else:
                print(f"   (Station not in database)")
    
    print("\n" + "=" * 60)
    print("Current contest status:")
    print("=" * 60)
    
    stats = advisor.get_stats()
    print(f"Stations worked: {stats['current_contest_stations']}")
    print(f"Total QSOs: {stats['current_contest_qsos']}")
    
    # Show what's left for K5QE
    print("\n" + "-" * 60)
    print("Checking K5QE band status:")
    print("-" * 60)
    
    info = advisor.get_station_info('K5QE')
    if info:
        print(f"K5QE operates on: {', '.join(info['band_names'])}")
        unworked = advisor.get_unworked_bands('K5QE')
        if unworked:
            unworked_names = [advisor.BAND_NAMES.get(b, b) for b in unworked]
            print(f"Not yet worked on: {', '.join(unworked_names)}")
        else:
            print("All bands worked!")


if __name__ == '__main__':
    main()
