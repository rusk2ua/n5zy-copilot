# QSO Logging Relay - Feature Documentation

## Overview

The Co-Pilot now captures QSO Logged messages from **all WSJT-X instances** and:
1. Displays them in the QSO Log tab
2. Writes them to an ADIF file (immediate backup)
3. **Queues and relays to N1MM+ one at a time** (prevents race conditions)
4. Announces new QSOs via voice

This solves TWO problems:
1. N1MM+ only supporting 2 WSJT-X listeners when you run 3+ instances
2. Race conditions when multiple WSJT-X instances log QSOs at the same moment

---

## The Race Condition Problem (Why GridTracker2 Failed)

When you work a station on multiple bands (e.g., 6m, 2m, and 70cm simultaneously), all three WSJT-X instances log QSOs at nearly the **same instant**.

**What happens without queuing:**
```
WSJT-X (6m)     ──┐
                  ├──→ All three arrive at N1MM+ simultaneously
WSJT-X (2m)       │    N1MM+ can only process ONE at a time
                  │    Two QSOs get dropped!
WSJT-X (70cm)  ──┘
```

**GridTracker2's problem**: No buffering - it just forwarded UDP packets as fast as they arrived.

---

## The Solution: Queued Relay

The Co-Pilot uses a **thread-safe queue** with **750ms delays** between sends:

```
WSJT-X (6m)     ──┐                    ┌──→ N1MM+ (QSO 1)
                  ├──→ Co-Pilot Queue ──┤   wait 750ms
WSJT-X (2m)       │                    ├──→ N1MM+ (QSO 2)
                  │                    │   wait 750ms
WSJT-X (70cm)  ──┘                    └──→ N1MM+ (QSO 3)
```

**Result**: ALL QSOs get to N1MM+, even when they arrive simultaneously!

---

## How It Works

### 1. QSOs Arrive
Multiple WSJT-X instances may log QSOs at the same moment.

### 2. Immediate Actions (parallel)
- Write to ADIF file (backup - never lost!)
- Add to UI display
- Voice announcement
- Add to relay queue

### 3. Queued Relay (serial)
A separate thread drains the queue:
- Sends one QSO to N1MM+
- Waits 750ms
- Sends next QSO
- Repeat until queue is empty

---

## Console Output

When multiple QSOs arrive simultaneously:
```
============================================================
QSO LOGGED from WSJT-X - ic7610:
  Call: W1AW
  Band: 6m (50.313000 MHz)
============================================================
Radio Update: QSO written to logs/n5zy_copilot_20260117.adi
Radio Update: QSO queued for N1MM+ relay (queue size: 1)

============================================================
QSO LOGGED from WSJT-X - ic9700:
  Call: W1AW
  Band: 2m (144.174000 MHz)
============================================================
Radio Update: QSO written to logs/n5zy_copilot_20260117.adi
Radio Update: QSO queued for N1MM+ relay (queue size: 2)

============================================================
QSO LOGGED from WSJT-X - ic7300:
  Call: W1AW
  Band: 70cm (432.065000 MHz)
============================================================
Radio Update: QSO written to logs/n5zy_copilot_20260117.adi
Radio Update: QSO queued for N1MM+ relay (queue size: 3)

Radio Update: ✅ Sent QSO to N1MM+ (W1AW on 6m)
... 750ms delay ...
Radio Update: ✅ Sent QSO to N1MM+ (W1AW on 2m)
... 750ms delay ...
Radio Update: ✅ Sent QSO to N1MM+ (W1AW on 70cm)
```

---

## Configuration

### WSJT-X Setup
Each WSJT-X instance must broadcast UDP:

1. **Settings → Reporting → UDP Server**
   - Check "Enable UDP"
   - Port: 2237 (6m), 2238 (2m), 2239 (222/902)
   - Each instance needs a UNIQUE port

### N1MM+ Setup (CRITICAL!)

The Co-Pilot sends QSOs to N1MM+ via **TCP** (not UDP!) using the JTDX protocol.

**In N1MM+:**
1. **Config → Configure Ports, Mode Control, Winkey, etc.**
2. **Click the "WSJT/JTDX Setup" tab**
3. **Configure "JTDX / Other TCP Settings":**
   - IP Address: `127.0.0.1` (or leave default)
   - Port: `52001` (default JTDX port)

**In Co-Pilot Settings tab:**
- TCP Port: `52001` (must match N1MM+)

**Why TCP instead of UDP?**
- N1MM+'s "Broadcast Data" tab is for SENDING data OUT to other apps
- N1MM+'s "WSJT/JTDX Setup" tab is for RECEIVING data IN via TCP
- The Co-Pilot uses the same TCP protocol that JTDX uses

---

## ADIF File Backup

Regardless of N1MM+ relay success, ALL QSOs are saved to:
```
logs/n5zy_copilot_YYYYMMDD.adi
```

### During Contest
QSOs accumulate in this file throughout the contest.

### After Contest (If Needed)
If any QSOs didn't make it to N1MM+:
1. N1MM+: **File → Import → Import ADIF**
2. Select the ADIF file
3. N1MM+ will flag duplicates automatically

---

## Features Summary

| Feature | Status | Notes |
|---------|--------|-------|
| Capture 3+ WSJT-X instances | ✅ Working | Listens on all configured ports |
| Duplicate detection | ✅ Working | By datetime + call + band |
| Display in UI | ✅ Working | Real-time QSO Log tab |
| Write to ADIF | ✅ Working | Immediate, never lost |
| Voice announcements | ✅ Working | "QSO logged. [callsign]" |
| Queued N1MM+ relay | ✅ Working | 750ms between sends |

---

## Troubleshooting

### "QSOs not appearing in N1MM+"

1. **Check N1MM+ is listening**: The contactinfo UDP format may require specific N1MM+ configuration
2. **Use ADIF import**: This is 100% reliable after the contest
3. **Check port**: Ensure N1MM+ and Co-Pilot use the same port

### "Queue keeps growing"

If QSOs queue up faster than they drain:
- This is normal during a massive pileup
- They will all be sent eventually
- ADIF backup is already written

### "Some QSOs missing from N1MM+"

Import the ADIF file - it has everything!

---

## Technical Details

### Queue Implementation
```python
import queue
self.qso_queue = queue.Queue()  # Thread-safe

# Producer (listener threads)
self.qso_queue.put(qso_data)

# Consumer (relay thread)
qso = self.qso_queue.get(timeout=1.0)
self._send_qso_to_n1mm(qso)
time.sleep(0.75)  # 750ms delay
```

### Timing
- **750ms** between N1MM+ sends (configurable)
- Allows N1MM+ time to process each QSO
- Fast enough for typical contest rates

---

Generated: 2026-01-11

