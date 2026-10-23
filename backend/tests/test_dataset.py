from trip_feasibility.dataset import SPLIT_PLAN, DatasetEntry, split_errors


def planned() -> list[DatasetEntry]:
    """Original itineraries exactly as SPLIT_PLAN lays them out."""
    return [
        DatasetEntry(id=f"{split}-{city}-{i}", city=city, split=split)
        for (split, city), n in SPLIT_PLAN.items()
        for i in range(n)
    ]


def test_planned_split_is_valid():
    entries = planned()
    assert len(entries) == 40
    assert split_errors(entries) == []


def test_variants_in_their_parents_split_are_valid():
    entries = planned() + [
        DatasetEntry(id="v1", city="paris", split="dev", parent_id="dev-paris-0"),
        DatasetEntry(id="v2", city="vienna", split="test", parent_id="test-vienna-0"),
    ]
    assert split_errors(entries) == []


def test_wrong_totals():
    entries = planned()[1:]  # one dev itinerary short
    assert split_errors(entries) == [
        "dev: 14 itineraries, expected 15",
        "dev/paris: 4 itineraries, expected 5",
    ]


def test_city_moved_between_splits():
    entries = planned()
    moved = next(e for e in entries if e.id == "test-paris-0")
    moved.split = "dev"
    errors = split_errors(entries)
    assert "dev: 16 itineraries, expected 15" in errors
    assert "test/paris: 4 itineraries, expected 5" in errors


def test_vienna_in_dev():
    entries = planned()
    entries[-1] = DatasetEntry(id="test-vienna-9", city="vienna", split="dev")
    errors = split_errors(entries)
    assert "test-vienna-9: vienna is held out, so it must be in test" in errors
    assert "dev/vienna: 1 itineraries, expected 0" in errors


def test_vienna_variant_in_dev():
    entries = planned() + [
        DatasetEntry(id="v1", city="vienna", split="dev", parent_id="test-vienna-0")
    ]
    assert split_errors(entries) == [
        "v1: vienna is held out, so it must be in test",
        "v1: in dev, but its parent test-vienna-0 is in test",
    ]


def test_variant_in_other_split_than_parent():
    entries = planned() + [
        DatasetEntry(id="v1", city="paris", split="test", parent_id="dev-paris-0")
    ]
    assert split_errors(entries) == ["v1: in test, but its parent dev-paris-0 is in dev"]


def test_variant_parent_problems():
    entries = planned() + [
        DatasetEntry(id="v1", city="paris", split="dev", parent_id="missing"),
        DatasetEntry(id="v2", city="paris", split="dev", parent_id="dev-paris-0"),
        DatasetEntry(id="v3", city="paris", split="dev", parent_id="v2"),
        DatasetEntry(id="v4", city="london", split="dev", parent_id="dev-paris-1"),
    ]
    assert split_errors(entries) == [
        "v1: parent missing not found",
        "v3: parent v2 is itself a variant",
        "v4: city london, but its parent dev-paris-1 is paris",
    ]


def test_variants_do_not_count_towards_totals():
    entries = planned() + [
        DatasetEntry(id=f"v{i}", city="london", split="test", parent_id="test-london-0")
        for i in range(5)
    ]
    assert split_errors(entries) == []


def test_duplicate_id():
    entries = planned()
    entries.append(entries[0].model_copy())
    assert "dev-paris-0: duplicate id" in split_errors(entries)
