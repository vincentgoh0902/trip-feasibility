"""The dev/test split and its validation.

Vienna is held out of the dev set entirely (decided 2026-10-24), so the test set reports
results for seen cities (Paris, London, Barcelona) and one unseen city (Vienna) separately.
Synthetic variants (from the injection script) record their source in `parent_id` and always
sit in the same split as it, so no variant of a test itinerary can leak into dev.

The full annotation format (models.DatasetItinerary) extends `DatasetEntry`.
"""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

City = Literal["paris", "london", "barcelona", "vienna"]
Split = Literal["dev", "test"]

UNSEEN_CITY: City = "vienna"
# (split, city) -> number of original itineraries; variants are not counted
SPLIT_PLAN: dict[tuple[Split, City], int] = {
    ("dev", "paris"): 5,
    ("dev", "london"): 5,
    ("dev", "barcelona"): 5,
    ("test", "paris"): 5,
    ("test", "london"): 5,
    ("test", "barcelona"): 5,
    ("test", "vienna"): 10,
}
SPLIT_TOTALS: dict[Split, int] = {"dev": 15, "test": 25}


class DatasetEntry(BaseModel):
    id: str = Field(min_length=1, max_length=50)
    city: City
    split: Split
    # set on synthetic variants: the id of the original itinerary they were made from
    parent_id: str | None = None


def split_errors(entries: list[DatasetEntry], *, complete: bool = True) -> list[str]:
    """Everything wrong with the dev/test split; an empty list means it's valid.

    complete=False is for a dataset still being collected: counts may fall short of the
    plan, but not exceed it.
    """
    errors = []
    by_id: dict[str, DatasetEntry] = {}
    for e in entries:
        if e.id in by_id:
            errors.append(f"{e.id}: duplicate id")
        by_id[e.id] = e

    def count_error(name: str, n: int, expected: int) -> None:
        if n > expected or (complete and n < expected):
            errors.append(f"{name}: {n} itineraries, expected {expected}")

    originals = [e for e in entries if e.parent_id is None]
    totals = Counter(e.split for e in originals)
    for split, expected in SPLIT_TOTALS.items():
        count_error(split, totals[split], expected)
    per_city = Counter((e.split, e.city) for e in originals)
    for key in sorted(set(SPLIT_PLAN) | set(per_city)):
        count_error(f"{key[0]}/{key[1]}", per_city[key], SPLIT_PLAN.get(key, 0))

    for e in entries:
        if e.city == UNSEEN_CITY and e.split != "test":
            errors.append(f"{e.id}: {UNSEEN_CITY} is held out, so it must be in test")
        if e.parent_id is None:
            continue
        parent = by_id.get(e.parent_id)
        if parent is None:
            errors.append(f"{e.id}: parent {e.parent_id} not found")
        elif parent.parent_id is not None:
            errors.append(f"{e.id}: parent {e.parent_id} is itself a variant")
        else:
            if e.split != parent.split:
                errors.append(
                    f"{e.id}: in {e.split}, but its parent {parent.id} is in {parent.split}"
                )
            if e.city != parent.city:
                errors.append(f"{e.id}: city {e.city}, but its parent {parent.id} is {parent.city}")
    return errors
