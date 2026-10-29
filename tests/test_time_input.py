"""Civil-hour validation must not reject valid minute/second fields."""

from datetime import timezone

import pytest

from backend.calculations.sun_earth import parse_utc_time


@pytest.mark.parametrize(
    "value",
    ["2026-10-04T19:24:13+00:00", "2026-01-05T00:24:00Z", "2026-10-24T00:00:24+02:00"],
)
def test_24_in_other_fields_is_valid(value):
    assert parse_utc_time(value).tzinfo == timezone.utc


@pytest.mark.parametrize("value", ["2026-10-03T24:00:00Z", "20261003T240000Z", None])
def test_invalid_civil_hour_and_non_timestamp_are_validation_errors(value):
    with pytest.raises(ValueError):
        parse_utc_time(value)
