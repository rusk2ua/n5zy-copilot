#!/usr/bin/env python3
"""
QSO Relay Stress Test

Simulates multiple WSJT-X instances logging QSOs simultaneously
to test the queued relay to N1MM+.

This sends QSO Logged (Type 5) messages that look exactly like
real WSJT-X broadcasts.
"""

import socket
import struct
import datetime
import time
import threading

# WSJT-X Protocol Constants
MAGIC = 0xADBCCBDA
SCHEMA = 3
MSG_QSO_LOGGED = 5

def encode_qstring(text):
    """Encode string as Qt QString"""
    if text is None or text == '':
        return struct.pack('>I', 0xFFFFFFFF)
    encoded = text.encode('utf-8')
    return struct.pack('>I', len(encoded)) + encoded

def encode_qdatetime(dt):
    """Encode datetime as Qt QDateTime"""
    # Julian day calculation
    a = (14 - dt.month) // 12
    y = dt.year + 4800 - a
    m = dt.month + 12 * a - 3
    julian_day = dt.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    
    # Milliseconds since midnight
    msecs = (dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000 + dt.microsecond // 1000
    
    # Time spec: 1 = UTC
    return struct.pack('>Q', julian_day) + struct.pack('>I', msecs) + struct.pack('>B', 1)

def build_qso_logged_message(wsjtx_id, dx_call, dx_grid, freq_hz, mode, 
                              rst_sent, rst_rcvd, my_call, my_grid):
    """Build a WSJT-X QSO Logged message (Type 5)"""
    now = datetime.datetime.utcnow()
    
    # Header
    msg = struct.pack('>I', MAGIC)      # Magic
    msg += struct.pack('>I', SCHEMA)    # Schema
    msg += struct.pack('>I', MSG_QSO_LOGGED)  # Type
    
    # Fields
    msg += encode_qstring(wsjtx_id)     # WSJT-X instance ID
    msg += encode_qdatetime(now)        # Date/Time Off
    msg += encode_qstring(dx_call)      # DX Call
    msg += encode_qstring(dx_grid)      # DX Grid
    msg += struct.pack('>Q', freq_hz)   # TX Frequency (Hz)
    msg += encode_qstring(mode)         # Mode
    msg += encode_qstring(rst_sent)     # RST Sent
    msg += encode_qstring(rst_rcvd)     # RST Received
    msg += encode_qstring('')           # TX Power
    msg += encode_qstring('')           # Comments
    msg += encode_qstring('')           # Name
    msg += encode_qdatetime(now)        # Date/Time On
    msg += encode_qstring(my_call)      # Operator call
    msg += encode_qstring(my_call)      # My call
    msg += encode_qstring(my_grid)      # My grid
    msg += encode_qstring(my_grid)      # Exchange sent
    msg += encode_qstring(dx_grid)      # Exchange received
    msg += encode_qstring('')           # ADIF propagation mode
    
    return msg

def send_qso(port, wsjtx_id, dx_call, dx_grid, freq_hz, mode='FT8'):
    """Send a simulated QSO Logged message"""
    msg = build_qso_logged_message(
        wsjtx_id=wsjtx_id,
        dx_call=dx_call,
        dx_grid=dx_grid,
        freq_hz=freq_hz,
        mode=mode,
        rst_sent='-10',
        rst_rcvd='-12',
        my_call='N5ZY',
        my_grid='EM15'
    )
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(msg, ('127.0.0.1', port))
    sock.close()
    print(f"  Sent: {dx_call} on {freq_hz/1e6:.3f} MHz via {wsjtx_id} (port {port})")

def stress_test_simultaneous():
    """
    Send 3 QSOs simultaneously (within milliseconds)
    Same callsign on 3 different bands - tests if timestamp offset works.
    """
    print("\n" + "="*60)
    print("STRESS TEST: Sending 3 QSOs SIMULTANEOUSLY")
    print("="*60)
    print("\nSending W1AW on 6m, 2m, and 70cm at once.")
    print("Co-Pilot will add timestamp offsets to make each unique.\n")
    
    # Create threads to send all at once - SAME callsign, different bands
    threads = [
        threading.Thread(target=send_qso, args=(2237, 'WSJT-X - ic7610', 'W1AW', 'FN31', 50_313_000)),
        threading.Thread(target=send_qso, args=(2238, 'WSJT-X - ic9700', 'W1AW', 'FN31', 144_174_000)),
        threading.Thread(target=send_qso, args=(2239, 'WSJT-X - ic7300', 'W1AW', 'FN31', 432_065_000)),
    ]
    
    # Start all threads at once
    for t in threads:
        t.start()
    
    # Wait for all to complete
    for t in threads:
        t.join()
    
    print("\n✅ All 3 QSOs sent simultaneously!")
    print("\nCheck N1MM+ - should see 3 SEPARATE entries for W1AW:")
    print("  - W1AW on 6m")
    print("  - W1AW on 2m") 
    print("  - W1AW on 70cm")
    print("\n(Timestamps will be offset by 1 second each)")

def stress_test_burst():
    """
    Send a burst of 6 QSOs rapidly (2 rounds of 3)
    This simulates back-to-back contacts.
    """
    print("\n" + "="*60)
    print("STRESS TEST: Sending BURST of 6 QSOs")
    print("="*60)
    print("\nThis simulates two rapid multi-band contacts.\n")
    
    # Round 1: W1AW
    print("Round 1: W1AW (all 3 bands)")
    threads1 = [
        threading.Thread(target=send_qso, args=(2237, 'WSJT-X - ic7610', 'W1AW', 'FN31', 50_313_000)),
        threading.Thread(target=send_qso, args=(2238, 'WSJT-X - ic9700', 'W1AW', 'FN31', 144_174_000)),
        threading.Thread(target=send_qso, args=(2239, 'WSJT-X - ic7300', 'W1AW', 'FN31', 432_065_000)),
    ]
    for t in threads1:
        t.start()
    for t in threads1:
        t.join()
    
    # Small delay (like next sequence starting)
    time.sleep(0.5)
    
    # Round 2: K1JT
    print("\nRound 2: K1JT (all 3 bands)")
    threads2 = [
        threading.Thread(target=send_qso, args=(2237, 'WSJT-X - ic7610', 'K1JT', 'FN20', 50_313_000)),
        threading.Thread(target=send_qso, args=(2238, 'WSJT-X - ic9700', 'K1JT', 'FN20', 144_174_000)),
        threading.Thread(target=send_qso, args=(2239, 'WSJT-X - ic7300', 'K1JT', 'FN20', 432_065_000)),
    ]
    for t in threads2:
        t.start()
    for t in threads2:
        t.join()
    
    print("\n✅ All 6 QSOs sent!")
    print("\nWatch the queue - should peak at 6, then drain one at a time.")

def stress_test_extreme():
    """
    Send 12 QSOs as fast as possible
    This tests queue handling under extreme load.
    """
    print("\n" + "="*60)
    print("STRESS TEST: EXTREME - 12 QSOs as fast as possible")
    print("="*60)
    print("\nThis pushes the queue to its limits.\n")
    
    calls = ['W1AW', 'K1JT', 'N1MM', 'VE3NEA']
    
    threads = []
    for call in calls:
        for port, radio, freq in [(2237, 'ic7610', 50_313_000), 
                                   (2238, 'ic9700', 144_174_000), 
                                   (2239, 'ic7300', 432_065_000)]:
            t = threading.Thread(target=send_qso, 
                                args=(port, f'WSJT-X - {radio}', call, 'FN31', freq))
            threads.append(t)
    
    # Fire all at once
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print(f"\n✅ All {len(threads)} QSOs sent!")
    print("\nAt 750ms per relay, draining will take ~9 seconds.")
    print("Watch the queue drain in the Co-Pilot console.")

def main():
    print("="*60)
    print("N5ZY Co-Pilot QSO Relay Stress Tester")
    print("="*60)
    print()
    print("Prerequisites:")
    print("  1. Co-Pilot is running (python copilot.py)")
    print("  2. N1MM+ is running with JTDX TCP port configured:")
    print("     Config → Configure Ports → WSJT/JTDX Setup tab")
    print("     Set TCP port to 52001 (or match Co-Pilot settings)")
    print()
    print("Tests available:")
    print("  1. Simultaneous - 3 QSOs at exact same moment")
    print("  2. Burst - 6 QSOs in rapid succession")
    print("  3. Extreme - 12 QSOs as fast as possible")
    print("  4. Run all tests")
    print("  0. Exit")
    print()
    
    while True:
        choice = input("Select test (1-4, 0 to exit): ").strip()
        
        if choice == '0':
            break
        elif choice == '1':
            stress_test_simultaneous()
        elif choice == '2':
            stress_test_burst()
        elif choice == '3':
            stress_test_extreme()
        elif choice == '4':
            stress_test_simultaneous()
            time.sleep(3)
            stress_test_burst()
            time.sleep(5)
            stress_test_extreme()
        else:
            print("Invalid choice")
        
        print()

if __name__ == '__main__':
    main()
