# N1MM+ ROVERQTH Automatic Update

## Overview
The Co-Pilot now includes automatic ROVERQTH command sending to N1MM+ via keyboard automation.

## How It Works

### The Button
At the bottom of the Co-Pilot window, you'll see a button that updates automatically:
```
[Send to N1MM+: EM16]
```

The grid shown will always match your current GPS-derived grid square.

### Using the Feature

**Step 1: Click the Button**
When you want to update N1MM+'s ROVERQTH field, click the "Send to N1MM+" button.

**Step 2: Read the Dialog**
A dialog will appear:
```
Will send: ROVERQTH EM16

Click OK, then quickly click in the N1MM+ callsign box.
You have 3 seconds after clicking OK.
```

**Step 3: Click OK**
Click OK to start the countdown.

**Step 4: Click N1MM+ (3 seconds)**
Quickly click in the **N1MM+ Entry Window callsign box**. You have 3 seconds.

**Step 5: Automatic Entry**
The Co-Pilot will automatically:
1. Type: `ROVERQTH`
2. Press Enter (opens N1MM+ dialog)
3. Wait 0.5 seconds
4. Type: `EM16` in the dialog
5. Press Enter (triggers confirmation)
6. Wait 0.5 seconds
7. Press Enter again (confirms 'Yes' - default button)
8. Announce via voice: "N1MM updated to EM16"

### What Happens in N1MM+
After the command is sent:
1. ROVERQTH command typed in the callsign box
2. N1MM+ dialog appears: "Enter Rover QTH"
3. Grid is automatically filled: `EM16`
4. Confirmation dialog appears (defaults to 'Yes')
5. 'Yes' is automatically confirmed
6. ROVERQTH updated complete!

## Testing Without Driving

### Test Mode Tab
1. Go to the **Test Mode** tab
2. Enter a grid in the "Test Grid" field (e.g., `EM01`)
3. Click **"Send to WSJT-X"**
4. The N1MM+ button will update to show the new grid
5. Click **"Send to N1MM+"** to test the keyboard automation

### Test Sequence Example
```
Test Mode:
1. Enter: EM01 → Click "Send to WSJT-X"
2. Button updates: [Send to N1MM+: EM01]
3. Click button → Dialog appears
4. Click OK → Click N1MM+ callsign box
5. Watch Step 1: ROVERQTH <ENTER>
6. N1MM+ dialog appears automatically
7. Watch Step 2: EM01 <ENTER>
8. Confirmation dialog appears
9. Watch Step 3: <ENTER> (confirms Yes)
10. ROVERQTH updated!
```

## Installation Requirements

**New Requirement: pyautogui**
```bash
pip install pyautogui
```

Or install all requirements:
```bash
cd "C:\N5ZY CoPilot\n5zy-copilot"
pip install -r requirements.txt
```

## Tips & Tricks

### Timing
- The 3-second delay gives you time to switch windows
- If you miss the timing, just click the button again
- No penalty for trying multiple times

### Multiple Monitors
- Works perfectly with N1MM+ on a different monitor
- Just make sure you can click the N1MM+ callsign box quickly

### During Contest Operations
**Workflow:**
1. Drive to new grid
2. Co-Pilot announces: "Grid change. Entering EM16"
3. WSJT-X automatically updates (you see this in TX6)
4. When convenient, click "Send to N1MM+" button
5. Quick click in N1MM+ callsign box (3 seconds)
6. Watch: ROVERQTH typed, dialog opens
7. Watch: EM16 filled in, confirmation appears
8. Watch: Yes confirmed automatically
9. ROVERQTH updated - done!

### Voice Confirmation
Listen for the voice announcement:
- **"N1MM updated to EM16"** = Success!

## Troubleshooting

### "Nothing happened"
- Make sure you clicked in the N1MM+ **callsign box** (not any other field)
- Try again with more time before the 3-second countdown expires

### "Typed in wrong window"
- Click the button again
- This time, be ready to click N1MM+ callsign box immediately after clicking OK

### "Can't click fast enough"
**Option:** Modify the delay in `copilot.py`:
```python
# Line ~394: Change from 3 to 5 seconds
time.sleep(5)  # Give more time
```

### "Button says ----"
- GPS hasn't acquired position yet
- Check Alerts tab for GPS status
- Wait for "GPS acquired" message

## Why Not Fully Automatic?

We considered automatically sending ROVERQTH on every grid change, but decided against it because:
1. **You control timing** - Update when it's safe, not mid-QSO
2. **No interruptions** - Won't steal focus during critical operations
3. **Verification** - You see exactly what's being sent
4. **Flexibility** - Skip updates if you're just passing through a grid

## Future Enhancements

Possible future additions:
- Automatic send on grid change (with safety delays)
- Keyboard shortcut to trigger (e.g., Ctrl+Shift+N)
- Copy to clipboard option (for manual paste)
- Multiple N1MM+ instances support

## Current Status

### ✅ Working
- GPS → WSJT-X grid updates (automatic)
- WSJT-X → N1MM+ QSO logging (automatic)
- GPS → N1MM+ ROVERQTH (manual button)

### 📋 Manual
- N1MM+ ROVERQTH updates (by design)
- Gives you control over timing

## Summary

**WSJT-X**: Fully automatic grid updates ✅  
**N1MM+**: One-click semi-automatic updates ✅

Best of both worlds: automation where it helps, control where you need it!

---
73, AI Co-Pilot
Generated: 2026-01-11
