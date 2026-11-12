# Wake-Up E-paper F1 Pipeline

Public data pipeline for collecting Formula 1 schedules, session results, and
complete driver classifications from the official Formula 1 website.

The repository does not contain or require a firmware API key. GitHub Actions
validates the extracted data and commits a new snapshot only when meaningful F1
data changes.

## Outputs

- `data/f1-2026.json`: current-season snapshot.
- `data/f1-2027.json`: next-season snapshot once the season page is available.
- `dist/f1-YYYY.f1p`: F1P3 binary pack for the firmware.
- `dist/f1-manifest.json`: current/next pack sizes, hashes, format version and
  full-result metadata.
- Workflow artifact: complete collector output, including source hashes and
  optional circuit assets.

Each race includes country, local and Beijing race times, circuit metadata,
every scheduled session, and the complete result tables published by Formula 1.
An individual driver row has this shape:

```json
{
  "position": 1,
  "number": "63",
  "name": "George Russell",
  "code": "RUS",
  "team": "Mercedes",
  "time": "1:23:06.801",
  "laps": "58",
  "points": "25"
}
```

Qualifying rows may additionally contain `q1`, `q2`, and `q3` times. Upcoming
sessions keep empty `results` and `result_complete: false`.

## Compact F1P3 pack

`tools/build_f1_pack.py` creates an F1P3 pack without embedded circuit maps. It
keeps the `F1P2` magic for compatibility and contains:

- Race and meeting epoch times.
- Country, Beijing time, circuit, length, laps and distance.
- Historical fastest lap information.
- Three race podium entries with code, name and result.
- Every completed practice, sprint-qualifying, sprint, qualifying, and race
  classification entry.
- A zlib-compressed result section after the fixed race records.
- Header size, record size, record count, compressed result size, CRC32 and
  generation epoch.

The current 2026 pack is about 25 KB including more than 2,000 driver result
rows. A calendar-only pack remains under 10 KB.

## Workflows

### F1 session watcher

`.github/workflows/f1-snapshot.yml` checks hourly but does not contact Formula 1
outside a result window:

- It reads the session end time from the published calendar.
- Collection begins 15 minutes after the scheduled session end.
- It retries hourly for up to six hours to cover delayed starts and stoppages.
- A final retry is made around 24 hours after the session.
- A session is skipped after its complete driver table is stored.

Manual runs can force a current-season refresh when recovering from a failed
history run.

The workflow:

1. Runs parser unit tests.
2. Reads the existing snapshot and exits without an official F1 request while
   no session is due.
3. Collects the official season page, the results overview, race pages, and
   newly published result tables.
4. Reuses already complete session result tables instead of downloading the
   entire season again.
5. Validates rounds, session times, driver positions, names and result
   completeness.
6. Builds the snapshot, F1P3 pack, and device manifest.
7. Uploads the complete extraction and commits only meaningful changes.

### Next-season calendar

`.github/workflows/f1-next-season.yml` checks on the first day of each month. If
`data/f1-<next-year>.json` already exists and contains the expected season, the
workflow exits before contacting Formula 1. The next automatic attempt starts
only after the target year rolls forward.

A manual `force_next` input remains available for recovering a damaged file.

### Monthly keepalive

`.github/workflows/monthly-keepalive.yml` updates
`activity/last-heartbeat.txt` on the first day of each month so GitHub does not
disable scheduled workflows after a long inactive period.

The official F1 pages are not a documented public API. HTML changes can break
extraction; workflows fail rather than publishing fabricated data.

## Manual run

```powershell
python tools/f1_collector.py --year 2026 --existing data/f1-2026.json --refresh
python tools/f1_snapshot.py `
  artifacts/current/season.json `
  data/f1-2026.json `
  --expected-year 2026
python tools/build_f1_pack.py `
  data/f1-2026.json `
  dist/f1-2026.f1p `
  --expected-year 2026
```

## Source and licensing

Data is normalized from official Formula 1 web pages. Circuit images and F1
marks may have separate licensing restrictions.
