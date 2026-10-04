# Sentinel

Map of Swedish police and military aircraft from adsb.lol globe_history archives (ODbL).

## Layout
- `scripts/process_day.py`: stdlib-only Python. Streams a daily ~3.8 GB release, writes compact JSON per day plus `index.json`.
- `web/`: static Leaflet app, no build step. Data comes from `DATA_BASE` in `web/config.js`.
- `.github/workflows/update-data.yml`: daily job and manual backfill. Pushes to the `data` branch.
- `vercel.json`: serves `web/` as a static site.

## Rules
- Never download a full day's archive without asking me first (3.8 GB, 1-3 minutes).
- Data is committed to the `data` branch by the workflow, never to main. The one sample day in `web/data` is the exception.
- Keep the adsb.lol / ODbL attribution in the footer.
- Keep the military look: dark, green phosphor, Rajdhani + Share Tech Mono. Colours: police #4cc9ff, Swedish military #ffb020, foreign military #ff6b5e, accent #3dffa8.
- Police = Swedish hex block (4A8000-4AFFFF) + registration `SE-JP*`. Bbox, sampling and rules are constants at the top of `process_day.py`.
- The two tar parts of a day must be read as ONE stream (a file can straddle the split). Do not process them separately.

## Known gaps
- Front end checked in a real browser on 2026-10-03 with the sample day: layout (desktop and 375px phone), fonts, data loading, heat layer, filters and contact selection work. CARTO tiles stopped working without an API key, so the basemap is now Esri World Dark Gray (keyless legacy endpoint, may also go key-only; Stadia Maps is the fallback).
- Time windows covering more than one day (7D/30D) have not been seen; the sample has one day.
- The GitHub workflow has never run on GitHub.
- Call signs are not in the archive; aircraft are identified by registration.
- ADS-B only shows broadcasting aircraft. Military coverage is patchy by nature.

## Commands
- Process one day: `python3 scripts/process_day.py --out web/data --start 2026-10-02 --end 2026-10-02`
- Rebuild the index: `python3 scripts/process_day.py --out web/data --index-only`
- Run the tests: `python3 -m unittest discover -s tests -v`
- Serve the map: `cd web && python3 -m http.server 8000`
