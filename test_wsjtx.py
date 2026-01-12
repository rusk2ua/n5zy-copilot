"""
WSJT-X Location Update Test Tool
Discovers the actual port WSJT-X is listening on, then tests LocationChange messages
"""

import socket
import struct
import sys
import time

# WSJT-X Protocol Constants
MAGIC = 0xADBCCBDA
SCHEMA = 3  # WSJT-X 2.7.0 uses schema 3
MSG_HEARTBEAT = 0
MSG_LOCATION = 11

def encode_qstring(text):
    """Encode a string as Qt QString"""
    if text is None or text == '':
        return struct.pack('>I', 0xFFFFFFFF)
    
    encoded = text.encode('utf-8')
    length = len(encoded)
    return struct.pack('>I', length) + encoded

def decode_qstring(data, offset):
    """Decode a Qt QString from packet"""
    if len(data) < offset + 4:
        return '', offset
    
    length = struct.unpack('>I', data[offset:offset+4])[0]
    offset += 4
    
    if length == 0xFFFFFFFF:
        return '', offset
    
    if len(data) < offset + length:
        return '', offset
    
    string = data[offset:offset+length].decode('utf-8')
    offset += length
    
    return string, offset

def discover_wsjtx():
    """Listen for WSJT-X HeartBeat and discover listening port"""
    print("\n" + "=" * 60)
    print("DISCOVERING WSJT-X...")
    print("=" * 60)
    print("\nListening for WSJT-X HeartBeat packets...")
    print("(Waiting up to 20 seconds...)")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', 2237))
    sock.settimeout(1.0)
    
    start_time = time.time()
    
    while time.time() - start_time < 20:
        try:
            data, addr = sock.recvfrom(4096)
            source_ip, source_port = addr
            
            # Try to parse as HeartBeat
            if len(data) < 12:
                continue
            
            magic, schema, msg_type = struct.unpack('>III', data[0:12])
            
            if magic == MAGIC and msg_type == MSG_HEARTBEAT:
                wsjtx_id, _ = decode_qstring(data, 12)
                
                print(f"\n✓ FOUND WSJT-X!")
                print(f"  Instance ID: '{wsjtx_id}'")
                print(f"  Listening on port: {source_port}")
                print(f"  Broadcasts from IP: {source_ip}")
                
                sock.close()
                return wsjtx_id, source_port
        
        except socket.timeout:
            print(".", end="", flush=True)
            continue
    
    sock.close()
    print("\n\n✗ TIMEOUT - No WSJT-X HeartBeat received")
    return None, None

def send_location(wsjtx_id, grid, port):
    """Send LocationChange message to WSJT-X"""
    message = struct.pack('>I', MAGIC)
    message += struct.pack('>I', SCHEMA)
    message += struct.pack('>I', MSG_LOCATION)
    message += encode_qstring(wsjtx_id)
    message += encode_qstring(grid)
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(message, ('127.0.0.1', port))
    sock.close()
    
    print(f"\n  Sent: ID='{wsjtx_id}', Grid='{grid}' → 127.0.0.1:{port}")

if __name__ == '__main__':
    print("=" * 60)
    print("WSJT-X AUTOGRID TEST TOOL")
    print("=" * 60)
    print("\nREQUIREMENTS:")
    print("  1. WSJT-X must be RUNNING")
    print("  2. Settings → Reporting → Accept UDP requests: CHECKED")
    print("  3. Settings → General → Autogrid: CHECKED")
    print("\nWATCH: Settings → General → My Grid field")
    print("       (Should update when test succeeds)")
    
    # Discover WSJT-X
    wsjtx_id, listen_port = discover_wsjtx()
    
    if not listen_port:
        print("\nTROUBLESHOOTING:")
        print("  - Is WSJT-X actually running?")
        print("  - Check Settings → Reporting → UDP Server settings")
        print("  - Try setting UDP Server to 127.0.0.1 or 224.0.0.1")
        print("  - Make sure 'Enable' is checked in Reporting tab")
        sys.exit(1)
    
    # Get test grid
    print("\n" + "=" * 60)
    print("TESTING GRID UPDATE")
    print("=" * 60)
    
    current_grid = input("\nWhat grid is CURRENTLY in WSJT-X 'My Grid'? (e.g., EM15): ").strip().upper()
    
    if len(current_grid) == 4:
        test_grids = ["EM14", "EM16", "EM25", "DM79"]
        test_grids = [g for g in test_grids if g != current_grid]
        test_grid = test_grids[0]
    else:
        test_grid = "EM15"
    
    print(f"\nWill try to change grid from '{current_grid}' to '{test_grid}'")
    
    # Test plain grid
    print(f"\n{'=' * 60}")
    print("TEST 1: Plain grid format")
    print(f"{'=' * 60}")
    send_location(wsjtx_id, test_grid, listen_port)
    
    response = input(f"\nDid WSJT-X 'My Grid' change to '{test_grid}'? (yes/no): ").strip().lower()
    
    if response == 'yes':
        print(f"\n✓ SUCCESS with plain grid format!")
        sys.exit(0)
    
    # Test GRID: prefix
    print(f"\n{'=' * 60}")
    print("TEST 2: GRID: prefix format")
    print(f"{'=' * 60}")
    send_location(wsjtx_id, f"GRID:{test_grid}", listen_port)
    
    response = input(f"\nDid WSJT-X 'My Grid' change to '{test_grid}'? (yes/no): ").strip().lower()
    
    if response == 'yes':
        print(f"\n✓ SUCCESS with GRID: prefix format!")
        sys.exit(0)
    
    print("\n" + "=" * 60)
    print("❌ TESTS FAILED")
    print("=" * 60)
    print("\nPossible issues:")
    print("  1. Autogrid NOT enabled (Settings → General)")
    print("  2. Accept UDP requests NOT checked (Settings → Reporting)")
    print("  3. WSJT-X version incompatibility")
    print()
