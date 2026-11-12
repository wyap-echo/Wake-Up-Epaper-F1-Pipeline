# Wake-Up E-paper F1 Pipeline

Public data pipeline for collecting Formula 1 schedule, race metadata, and
completed podium results from the official Formula 1 website.

The repository does not contain or require a firmware API key. GitHub Actions
runs on a schedule, validates the extracted data, and commits a stable snapshot
only when meaningful F1 data changes.

## Outputs

- `data/f1-2026.json`: current-season snapshot.
- `data/f1-2027.json`: next-season snapshot once the season page is available.
- `dist/f1-YYYY.f1p`: compact binary pack for the firmware.
- `dist/f1-manifest.json`: current/next pack sizes, SHA-256 values, and format
  metadata.
- Workflow artifact: complete collector output, including source hashes and
  optional circuit assets.

Each race includes country, local and Beijing race time, circuit metadata, and
an optional podium:

```json
{
  "position": 1,
  "name": "Russell",
  "code": "RUS",
  "result": "1:23:06.801"
}
```

Upcoming races keep `"podium": null`.

## Compact F1P2 pack

`tools/build_f1_pack.py` creates an `F1P2` pack without embedded circuit maps.
The current UI does not display those maps. The pack contains:

- Race and meeting epoch times.
- Country, Beijing time, circuit, length, laps and distance.
- Historical fastest lap information.
- Three podium entries with code, name and result.
- Header size, record size, record count, payload length, CRC32 and generation
  epoch.

The 2027 calendar pack is about 8.8 KB rather than the previous 130 KB map-heavy
pack. It is built only from validated stable snapshots.

## Workflows

### F1 data extraction

`.github/workflows/f1-snapshot.yml` uses a race-aware schedule:

- Friday through Sunday at 09:17, 15:17, and 21:17 Beijing time, when practice,
  qualifying, and race information can change.
- Monday at 13:17 Beijing time for a final result catch-up.
- The first day of each month at 13:17 Beijing time for the next-season
  calendar.

Manual runs refresh the current season by default. Enable `collect_next` to
also refresh the next-season calendar.

The workflow:

1. Runs parser unit tests.
2. Refreshes the current official season page and every race page.
3. Refreshes the next-season page only on the monthly schedule or when
   explicitly requested in a manual run.
4. Validates rounds, countries, race times, and podium records.
5. Uploads the complete extraction as a workflow artifact.
6. Commits stable snapshots only when meaningful content changes.

The official F1 pages are not a documented public API. HTML changes can break
extraction; the workflow intentionally fails instead of publishing partial or
fabricated race data.

### Monthly keepalive

`.github/workflows/monthly-keepalive.yml` updates
`activity/last-heartbeat.txt` on the first day of each month. This keeps the
repository active so GitHub does not disable scheduled workflows after a long
period without repository activity.

## Manual run

```powershell
python tools/f1_collector.py --year 2026 --refresh
python tools/f1_snapshot.py `
  artifacts/current/season.json `
  data/f1-2026.json `
  --expected-year 2026
```

## Source and licensing

Data is normalized from official Formula 1 web pages. Circuit images and F1
marks may have separate licensing restrictions.

