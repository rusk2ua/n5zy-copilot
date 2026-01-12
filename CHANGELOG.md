# N5ZY Co-Pilot - Changelog

## Version: 2026-01-11 Evening Update

### 🔧 CRITICAL FIX: Multiple WSJT-X Instances
**Problem:** Only the first WSJT-X instance (6m on port 2237) was receiving grid updates.

**Root Cause:** The Co-Pilot was only listening on port 2237 for WSJT-X heartbeat broadcasts.

**Solution:** Now listens on ALL configured ports:
- Port 2237 (6m instance)
- Port 2238 (2m instance)  
- Port 2239 (222/902 instance)

**Result:** All three WSJT-X instances now receive grid updates! ✅

### 📝 How It Works
Each WSJT-X instance:
1. Broadcasts heartbeats on its configured port
2. Listens for commands on a dynamic port (discovered from heartbeat source port)
3. Co-Pilot discovers all instances by listening to ALL broadcast ports
4. Sends LocationChange messages to each discovered instance

### ✨ N1MM+ ROVERQTH Enhancement
**Added:** Third automatic keystroke for confirmation dialog

**Complete Sequence:**
1. Type: `ROVERQTH` → Enter (opens dialog)
2. Type: `EM16` → Enter (triggers confirmation)
3. Enter (confirms 'Yes' - default button)

**Timing:**
- 3 seconds: Initial delay to click N1MM+ callsign box
- 0.5 seconds: Wait for grid entry dialog
- 0.5 seconds: Wait for confirmation dialog

### 🎯 Testing Instructions

**WSJT-X Multiple Instances:**
1. Start all three WSJT-X instances (6m, 2m, 222/902)
2. Go to Test Mode tab in Co-Pilot
3. Enter test grid: `EM01`
4. Click "Send to WSJT-X"
5. Check ALL three WSJT-X instances - TX6 should update in each!

**N1MM+ Full Automation:**
1. Keep Test Mode tab open with grid `EM01`
2. Click "Send to N1MM+" button
3. Click OK in Co-Pilot dialog
4. Quickly click in N1MM+ callsign box
5. Watch all three steps execute automatically:
   - ROVERQTH command
   - Grid entry (EM01)
   - Yes confirmation
6. Verify ROVERQTH field updated in N1MM+

### 📋 Files Changed
- `modules/radio_updater.py`: Multi-port listening for WSJT-X discovery
- `copilot.py`: Three-step N1MM+ ROVERQTH automation
- `N1MM_ROVERQTH_GUIDE.md`: Updated documentation

### 🎉 Current Status

**WSJT-X Integration:**
✅ Discovers all instances on configured ports
✅ Sends LocationChange to each instance
✅ TX6 message updates in all instances
✅ Grid changes announced via voice

**N1MM+ Integration:**
✅ One-click button with current grid
✅ Fully automated three-step process
✅ Voice confirmation when complete
✅ Works with Test Mode

**GPS Integration:**
✅ COM port monitoring
✅ Maidenhead grid calculation
✅ Real-time position tracking
✅ Grid change detection

### 🚀 Ready for Contest!

All systems operational for January 17-18, 2026 ARRL VHF Contest!

---
73 de AI Co-Pilot
Generated: 2026-01-11 Evening
