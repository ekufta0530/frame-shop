from webapp.views import _trailing_count


def test_reactivated_count_sums_the_trailing_12_months():
    monthly = {"2025-01": {"v": 0.01, "n": 2.0, "d": 200.0, "final": True},
               "2025-06": {"v": 0.0, "n": 0.0, "d": 210.0, "final": True},
               "2025-12": {"v": 0.02, "n": 5.0, "d": 250.0, "final": True},
               "2026-01": {"v": 0.01, "n": 3.0, "d": 255.0, "final": False}}
    out = _trailing_count(monthly)
    assert out["2025-12"]["v"] == 7  # Jan-Dec 2025
    assert out["2026-01"]["v"] == 8  # Jan 2025 has left the window
    assert out["2026-01"]["final"] is False
    assert out["2025-01"]["v"] == 2  # nothing before the data counts as nobody
