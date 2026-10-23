from sqlalchemy.engine import make_url

from trip_feasibility.config import get_settings


def test_app_points_at_the_test_database(test_database):
    assert get_settings().database_url == test_database
    assert make_url(test_database).database.endswith("_test")
