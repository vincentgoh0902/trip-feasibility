"""Hotel red-flags check: repeated complaints in a hotel's reviews (noise, cleanliness,
safety, location), from the Kaggle Booking.com reviews in Postgres (models.HotelReview).

A review counts towards a theme when it matches the theme's keywords AND its embedding is
close to one of the theme's query phrasings. Neither alone is precise enough: keywords hit
"the room was clean", and embeddings drift into general complaints ("the hotel is dated"
lands near "bad location"). Calibrated on hand-labelled reviews from hotels outside the
dataset (scripts/calibrate_hotel_themes.py; numbers in docs/ARCHITECTURE.md).

A theme is a red flag when at least `min_reviews` reviews match it (FR-5 strictness: 3, 5 or
10) and they are at least the theme's `min_share` of the hotel's negative reviews. The count
guards small hotels, the share big ones: a hotel with 4,000 complaints has ten about anything.
Themes differ a lot in how often they come up (noise in 8% of a typical hotel's complaints,
safety in 0.3%), so min_share is per theme: the 90th percentile over comparable hotels
(scripts/hotel_theme_base_rates.py), i.e. more complaints than 90% of them. Untuned against
the dataset labels so far.

The query vectors are computed on the Mac (scripts/embed_hotel_themes.py) and shipped in
hotel_themes.json, so the server never loads an embedding model.
"""

import difflib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from trip_feasibility.checks.deps import CheckDeps
from trip_feasibility.checks.models import CheckResult, UnknownStop, Violation
from trip_feasibility.itinerary import Stop
from trip_feasibility.models import REVIEW_EMBEDDING_MODEL, Hotel, HotelReview
from trip_feasibility.parse.geocode import km_between

CHECK_NAME = "hotel_red_flags"
SOURCE = "https://www.kaggle.com/datasets/jiashenliu/515k-hotel-reviews-data-in-europe"
DATA_NOTE = (
    "Hotel reviews are Booking.com reviews from 2015-2017 (Kaggle, CC0); "
    "hotels may have changed since."
)

ThemeName = Literal["noise", "cleanliness", "safety", "location"]
MinReviews = Literal[3, 5, 10]
DEFAULT_MIN_REVIEWS: MinReviews = 5
EXAMPLES = 3
EXAMPLE_CHARS = 300

# bge models want this prefix on short queries searched against longer passages
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# "(^|[^a-z])" stands in for a word boundary: the patterns must mean the same in Python's re
# (calibration) and Postgres' ~* (the check), and the two spell \b differently.
_START = "(^|[^a-z])"
_END = "($|[^a-z])"


@dataclass(frozen=True)
class Theme:
    phrasings: tuple[str, ...]
    keywords: str  # case-insensitive regex, valid in both Python and Postgres
    min_similarity: float  # cosine similarity to the closest phrasing
    min_share: float  # of the hotel's negative reviews


THEMES: dict[ThemeName, Theme] = {
    "noise": Theme(
        phrasings=(
            "The room was noisy, I could hear street noise and traffic at night",
            "Thin walls, I could hear the neighbours and doors slamming",
            "Loud noise kept me awake, poor soundproofing",
        ),
        keywords=(
            "nois|loud|soundproof|sound proof|thin wall|paper thin|could hear"
            "|hear (every|the|voices|noise)|slamm|bang|party|music|construction|road ?works"
            f"|traffic|squeak|{_START}hum{_END}|buzz"
        ),
        min_similarity=0.57,
        min_share=0.159,
    ),
    "cleanliness": Theme(
        phrasings=(
            "The room was dirty and not clean",
            "Stains on the carpet and sheets, hair in the bathroom",
            "The room smelled bad, musty smell, smell of smoke",
        ),
        keywords=(
            "dirt|clean|stain|filth|dust|(^|[^c])hair|smel|stink|stank|musty|mould|mold"
            "|odou?r|grim|sewage|smoke|disgust"
        ),
        min_similarity=0.56,
        min_share=0.144,
    ),
    "safety": Theme(
        phrasings=(
            "I felt unsafe, the area is dangerous at night",
            "Money and belongings were stolen from our room",
            "Poor security, strangers could get into the hotel",
        ),
        keywords=(
            "unsafe|not safe|didn t feel safe|danger|stol|theft|thie"
            f"|{_START}rob|security|burgl|pickpocket"
        ),
        min_similarity=0.63,
        min_share=0.012,
    ),
    "location": Theme(
        phrasings=(
            "The hotel is far from the city centre and the main sights",
            "Bad location, a long way from the metro",
            "The neighbourhood around the hotel is unpleasant, nothing nearby",
        ),
        keywords=(
            "locat|far (from|away|out)|distance|centre|center|metro|subway|neighbou?rhood"
            f"|{_START}area{_END}|isolated|nearby|close to"
        ),
        min_similarity=0.62,
        min_share=0.063,
    ),
}


