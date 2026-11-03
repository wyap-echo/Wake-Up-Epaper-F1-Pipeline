# Wake-Up E-paper F1 Pipeline

Private test repository for collecting Formula 1 schedule, race metadata, and
completed podium results from the official Formula 1 website.

The repository does not contain or require a firmware API key. GitHub Actions
runs on a schedule, validates the extracted data, and commits a stable snapshot
only when meaningful F1 data changes.

## Outputs

- `data/f1-2026.json`: current-season snapshot.
- `data/f1-2027.json`: next-season snapshot once the season page is available.
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

## Workflows

### F1 data extraction

`.github/workflows/f1-snapshot.yml` runs daily and can also be started manually.
It:

1. Runs parser unit tests.
2. Refreshes the current official season page and every race page.
3. Attempts to refresh the next-season page without failing early in the year
   when that page is not published.
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
marks may have separate licensing restrictions. This repository is private and
is intended for pipeline reliability testing.

