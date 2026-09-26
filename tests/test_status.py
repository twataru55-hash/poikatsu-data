"""表示状態は現在時刻から計算する。"""
from common import campaign_status, parse_dt

NOW = parse_dt("2026-10-05T12:00:00+09:00")


def c(start, end, override=None):
    return {"period": {"start": start, "end": end}, "status_override": override}


def test_upcoming():
    assert campaign_status(c("2026-10-06T00:00:00+09:00", "2026-10-31T23:59:59+09:00"), NOW) == "upcoming"


def test_active():
    assert campaign_status(c("2026-10-01T00:00:00+09:00", "2026-10-31T23:59:59+09:00"), NOW) == "active"


def test_ending_soon():
    assert campaign_status(c("2026-10-01T00:00:00+09:00", "2026-10-07T23:59:59+09:00"), NOW) == "ending_soon"


def test_ended():
    assert campaign_status(c("2026-10-01T00:00:00+09:00", "2026-10-05T11:59:59+09:00"), NOW) == "ended"


def test_override_ended():
    assert campaign_status(c("2026-10-01T00:00:00+09:00", "2026-10-31T23:59:59+09:00", "ended"), NOW) == "ended"
