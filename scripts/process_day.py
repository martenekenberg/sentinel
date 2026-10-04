#!/usr/bin/env python3
"""
Sentinel data processor.

Streams one (or many) daily adsb.lol globe_history releases from GitHub,
keeps only police and military aircraft over Sweden, and writes a compact
JSON file per day:  <out>/YYYY-MM-DD.json  plus  <out>/index.json.

Standard library only. Needs `curl` on the PATH.

Source data: adsb.lol contributors, Open Database License (ODbL).
"""
import argparse
import datetime as dt
import gzip
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import time
import urllib.request

# --- tunables ---------------------------------------------------------------
BBOX = (55.0, 69.5, 10.5, 24.5)          # lat_min, lat_max, lon_min, lon_max
SWE_HEX = (0x4A8000, 0x4AFFFF)           # Sweden's ICAO 24-bit address block
POLICE_REG_PREFIX = "SE-JP"              # Swedish Police Authority registrations
POLICE_OWNER = re.compile(r"polis", re.I)
MIN_STEP_S = 10                          # keep at most one point per N seconds
LEG_GAP_S = 300                          # a gap this long starts a new segment
REPO = "adsblol/globe_history_{year}"
TAG = "v{y:04d}.{m:02d}.{d:02d}-planes-readsb-prod-0"

RE_DBFLAGS = re.compile(rb'"dbFlags":\s*(\d+)')
RE_REG = re.compile(rb'"r":\s*"([^"]*)"')
RE_TYPE = re.compile(rb'"t":\s*"([^"]*)"')
RE_DESC = re.compile(rb'"desc":\s*"([^"]*)"')
RE_OWN = re.compile(rb'"ownOp":\s*"([^"]*)"')


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --- locating the release parts ---------------------------------------------
def release_urls(day: dt.date):
    """Return the ordered list of tar part URLs for a day, or [] if absent."""
    repo = REPO.format(year=day.year)
    tag = TAG.format(y=day.year, m=day.month, d=day.day)
    page = f"https://github.com/{repo}/releases/expanded_assets/{tag}"
    try:
        req = urllib.request.Request(page, headers={"User-Agent": "sentinel/1.0"})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as exc:  # 404 = release not published (yet)
        log(f"  no release page for {day}: {exc}")
        return []
    hrefs = sorted(set(re.findall(r'href="(/[^"]+/releases/download/[^"]+\.tar\.[a-z]{2})"', html)))
    return ["https://github.com" + h for h in hrefs]


class Chain:
    """File-like object that concatenates several curl downloads into one
    stream. The split tar parts must be read as ONE archive, because a file
    can straddle the boundary between two parts."""

    def __init__(self, urls):
        self.urls = list(urls)
        self.proc = None

    def _open_next(self):
        if not self.urls:
            return False
        url = self.urls.pop(0)
        log(f"  downloading {url.rsplit('/', 1)[-1]}")
        self.proc = subprocess.Popen(
            ["curl", "-fsSL", "--retry", "5", "--retry-delay", "5", url],
            stdout=subprocess.PIPE,
        )
        return True

    def read(self, n=-1):
        while True:
            if self.proc is None and not self._open_next():
                return b""
            chunk = self.proc.stdout.read(n if n and n > 0 else 1 << 20)
            if chunk:
                return chunk
            self.proc.stdout.close()
            rc = self.proc.wait()
            self.proc = None
            if rc != 0:
                raise RuntimeError(f"curl failed with exit code {rc}")

    def close(self):
        """Stop the current download. tarfile stops reading at the end-of-archive
        marker, so the last curl is usually still running when we are done."""
        self.urls = []
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc.stdout.close()
            self.proc = None


# --- filtering ---------------------------------------------------------------
def _g(rx, b):
    m = rx.search(b)
    return m.group(1).decode("utf-8", "replace") if m else ""


def classify(hexid: str, head: bytes):
    """Cheap pre-filter on the first bytes of a trace file. Returns
    (category, meta) or (None, None) without parsing the whole JSON."""
    try:
        h = int(hexid, 16)
    except ValueError:
        return None, None            # TIS-B / anonymous addresses start with '~'
    swe = SWE_HEX[0] <= h <= SWE_HEX[1]
    m = RE_DBFLAGS.search(head)
    mil = bool(m and int(m.group(1)) & 1)
    reg = _g(RE_REG, head)
    own = _g(RE_OWN, head)
    if swe and (reg.startswith(POLICE_REG_PREFIX) or POLICE_OWNER.search(own)):
        cat = "police"
    elif mil:
        cat = "swe_mil" if swe else "foreign_mil"
    else:
        return None, None
    return cat, {
        "hex": hexid, "reg": reg or None,
        "type": _g(RE_TYPE, head) or None,
        "desc": _g(RE_DESC, head) or None,
        "op": own or None, "cat": cat,
    }


