# QSY Advisor - Multi-Band Station Tracking

The QSY Advisor tracks which bands stations have operated in past VHF contests. When you work a station, it checks if they operate on other bands and alerts you to request a QSY.

## How It Works

1. **Station Database** - Contains historical data on which bands stations operate
2. **QSO Tracking** - When you log a QSO, the advisor checks the database
3. **Voice Alert** - If the station has other bands you haven't worked them on, you get a voice alert

### Example

You work K5QE on 2m via FT8. The advisor knows K5QE operates on 6m, 2m, 222, 70cm, 33cm, 23cm, and up. Since you haven't worked them on those other bands in this contest, you hear:

> "QSY opportunity. K5QE also has 6m, 70cm, and 23cm"

This reminds you to call them on SSB and ask if they can QSY to other bands!

## Populating the Database

### Method 1: Import Your Own Logs

The best source is your own past contest logs. After each contest, import your Cabrillo file:

```bash
cd tools
python import_cabrillo.py "C:\Users\Marcus\Documents\N1MM Logger+\ExportFiles\n5zy_janvhf2025.log"
```

This extracts every station you worked and which bands they were on.

### Method 2: Manual Entry

Add stations directly via Python:

```python
from modules.qsy_advisor import QSYAdvisor

advisor = QSYAdvisor()
advisor.add_station('K5QE', ['50', '144', '222', '432', '902', '1296'], 'EM31', 'Jan 2025')
advisor.add_station('W5ZN', ['50', '144', '432', '1296'], 'EM35', 'Sep 2025')
```

### Method 3: Edit JSON Directly

The database is stored in `data/station_bands.json`:

```json
{
  "K5QE": {
    "bands": ["50", "144", "222", "432", "902", "1296"],
    "grids": ["EM31"],
    "last_seen": "2025-01",
    "contests": ["Jan 2025"],
    "notes": "East Texas superstation"
  }
}
```

## Band Codes

| Code | Band |
|------|------|
| 50 | 6m |
| 144 | 2m |
| 222 | 1.25m |
| 432 | 70cm |
| 902 | 33cm |
| 1296 | 23cm |
| 2304 | 13cm |
| 3456 | 9cm |
| 5760 | 6cm |
| 10368 | 3cm |

## Getting Data from 3830scores.com

1. Go to https://www.3830scores.com/contests.php
2. Click "ARRL January VHF Contest" (or June/September)
3. Select a recent year (e.g., "2025")
4. Look for "Band Breakdowns" or individual score pages
5. Note which stations operated multiple bands
6. Add them to your database

## During the Contest

- QSY suggestions only appear for stations **in the database**
- You only get alerted once per station/band combination per contest
- The alert includes up to 3 bands to keep it short
- Use the info to ask: "Do you have 70cm?" or "QSY to 23cm?"

## Tips

1. **Focus on your region** - Stations in EM grids are most useful for you
2. **Big stations matter most** - K5QE, W5LUA etc. are worth tracking
3. **Rovers are valuable** - They often have many bands and move around
4. **Update after each contest** - Import your log to build the database

## Current Database

The starter database includes ~20 known multi-band stations in the TX/OK region. Import your past logs to add more!