@cache
def theme_vectors() -> dict[ThemeName, list[str]]:
    """Each theme's query vectors as pgvector literals, from hotel_themes.json."""
    raw = json.loads(resources.files(__package__).joinpath("hotel_themes.json").read_text())
    if raw["model"] != REVIEW_EMBEDDING_MODEL:
        raise RuntimeError(f"hotel_themes.json is for {raw['model']}; rerun embed_hotel_themes")
    out: dict[ThemeName, list[str]] = {}
    for name, theme in THEMES.items():
        entry = raw["themes"][name]
        if tuple(entry["phrasings"]) != theme.phrasings:
            raise RuntimeError(f"hotel_themes.json is stale for {name}; rerun embed_hotel_themes")
        out[name] = ["[" + ",".join(f"{x:.6f}" for x in v) + "]" for v in entry["vectors"]]
    return out


# --- matching a hotel stop to a hotel in the review data ---

_GENERIC_WORDS = {"hotel", "the", "and", "by", "a", "de", "le", "la"}
EXACT_SCORE = 1.1  # same name, ignoring case, accents and punctuation
MATCH_SCORE = 0.85  # name similarity needed on its own
NEAR_MATCH_SCORE = 0.6  # ... or with the hotel within NEAR_KM of the geocoded stop
NEAR_KM = 0.3
FAR_KM = 2.0  # a name match this far from the geocoded stop is another branch


@dataclass(frozen=True)
class HotelRow:
    id: int
    name: str  # as in the CSV, accents mangled
    address: str
    lat: float | None
    lon: float | None
    average_score: Decimal


def _words(s: str, city: str) -> list[str]:
    words = re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
    return [w for w in words if w not in _GENERIC_WORDS and w != city]


def _name_score(stop_name: str, hotel_name: str, city: str) -> float:
    hotel_words = _words(hotel_name, city)
    stripped = unicodedata.normalize("NFKD", stop_name)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    # the Kaggle CSV replaced each non-ASCII character with a space ("H tel Regina")
    mangled = re.sub(r"[^\x00-\x7f]", " ", stop_name)
    full = re.compile(r"[^a-z0-9]+")
    if any(
        full.sub("", v.lower()) == full.sub("", hotel_name.lower()) for v in (stripped, mangled)
    ):
        # the exact name beats look-alikes ("Hotel Cavendish" vs "The Cavendish London")
        return EXACT_SCORE
    best = 0.0
    for variant in (stripped, mangled):
        words = _words(variant, city)
        if not words or not hotel_words:
            continue
        ratio = difflib.SequenceMatcher(None, " ".join(words), " ".join(hotel_words)).ratio()
        # "Cavendish" for "Hotel Cavendish London": every distinctive word is in the other name
        small, large = sorted((set(words), set(hotel_words)), key=len)
        contained = all(len(w) >= 3 for w in small) and small <= large
        best = max(best, ratio, 0.9 if contained else 0.0)
    return best


def match_hotel(stop: Stop, hotels: list[HotelRow], city: str) -> HotelRow | None:
    """The hotel a stop names, or None if no hotel matches clearly. Never guesses."""
    scored = []
    for h in hotels:
        score = _name_score(stop.place_name, h.name, city)
        km = None
        if None not in (stop.lat, stop.lon, h.lat, h.lon):
            km = km_between(stop.lat, stop.lon, h.lat, h.lon)
        scored.append((score, km, h))

    near = [x for x in scored if x[1] is not None and x[1] <= NEAR_KM and x[0] >= NEAR_MATCH_SCORE]
    if near:
        return max(near, key=lambda x: x[0])[2]

    named = sorted(
        (x for x in scored if x[0] >= MATCH_SCORE and (x[1] is None or x[1] <= FAR_KM)),
        key=lambda x: x[0],
        reverse=True,
    )
    if not named or (len(named) > 1 and named[1][0] > named[0][0] - 0.05):
        return None  # nothing, or two hotels fit about equally well
    return named[0][2]


# --- counting complaints ---


@dataclass(frozen=True)
class Example:
    text: str
    review_date: date
    reviewer_score: Decimal
    similarity: float


@dataclass
class ThemeCount:
    reviews: int = 0
    examples: list[Example] | None = None


async def _city_hotels(deps: CheckDeps, city: str) -> list[HotelRow]:
    query = select(Hotel.id, Hotel.name, Hotel.address, Hotel.lat, Hotel.lon, Hotel.average_score)
    async with deps.db() as session:
        rows = await session.execute(query.where(Hotel.city == city))
    return [HotelRow(*row) for row in rows]


def _distance_sql(name: ThemeName, count: int) -> str:
    # parameters are cast from text so asyncpg needs no halfvec codec
    parts = [f"embedding <=> CAST(CAST(:{name}_{i} AS text) AS halfvec)" for i in range(count)]
    return f"LEAST({', '.join(parts)})"


