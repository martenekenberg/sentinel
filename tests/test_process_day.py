"""Unit tests for scripts/process_day.py. Standard library only.

Run from the project root:  python3 -m unittest discover -s tests -v
"""
import io
import json
import pathlib
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import process_day as pd  # noqa: E402

DAY_START = 1790899200        # 2026-10-02 00:00:00 UTC
STHLM = (59.33, 18.07)        # well inside the box
REAL_POPEN = pd.subprocess.Popen


def head(**fields):
    """Bytes that look like the start of a trace_full file."""
    return json.dumps({"icao": "x", **fields}).encode()


def pt(t, lat=STHLM[0], lon=STHLM[1], alt=1000, flags=0):
    """A readsb trace point: [dt, lat, lon, alt, gs, track, flags]."""
    return [t, lat, lon, alt, 120.0, 90.0, flags]


# --- classify ------------------------------------------------------------------
class ClassifyTest(unittest.TestCase):
    def test_police_by_registration(self):
        cat, meta = pd.classify("4a81f2", head(r="SE-JPA", t="AW69", dbFlags=0))
        self.assertEqual(cat, "police")
        self.assertEqual(meta, {"hex": "4a81f2", "reg": "SE-JPA", "type": "AW69",
                                "desc": None, "op": None, "cat": "police"})

    def test_police_by_owner(self):
        cat, _ = pd.classify("4a81f2", head(r="SE-XYZ", ownOp="Polismyndigheten"))
        self.assertEqual(cat, "police")

    def test_police_wins_over_military_flag(self):
        cat, _ = pd.classify("4a81f2", head(r="SE-JPB", dbFlags=1))
        self.assertEqual(cat, "police")

    def test_police_registration_outside_swedish_block_is_ignored(self):
        self.assertEqual(pd.classify("3c1234", head(r="SE-JPA")), (None, None))

    def test_swedish_military(self):
        cat, meta = pd.classify("4a8000", head(r="39001", dbFlags=1, desc="SAAB JAS 39"))
        self.assertEqual(cat, "swe_mil")
        self.assertEqual(meta["desc"], "SAAB JAS 39")

    def test_foreign_military(self):
        cat, _ = pd.classify("ae1234", head(r="12-3456", dbFlags=1))
        self.assertEqual(cat, "foreign_mil")

    def test_military_is_bit_one_of_dbflags(self):
        self.assertEqual(pd.classify("ae1234", head(dbFlags=2))[0], None)
        self.assertEqual(pd.classify("ae1234", head(dbFlags=3))[0], "foreign_mil")

    def test_swedish_hex_block_edges(self):
        mil = head(dbFlags=1)
        self.assertEqual(pd.classify("4a7fff", mil)[0], "foreign_mil")
        self.assertEqual(pd.classify("4a8000", mil)[0], "swe_mil")
        self.assertEqual(pd.classify("4affff", mil)[0], "swe_mil")
        self.assertEqual(pd.classify("4b0000", mil)[0], "foreign_mil")

    def test_civil_aircraft_dropped(self):
        self.assertEqual(pd.classify("4a9abc", head(r="SE-RXA", dbFlags=0)), (None, None))
        self.assertEqual(pd.classify("4a9abc", head(r="SE-RXA")), (None, None))

    def test_non_hex_address_dropped(self):
        self.assertEqual(pd.classify("~2b4c1e", head(dbFlags=1)), (None, None))

    def test_missing_fields_become_none(self):
        _, meta = pd.classify("ae1234", head(dbFlags=1))
        self.assertIsNone(meta["reg"])
        self.assertIsNone(meta["type"])
        self.assertIsNone(meta["op"])

    def test_tolerates_whitespace_after_colon(self):
        raw = b'{"r": "SE-JPC", "dbFlags": 0}'
        self.assertEqual(pd.classify("4a81f2", raw)[0], "police")


