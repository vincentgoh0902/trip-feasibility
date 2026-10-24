import json
import re
from datetime import date
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.factories import stop
from trip_feasibility.checks.deps import make_check_deps
from trip_feasibility.checks.hotel_red_flags import (
    THEMES,
    HotelRow,
    check_hotel_red_flags,
    match_hotel,
    theme_vectors,
)
from trip_feasibility.config import get_settings

DAY = date(2026, 7, 1)
VECTORS = {n: [np.array(json.loads(v)) for v in vs] for n, vs in theme_vectors().items()}
# far from every theme phrasing
NEUTRAL = -sum(v for vs in VECTORS.values() for v in vs)
NEUTRAL /= np.linalg.norm(NEUTRAL)


def hotel_stop(stop_id, name, at=None):
    return stop(stop_id, DAY, type="hotel", osm=None, name=name, at=at)


def row(hotel_id, name, at=(None, None)):
    return HotelRow(hotel_id, name, f"{name} address", at[0], at[1], Decimal("8.0"))


# --- matching a stop to a hotel ---

HOTELS = [
    row(1, "H tel Regina", (48.8635, 2.3325)),
    row(2, "Hotel Cavendish", (51.5076, -0.1374)),
    row(3, "Mercure Paris Terminus Nord", (48.8803, 2.3554)),
    row(4, "Ibis Paris Gare du Nord", (48.8810, 2.3560)),
    row(5, "Ibis Paris Bastille", (48.8530, 2.3700)),
    row(6, "The Cavendish London", (51.5080, -0.1390)),
]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Hôtel Regina", 1),  # the CSV mangles accents into spaces
        ("Mercure Terminus Nord", 3),
        ("Mercure Paris Terminus Nord Hotel", 3),
        ("Ibis", None),  # two hotels fit equally well
        ("Hotel Lutetia", None),
    ],
)
def test_match_by_name(name, expected):
    h = match_hotel(hotel_stop("h", name), HOTELS, "paris")
    assert (h.id if h else None) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Hotel Cavendish", 2),  # the exact name beats "The Cavendish London"
        ("hotel cavendish", 2),
        ("The Cavendish London", 6),
        ("Cavendish", None),  # either hotel
        ("The Cavendish", None),
    ],
)
def test_exact_name_beats_look_alikes(name, expected):
    h = match_hotel(hotel_stop("h", name), HOTELS, "london")
    assert (h.id if h else None) == expected


def test_coordinates_settle_a_weak_name_match():
    s = hotel_stop("h", "Ibis Hotel", at=(48.8531, 2.3701))
    assert match_hotel(s, HOTELS, "paris").id == 5


def test_name_match_far_from_the_geocoded_stop_is_rejected():
    s = hotel_stop("h", "Hotel Cavendish", at=(51.40, -0.30))  # ~17 km away
    assert match_hotel(s, HOTELS, "london") is None


# --- counting complaints in the database ---


async def _add_hotel(session, hotel_id, name, city, reviews):
    await session.execute(
        text(
            "INSERT INTO hotels (id, name, address, city, lat, lon, average_score, total_reviews) "
            "VALUES (:id, :name, 'addr', :city, NULL, NULL, 7.5, :n)"
        ),
        {"id": hotel_id, "name": name, "city": city, "n": len(reviews)},
    )
    for body, vector in reviews:
        await session.execute(
            text(
                "INSERT INTO hotel_reviews (hotel_id, review_date, reviewer_score, negative_text,"
                " embedding) VALUES (:h, '2016-05-01', 6.3, :t, CAST(CAST(:e AS text) AS halfvec))"
            ),
            {"h": hotel_id, "t": body, "e": "[" + ",".join(f"{x:.6f}" for x in vector) + "]"},
        )


def reviews(noise=0, keyword_only=0, neutral=0):
    return (
        [
            (f"Very noisy room number {i}, traffic all night", VECTORS["noise"][0])
            for i in range(noise)
        ]
        # mentions noise, but the embedding is about something else
        + [("The noise of the coffee machine was the only sound", NEUTRAL)] * keyword_only
        + [("Breakfast was expensive", NEUTRAL)] * neutral
    )


