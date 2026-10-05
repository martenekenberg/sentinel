# Sentinel

A tactical-style map of where Swedish police and military aircraft (and foreign
military aircraft over the Nordic and Baltic countries) have flown, built from open ADS-B data.

```
adsb.lol daily archives  ->  GitHub Actions (filter)  ->  `data` branch  ->  static map on Vercel
```

* **Data source:** [adsb.lol](https://github.com/adsblol) `globe_history_<year>` releases, one per day, about 3.8 GB each.
  Licensed ODbL: keep the attribution in the footer.
* **Processor:** `scripts/process_day.py` streams a day's archive, keeps only the aircraft we care about
  and writes a small file (about 300 KB per day).
* **Front end:** `web/` is plain HTML/CSS/JS with Leaflet. No build step.

## What gets kept

| Channel          | Rule                                                                  |
| ---------------- | --------------------------------------------------------------------- |
| Police           | Swedish hex block (`4A8000`-`4AFFFF`) and registration `SE-JP*` (or owner contains "polis") |
| Swedish military | Swedish hex block and flagged military in adsb.lol's aircraft database |
| Foreign military | Flagged military, any other country, with positions inside the stored area |

Only positions inside the box 53.8-71.3 N, 4.0-31.7 E are stored. It covers Sweden, Norway, Finland, Denmark,
Estonia, Latvia and Lithuania (and with them parts of Russia, Belarus, Poland and Germany). At most one
point per 20 seconds is kept. Edit the constants at the top of `scripts/process_day.py` to change the box,
the sampling interval or the rules; days processed before a change keep the old area until reprocessed.

### Limits to know about

* ADS-B only shows aircraft that broadcast. Fighters and many military flights switch it off, and some
  hide behind privacy programmes. Expect good police coverage and patchy military coverage.
* The military flag comes from adsb.lol's database and will miss some aircraft.
* Police call signs are not stored in the archive; aircraft are identified by registration.

## Run it locally

```bash
# process one day (takes 1-3 minutes, downloads about 3.8 GB, needs curl)
python3 scripts/process_day.py --out web/data --start 2026-10-02 --end 2026-10-02

# serve the map
cd web && python3 -m http.server 8000     # open http://localhost:8000
```

`web/data/` already contains the real output for 2026-10-02 so the map works straight away.
Run the processor's unit tests: `python3 -m unittest discover -s tests -v`.
Rebuild the day index only: `python3 scripts/process_day.py --out web/data --index-only`.

## Deploy

1. **Create a public GitHub repo** and push this project to `main`.
2. **Allow the workflow to push:** the workflow asks for `contents: write` itself, which is enough on a personal repo.
   If the push step fails (for example under an organisation policy), set Settings > Actions > General >
   Workflow permissions > *Read and write*.
3. **Backfill:** Actions > *Update data* > *Run workflow*, with a start and end date
   (for example the last 30 days). Each day takes a few minutes on a GitHub runner, so large ranges are
   fine but long. The workflow commits after every day, so nothing is lost if it is interrupted.
   It creates the `data` branch on first run.
4. **Daily updates** run by themselves at 06:30 UTC and process the last two days (existing days are skipped).
5. **Connect Vercel:** import the repo. `vercel.json` already points the output directory at `web/`;
   no build command is needed.
6. **Point the map at the data:** in `web/config.js` set

   ```js
   DATA_BASE: "https://raw.githubusercontent.com/<owner>/<repo>/data"
   ```

   and push. (Commits to the `data` branch will trigger Vercel preview builds; turn those off under
   Project Settings > Git > Ignored Build Step if they bother you.)

## Notes

* GitHub's scheduler is best-effort, so a day can occasionally be late. The two-day window heals gaps.
* Scheduled workflows on a repo with no activity for 60 days get paused. Re-enable them if that happens.
* The basemap is Esri's World Dark Gray Base from the keyless `server.arcgisonline.com` endpoint (CARTO's dark
  tiles now need an API key). Esri may put that endpoint behind a key too; Stadia Maps with domain-based access
  is the planned fallback. Check Esri's terms before using this commercially.
* Data size: roughly 150 MB per year on the `data` branch (about 375 KB per day).

## Layout

```
scripts/process_day.py          download, filter, write daily JSON + index
tests/                          unit tests for the processor (stdlib unittest)
.github/workflows/update-data.yml   daily job and manual backfill
web/                            Leaflet map (index.html, app.js, style.css, config.js, data/)
vercel.json                     serve web/ as a static site
```