# --- build_segments --------------------------------------------------------------
class BuildSegmentsTest(unittest.TestCase):
    def seg(self, trace, base=DAY_START):
        return pd.build_segments(trace, base, DAY_START)

    def test_time_is_seconds_since_day_start(self):
        segs = self.seg([pt(0), pt(20)], base=DAY_START + 3600)
        self.assertEqual([p[0] for p in segs[0]], [3600, 3620])

    def test_point_shape_and_rounding(self):
        segs = self.seg([pt(0, 59.123456, 18.987654, 2500), pt(20)])
        self.assertEqual(segs[0][0], [0, 59.1235, 18.9877, 2500])

    def test_altitude_variants(self):
        segs = self.seg([pt(0, alt="ground"), pt(20, alt=1234.7), pt(40, alt=None)])
        self.assertEqual([p[3] for p in segs[0]], [0, 1234, None])

    def test_short_points_without_altitude(self):
        segs = self.seg([[0, *STHLM], [20, *STHLM]])
        self.assertEqual([p[3] for p in segs[0]], [None, None])

    def test_points_without_position_are_skipped(self):
        segs = self.seg([pt(0), [10, None, None, 1000], [15], pt(20)])
        self.assertEqual([p[0] for p in segs[0]], [0, 20])

    def test_sampling_keeps_one_point_per_min_step(self):
        segs = self.seg([pt(t) for t in range(0, 31)])     # one point per second
        self.assertEqual([p[0] for p in segs[0]], [0, 10, 20, 30])

    def test_gap_starts_new_segment(self):
        gap = pd.LEG_GAP_S
        segs = self.seg([pt(0), pt(20), pt(20 + gap + 1), pt(40 + gap + 1)])
        self.assertEqual(len(segs), 2)

    def test_gap_exactly_at_limit_does_not_split(self):
        segs = self.seg([pt(0), pt(pd.LEG_GAP_S)])
        self.assertEqual(len(segs), 1)

    def test_new_leg_flag_starts_new_segment(self):
        segs = self.seg([pt(0), pt(20), pt(40, flags=2), pt(60)])
        self.assertEqual([[p[0] for p in s] for s in segs], [[0, 20], [40, 60]])

    def test_box_exit_splits_and_drops_outside_points(self):
        outside = (52.5, 13.4)                               # Berlin
        segs = self.seg([pt(0), pt(20), pt(40, *outside), pt(60, *outside), pt(80), pt(100)])
        self.assertEqual([[p[0] for p in s] for s in segs], [[0, 20], [80, 100]])

    def test_box_edges_are_inclusive(self):
        lat0, lat1, lon0, lon1 = pd.BBOX
        segs = self.seg([pt(0, lat0, lon0), pt(20, lat1, lon1)])
        self.assertEqual(len(segs[0]), 2)

    def test_single_point_segments_are_dropped(self):
        outside = (52.5, 13.4)
        self.assertEqual(self.seg([pt(0), pt(20, *outside), pt(40)]), [])

    def test_empty_trace(self):
        self.assertEqual(self.seg([]), [])

    def test_flag_value_must_be_int(self):
        segs = self.seg([pt(0), pt(20, flags="2")])
        self.assertEqual(len(segs), 1)


# --- release_urls ----------------------------------------------------------------
class ReleaseUrlsTest(unittest.TestCase):
    HTML = """
      <a href="/adsblol/globe_history_2026/releases/download/v2026.10.02-planes-readsb-prod-0/v2026.10.02-planes-readsb-prod-0.tar.ab">b</a>
      <a href="/adsblol/globe_history_2026/releases/download/v2026.10.02-planes-readsb-prod-0/v2026.10.02-planes-readsb-prod-0.tar.aa">a</a>
      <a href="/adsblol/globe_history_2026/releases/download/v2026.10.02-planes-readsb-prod-0/v2026.10.02-planes-readsb-prod-0.tar.aa">dup</a>
      <a href="/adsblol/globe_history_2026/archive/refs/tags/v2026.10.02-planes-readsb-prod-0.tar.gz">source</a>
    """

    def test_parts_found_sorted_and_deduplicated(self):
        resp = mock.Mock()
        resp.read.return_value = self.HTML.encode()
        with mock.patch.object(pd.urllib.request, "urlopen", return_value=resp) as uo:
            urls = pd.release_urls(pd.dt.date(2026, 10, 2))
        self.assertIn("globe_history_2026/releases/expanded_assets/v2026.10.02-planes-readsb-prod-0",
                      uo.call_args[0][0].full_url)
        self.assertEqual([u.rsplit(".", 1)[1] for u in urls], ["aa", "ab"])
        self.assertTrue(all(u.startswith("https://github.com/adsblol/") for u in urls))

    def test_missing_release_returns_empty(self):
        with mock.patch.object(pd.urllib.request, "urlopen", side_effect=OSError("404")), \
             mock.patch.object(pd, "log"):
            self.assertEqual(pd.release_urls(pd.dt.date(2026, 10, 2)), [])


