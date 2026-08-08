"""Regression test for a real bug found running MLBCollector.collect_pybaseball
against live data during the Checkpoint 4 takeover: pybaseball's statcast()
returns pandas.Timestamp columns, and RawStore.write()'s json.dumps had no
`default=` handler, so it raised TypeError on every real payload. That
TypeError was swallowed by the collector's broad except-and-report-degraded
handler, so real, successfully-fetched Statcast data was silently discarded
every time — the source health table recorded 'Object of type Timestamp is
not JSON serializable' with the collector reporting status: "no_data" and no
visible error to a caller not inspecting source health.

See outputs/rebuild/takeover_status.md Checkpoint 4.
"""

from __future__ import annotations

from model_prediction.rebuild import RawStore


class FakeTimestamp:
    """Stands in for pandas.Timestamp without adding a pandas import here —
    duck-typed the same way _json_default() detects it: has .isoformat()."""

    def isoformat(self) -> str:
        return "2026-08-04T18:05:00"


class FakeNumpyScalar:
    """Stands in for numpy.int64/float64 — duck-typed via .item()."""

    def __init__(self, value: object) -> None:
        self._value = value

    def item(self) -> object:
        return self._value


class TestRawStoreJSONDefault:
    def test_write_accepts_pandas_timestamp_like_values(self, tmp_path):
        store = RawStore(tmp_path)
        payload = [{"game_date": FakeTimestamp(), "pitch_number": 1}]

        ref = store.write("pybaseball", "2026-08-04", "statcast_2026-08-04", payload)

        read_back = store.read(ref)
        assert read_back[0]["game_date"] == "2026-08-04T18:05:00"

    def test_write_accepts_numpy_scalar_like_values(self, tmp_path):
        store = RawStore(tmp_path)
        payload = [{"release_speed": FakeNumpyScalar(94.3), "zone": FakeNumpyScalar(5)}]

        ref = store.write("pybaseball", "2026-08-04", "statcast_2026-08-04", payload)

        read_back = store.read(ref)
        assert read_back[0]["release_speed"] == 94.3
        assert read_back[0]["zone"] == 5

    def test_write_still_rejects_genuinely_unserializable_objects(self, tmp_path):
        """The fix must not silently swallow real serialization bugs —
        only handle the known datetime-like/numpy-scalar-like shapes."""
        store = RawStore(tmp_path)

        class NotSerializable:
            pass

        try:
            store.write("pybaseball", "2026-08-04", "bad", {"x": NotSerializable()})
            raised = False
        except TypeError:
            raised = True
        assert raised, "genuinely unserializable objects must still raise, not be silently coerced"