def in_box(lat, lon):
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def build_segments(trace, base_ts, day_start):
    """Turn readsb trace points into compact segments of [t, lat, lon, alt].
    t is seconds since 00:00 UTC of the processed day. Only points inside the
    Sweden box are kept; gaps and box exits start a new segment."""
    segs, cur = [], []
    prev_t = None
    last_kept_t = None
    for p in trace:
        if len(p) < 3 or p[1] is None or p[2] is None:
            continue
        lat, lon = p[1], p[2]
        t = int(base_ts + p[0] - day_start)
        flags = p[6] if len(p) > 6 and isinstance(p[6], int) else 0
        if not in_box(lat, lon):
            if cur:
                segs.append(cur)
                cur = []
            prev_t = None
            continue
        if cur and (prev_t is None or t - prev_t > LEG_GAP_S or flags & 2):
            segs.append(cur)
            cur = []
        alt = p[3] if len(p) > 3 else None
        alt = 0 if alt == "ground" else (int(alt) if isinstance(alt, (int, float)) else None)
        if not cur or t - last_kept_t >= MIN_STEP_S:
            cur.append([t, round(lat, 4), round(lon, 4), alt])
            last_kept_t = t
        prev_t = t
    if cur:
        segs.append(cur)
    return [s for s in segs if len(s) >= 2]


def process_day(day: dt.date, out_dir: pathlib.Path, force=False):
    target = out_dir / f"{day.isoformat()}.json"
    if target.exists() and not force:
        log(f"{day}: already processed, skipping")
        return False
    urls = release_urls(day)
    if not urls:
        log(f"{day}: release not available, skipping")
        return False

    t0 = time.time()
    day_start = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp())
    aircraft, scanned = [], 0
    log(f"{day}: scanning {len(urls)} part(s)")
    chain = Chain(urls)
    try:
        with tarfile.open(fileobj=chain, mode="r|") as tf:
            for member in tf:
                if not member.isfile() or "trace_full_" not in member.name:
                    continue
                scanned += 1
                hexid = member.name.rsplit("trace_full_", 1)[1][:-5]
                raw = tf.extractfile(member).read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                cat, meta = classify(hexid, raw[:800])
                if not cat:
                    continue
                doc = json.loads(raw)
                segs = build_segments(doc.get("trace", []), doc.get("timestamp", day_start), day_start)
                if not segs:
                    continue
                meta["segs"] = segs
                aircraft.append(meta)
                if scanned % 20000 == 0:
                    log(f"  {scanned} files, {len(aircraft)} kept, {int(time.time() - t0)}s")
    finally:
        chain.close()

    aircraft.sort(key=lambda a: (a["cat"], a["reg"] or a["hex"]))
    payload = {
        "date": day.isoformat(),
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "adsb.lol globe_history (ODbL)",
        "bbox": list(BBOX),
        "aircraft": aircraft,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    log(f"{day}: {scanned} files scanned, {len(aircraft)} aircraft kept, "
        f"{target.stat().st_size / 1024:.0f} KB, {int(time.time() - t0)}s")
    return True


def update_index(out_dir: pathlib.Path):
    entries = []
    for f in sorted(out_dir.glob("????-??-??.json")):
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        counts = {"police": 0, "swe_mil": 0, "foreign_mil": 0}
        for a in doc.get("aircraft", []):
            counts[a["cat"]] = counts.get(a["cat"], 0) + 1
        entries.append({"date": doc["date"], "bytes": f.stat().st_size, **counts})
    idx = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "days": entries}
    (out_dir / "index.json").write_text(json.dumps(idx, separators=(",", ":")))
    log(f"index.json: {len(entries)} day(s)")


def git_publish(out_dir: pathlib.Path, message: str):
    def run(*a):
        subprocess.run(["git", "-C", str(out_dir), *a], check=True)
    run("config", "user.name", "sentinel-bot")
    run("config", "user.email", "sentinel-bot@users.noreply.github.com")
    run("add", "-A")
    if subprocess.run(["git", "-C", str(out_dir), "diff", "--cached", "--quiet"]).returncode == 0:
        return
    run("commit", "-m", message)
    run("push", "origin", "HEAD:data")


def daterange(a, b):
    while a <= b:
        yield a
        a += dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data", help="output directory (default: data)")
    ap.add_argument("--start", help="first date, YYYY-MM-DD (default: 2 days ago, UTC)")
    ap.add_argument("--end", help="last date, YYYY-MM-DD (default: yesterday, UTC)")
    ap.add_argument("--force", action="store_true", help="reprocess days that already exist")
    ap.add_argument("--push", action="store_true", help="git commit and push to the data branch after each day")
    ap.add_argument("--index-only", action="store_true", help="only rebuild index.json")
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.index_only:
        update_index(out)
        return

    today = dt.datetime.now(dt.timezone.utc).date()
    end = dt.date.fromisoformat(a.end) if a.end else today - dt.timedelta(days=1)
    start = dt.date.fromisoformat(a.start) if a.start else end - dt.timedelta(days=1)
    for day in daterange(start, end):
        try:
            changed = process_day(day, out, force=a.force)
        except Exception as exc:         # one bad day must not kill a backfill
            log(f"{day}: FAILED: {exc}")
            continue
        if changed:
            update_index(out)
            if a.push:
                git_publish(out, f"data: {day.isoformat()}")


if __name__ == "__main__":
    main()