# --- Chain -----------------------------------------------------------------------
@unittest.skipUnless(shutil.which("curl"), "needs curl")
class ChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_file_straddling_parts_is_read_whole(self):
        payload = json.dumps({"trace": list(range(5000))}).encode()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("traces/f2/trace_full_4a81f2.json")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        data = buf.getvalue()
        cut = 512 + len(payload) // 2                        # middle of the file body
        parts = [self.tmp / "x.tar.aa", self.tmp / "x.tar.ab"]
        parts[0].write_bytes(data[:cut])
        parts[1].write_bytes(data[cut:])

        chain = pd.Chain([p.as_uri() for p in parts])
        self.addCleanup(chain.close)
        with mock.patch.object(pd, "log"), tarfile.open(fileobj=chain, mode="r|") as tf:
            members = [(m.name, tf.extractfile(m).read()) for m in tf if m.isfile()]
        self.assertEqual(members, [("traces/f2/trace_full_4a81f2.json", payload)])

    def test_close_stops_running_download(self):
        part = self.tmp / "x.tar.aa"
        part.write_bytes(b"\0" * (4 << 20))
        with mock.patch.object(pd, "log"):
            chain = pd.Chain([part.as_uri(), part.as_uri()])
            chain.read(1024)
            proc = chain.proc
            chain.close()
        self.assertIsNotNone(proc.returncode)
        self.assertTrue(proc.stdout.closed)
        self.assertEqual(chain.read(), b"")             # remaining parts dropped

    def test_curl_failure_raises(self):
        quiet = mock.patch.object(pd.subprocess, "Popen",
                                  side_effect=lambda *a, **k: REAL_POPEN(*a, stderr=pd.subprocess.DEVNULL, **k))
        with mock.patch.object(pd, "log"), quiet:
            chain = pd.Chain([(self.tmp / "missing.tar.aa").as_uri()])
            with self.assertRaises(RuntimeError):
                chain.read()


# --- update_index ----------------------------------------------------------------
class UpdateIndexTest(unittest.TestCase):
    def setUp(self):
        self.out = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out)

    def write_day(self, date, cats):
        doc = {"date": date, "aircraft": [{"cat": c} for c in cats]}
        (self.out / f"{date}.json").write_text(json.dumps(doc))

    def test_counts_per_category_sorted_by_date(self):
        self.write_day("2026-10-02", ["police", "foreign_mil", "foreign_mil"])
        self.write_day("2026-10-01", [])
        with mock.patch.object(pd, "log"):
            pd.update_index(self.out)
        idx = json.loads((self.out / "index.json").read_text())
        self.assertEqual([d["date"] for d in idx["days"]], ["2026-10-01", "2026-10-02"])
        day = idx["days"][1]
        self.assertEqual((day["police"], day["swe_mil"], day["foreign_mil"]), (1, 0, 2))
        self.assertGreater(day["bytes"], 0)

    def test_skips_unreadable_and_unrelated_files(self):
        self.write_day("2026-10-02", ["police"])
        (self.out / "2026-10-03.json").write_text("{not json")
        (self.out / "notes.json").write_text("{}")
        with mock.patch.object(pd, "log"):
            pd.update_index(self.out)
        idx = json.loads((self.out / "index.json").read_text())
        self.assertEqual([d["date"] for d in idx["days"]], ["2026-10-02"])


if __name__ == "__main__":
    unittest.main()
