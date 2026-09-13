import pytest

from jobbot.config import LocationConfig
from jobbot.location import LocationRules, classify


@pytest.mark.parametrize(
    ("location", "kind"),
    [
        ("Austin, TX", "us"),
        ("Cambridge, MA", "us"),
        ("Louisville, KY", "us"),
        ("NYC", "us"),
        ("SF", "us"),
        ("South SF", "us"),
        ("LA", "us"),
        ("Massachusetts", "us"),
        ("Washington", "us"),
        ("Remote in USA", "remote"),
        ("Remote", "remote"),
        ("Remote in Canada", "remote_non_us"),
        ("Toronto, ON, Canada", "non_us"),
        ("Markham, ON, Canada", "non_us"),
        ("Canada", "non_us"),
        ("London, UK", "non_us"),
        ("Cambridge, UK", "non_us"),
        ("Dublin, Ireland", "non_us"),
        ("Dubai - United Arab Emirates", "non_us"),
        ("Multiple Locations", "unknown"),
    ],
)
def test_classify(location, kind):
    assert classify(location) == kind


def test_us_only_unless_remote(config):
    rules = LocationRules(config.location)
    assert rules.evaluate(["Toronto, ON, Canada"]).allowed is False
    assert rules.evaluate(["Toronto, ON, Canada"]).area == "Outside US"
    assert rules.evaluate(["Remote in Canada"]).allowed is False
    assert rules.evaluate(["Toronto, ON, Canada", "Seattle, WA"]).area == "US"
    remote = rules.evaluate(["Remote in USA"])
    assert remote.allowed and remote.area == "Remote"
    assert rules.evaluate(["Multiple Locations"]).allowed  # unknown is never dropped
    assert LocationRules(LocationConfig(us_only=False)).evaluate(["London, UK"]).allowed


@pytest.mark.parametrize(
    ("locations", "area"),
    [
        (["St. Louis, MO"], "⭐ St. Louis"),
        (["Lake St Louis, MO"], "⭐ St. Louis"),
        (["O'Fallon, MO"], "⭐ St. Louis"),
        (["Chesterfield, MO"], "⭐ St. Louis"),
        (["Seattle, WA", "St. Louis, MO"], "⭐ St. Louis"),
        (["Bozeman, MT"], "⭐ Bozeman"),
        (["Louisville, KY"], "US"),
    ],
)
def test_home_areas(config, locations, area):
    verdict = LocationRules(config.location).evaluate(locations)
    assert verdict.area == area
    assert verdict.bonus == (8 if area.startswith("⭐") else 0)