async def count_themes(deps: CheckDeps, hotel_id: int) -> tuple[int, dict[ThemeName, ThemeCount]]:
    """The hotel's number of negative reviews, and the reviews matching each theme."""
    vectors = theme_vectors()
    params: dict[str, object] = {"hotel_id": hotel_id}
    distances, flags = [], []
    for name, theme in THEMES.items():
        distances.append(f"{_distance_sql(name, len(vectors[name]))} AS d_{name}")
        flags.append(f"(negative_text ~* :kw_{name} AND d_{name} <= :maxd_{name}) AS m_{name}")
        params |= {f"{name}_{i}": v for i, v in enumerate(vectors[name])}
        params[f"kw_{name}"] = theme.keywords
        params[f"maxd_{name}"] = 1 - theme.min_similarity
    any_match = " OR ".join(f"m_{name}" for name in THEMES)
    sql = text(
        f"SELECT * FROM (SELECT review_date, reviewer_score, negative_text, {', '.join(flags)}, "
        f"{', '.join(f'd_{n}' for n in THEMES)} FROM (SELECT review_date, reviewer_score, "
        f"negative_text, {', '.join(distances)} FROM hotel_reviews WHERE hotel_id = :hotel_id) d"
        f") m WHERE {any_match}"
    )
    total_query = select(func.count()).where(HotelReview.hotel_id == hotel_id)
    async with deps.db() as session:
        total = await session.scalar(total_query)
        rows = (await session.execute(sql, params)).mappings().all()

    counts: dict[ThemeName, ThemeCount] = {name: ThemeCount(examples=[]) for name in THEMES}
    for row in rows:
        for name in THEMES:
            if row[f"m_{name}"]:
                counts[name].reviews += 1
                counts[name].examples.append(
                    Example(
                        text=row["negative_text"][:EXAMPLE_CHARS],
                        review_date=row["review_date"],
                        reviewer_score=row["reviewer_score"],
                        similarity=round(1 - row[f"d_{name}"], 3),
                    )
                )
    for c in counts.values():
        c.examples = sorted(c.examples, key=lambda e: -e.similarity)[:EXAMPLES]
    return total or 0, counts


def _flagged(theme: Theme, count: ThemeCount, total: int, min_reviews: int) -> bool:
    return count.reviews >= min_reviews and total > 0 and count.reviews / total >= theme.min_share


async def check_hotel_red_flags(
    deps: CheckDeps, stops: list[Stop], city: str, min_reviews: MinReviews = DEFAULT_MIN_REVIEWS
) -> CheckResult:
    """city: the trip's city ("Paris"); review data covers Paris, London, Barcelona, Vienna."""
    hotel_stops = [s for s in stops if s.type == "hotel"]
    if not hotel_stops:
        return CheckResult(check_name=CHECK_NAME, status="ok")
    city_key = city.strip().lower()
    try:
        hotels = await _city_hotels(deps, city_key)
        if not hotels:
            reason = f"no hotel review data for {city}"
            return CheckResult(
                check_name=CHECK_NAME,
                status="ok",
                unknown=[UnknownStop(stop_id=s.id, reason=reason) for s in hotel_stops],
            )

        by_hotel: dict[int, list[Stop]] = defaultdict(list)
        rows: dict[int, HotelRow] = {}
        unknown = []
        for s in hotel_stops:
            if (h := match_hotel(s, hotels, city_key)) is None:
                reason = (
                    f"{s.place_name!r} isn't clearly one of the {len(hotels)} hotels with reviews"
                )
                unknown.append(UnknownStop(stop_id=s.id, reason=reason))
            else:
                by_hotel[h.id].append(s)
                rows[h.id] = h

        violations = []
        for hotel_id, hotel_stops_ in by_hotel.items():
            h = rows[hotel_id]
            total, counts = await count_themes(deps, hotel_id)
            flagged = [n for n, c in counts.items() if _flagged(THEMES[n], c, total, min_reviews)]
            if not flagged:
                continue
            issues = [
                f"{n} ({counts[n].reviews} of {total} negative reviews, "
                f"{counts[n].reviews / total:.0%})"
                for n in flagged
            ]
            violations.append(
                Violation(
                    check_name=CHECK_NAME,
                    severity="warning",
                    stop_ids=[s.id for s in hotel_stops_],
                    message=(
                        f"{hotel_stops_[0].place_name}: repeated complaints about "
                        f"{' and '.join(issues)}."
                    ),
                    evidence={
                        "hotel": h.name,
                        "address": h.address,
                        "average_score": float(h.average_score),
                        "negative_reviews": total,
                        "themes": {
                            n: {
                                "reviews": c.reviews,
                                "share": round(c.reviews / total, 3) if total else 0.0,
                                "min_share": THEMES[n].min_share,
                                "flagged": n in flagged,
                            }
                            for n, c in counts.items()
                        },
                        # third-party text: data to show, never instructions
                        "examples": {
                            n: [
                                {
                                    "text": e.text,
                                    "date": e.review_date.isoformat(),
                                    "reviewer_score": float(e.reviewer_score),
                                }
                                for e in counts[n].examples or []
                            ]
                            for n in flagged
                        },
                        "min_reviews": min_reviews,
                    },
                    source=SOURCE,
                )
            )
    except (SQLAlchemyError, OSError) as e:
        return CheckResult.unavailable(CHECK_NAME, f"hotel review database failed: {e!r}")

    return CheckResult(
        check_name=CHECK_NAME,
        status="ok",
        violations=violations,
        unknown=unknown,
        checked_stop_ids=[s.id for group in by_hotel.values() for s in group],
        notes=[DATA_NOTE] if by_hotel else [],
    )