@pytest.fixture
async def hotels(check_deps):
    async with check_deps.db() as session:
        await session.execute(text("TRUNCATE hotels, hotel_reviews RESTART IDENTITY CASCADE"))
        await _add_hotel(session, 1, "Hotel Cavendish", "london", reviews(6, 2, 22))
        await _add_hotel(session, 2, "Big Hotel", "london", reviews(6, 0, 94))
        await session.commit()
    yield
    async with check_deps.db() as session:
        await session.execute(text("TRUNCATE hotels, hotel_reviews RESTART IDENTITY CASCADE"))
        await session.commit()


async def test_repeated_complaints_are_a_warning(check_deps, hotels):
    stops = [
        hotel_stop("h1", "Hotel Cavendish"),
        stop("v", DAY, name="British Museum"),
        hotel_stop("h2", "Back to Hotel Cavendish"),
    ]
    result = await check_hotel_red_flags(check_deps, stops, "London")
    assert result.status == "ok"
    assert result.checked_stop_ids == ["h1", "h2"]
    [v] = result.violations
    assert v.severity == "warning"
    assert v.stop_ids == ["h1", "h2"]
    assert "noise (6 of 30 negative reviews, 20%)" in v.message
    noise = v.evidence["themes"]["noise"]
    assert (noise["reviews"], noise["share"], noise["flagged"]) == (6, 0.2, True)
    assert v.evidence["themes"]["cleanliness"]["reviews"] == 0
    assert len(v.evidence["examples"]["noise"]) == 3
    assert result.notes  # the data is old


async def test_stricter_setting_needs_more_reviews(check_deps, hotels):
    stops = [hotel_stop("h", "Hotel Cavendish")]
    result = await check_hotel_red_flags(check_deps, stops, "london", min_reviews=10)
    assert result.violations == []
    assert result.checked_stop_ids == ["h"]


async def test_a_few_complaints_at_a_big_hotel_are_not_a_flag(check_deps, hotels):
    result = await check_hotel_red_flags(check_deps, [hotel_stop("h", "Big Hotel")], "london")
    assert result.violations == []  # 6 of 100 is under noise's min_share


async def test_unmatched_hotel_and_unknown_city_are_unknown(check_deps, hotels):
    result = await check_hotel_red_flags(check_deps, [hotel_stop("h", "The Savoy")], "london")
    assert [u.stop_id for u in result.unknown] == ["h"]
    assert result.violations == [] and result.checked_stop_ids == []

    result = await check_hotel_red_flags(check_deps, [hotel_stop("h", "Hotel Arts")], "Lisbon")
    assert "no hotel review data for Lisbon" in result.unknown[0].reason


async def test_no_hotel_stops(check_deps):
    result = await check_hotel_red_flags(check_deps, [stop("v", DAY)], "london")
    assert result.status == "ok" and result.checked_stop_ids == []


async def test_database_down_is_unavailable(check_deps):
    engine = create_async_engine("postgresql+asyncpg://x:y@127.0.0.1:1/nope")
    deps = make_check_deps(check_deps.http, async_sessionmaker(engine), get_settings())
    result = await check_hotel_red_flags(deps, [hotel_stop("h", "Hotel Cavendish")], "london")
    assert result.status == "unavailable"
    await engine.dispose()


SAMPLES = [
    "Very noisy at night",
    "Could hear everything",
    "The room was humming",
    "human error",
    "Dirty carpet",
    "Hairs in the shower",
    "The chair was broken",
    "Our bags were stolen",
    "We were robbed",
    "Problems with the wifi",
    "Wardrobe too small",
    "Bad area at night",
    "Far from the centre",
    "The bathroom area",
    "Squeaky bed",
    "Security deposit",
]


async def test_keywords_mean_the_same_in_python_and_postgres(check_deps):
    async with check_deps.db() as session:
        for theme in THEMES.values():
            for sample in SAMPLES:
                in_postgres = await session.scalar(
                    text("SELECT CAST(:s AS text) ~* CAST(:p AS text)"),
                    {"s": sample, "p": theme.keywords},
                )
                assert in_postgres == bool(re.search(theme.keywords, sample, re.IGNORECASE)), (
                    sample,
                    theme.keywords,
                )
