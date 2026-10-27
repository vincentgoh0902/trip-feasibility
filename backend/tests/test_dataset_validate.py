import shutil
from datetime import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from trip_feasibility.dataset import DatasetItinerary, Hotel, gold_violations, validate_dataset
from trip_feasibility.dataset.validate import dataset_version, itinerary_errors, load_yaml

DOCS = Path(__file__).resolve().parents[2] / "docs" / "annotation"
EXAMPLE = DOCS / "example.yaml"
EXAMPLE_TEXT = (DOCS / "example.md").read_text(encoding="utf-8").rstrip()


def example_raw() -> dict:
    """The example as load_dataset sees it: the YAML plus its text file."""
    return load_yaml(EXAMPLE) | {"text": EXAMPLE_TEXT}


def hotels(**red_flags) -> dict[str, Hotel]:
    base = load_yaml(DOCS / "hotels.yaml")
    out = {}
    for raw in base:
        raw["red_flags"] = red_flags.get(raw["key"].replace("-", "_"))
        out[raw["key"]] = Hotel.model_validate(raw)
    return out


def example(**changes) -> DatasetItinerary:
    raw = example_raw()
    raw.update(changes)
    return DatasetItinerary.model_validate(raw)


def stop(id, start, end, name, *, day=1, type="venue", **extra):
    return {"id": id, "day": day, "start_time": start, "end_time": end, "place_name": name,
            "type": type, **extra}  # fmt: skip


def test_yaml_keeps_times_as_text(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("a: 10:30\nb: 9:05\nc: 12\nd: 1.5\ne: 2026-12-08\n")
    data = load_yaml(path)
    assert data["a"] == "10:30" and data["b"] == "9:05"
    assert data["c"] == 12 and data["d"] == 1.5
    assert str(data["e"]) == "2026-12-08"


def test_example_is_valid():
    assert itinerary_errors(example(), hotels()) == []


def test_example_gold_violations():
    gold = gold_violations(example(), hotels())
    gold = {(g.check, g.severity, g.day, g.place, g.from_place, g.derived) for g in gold}
    assert gold == {
        ("opening_hours", "blocker", 1, "Louvre Museum", None, False),
        ("travel_time", "warning", 1, "Musée d'Orsay", "Café Marly", False),
        ("travel_time", "blocker", 1, "Eiffel Tower", "Musée d'Orsay", False),
    }


def test_a_flagged_hotel_gives_one_label():
    itin = example()
    [g] = [g for g in gold_violations(itin, hotels(paris_regina=["noise"])) if g.derived]
    assert (g.check, g.day, g.place, g.severity) == ("hotel_red_flags", None, "Hôtel Regina",
                                                     "warning")  # fmt: skip
    assert gold_violations(itin, hotels(paris_regina=[])) == gold_violations(itin, hotels())
    assert itinerary_errors(example(hotel="london-cavendish"), hotels()) == [
        "hotel london-cavendish is in london"
    ]
    assert itinerary_errors(example(hotel="nowhere"), hotels()) == [
        "hotel nowhere: not in hotels.yaml"
    ]


def test_template_loads_as_unannotated():
    itin = DatasetItinerary.model_validate(load_yaml(DOCS / "template.yaml") | {"text": "x"})
    assert not itin.annotated
    assert itinerary_errors(itin, hotels()) == []


def test_place_name_must_be_verbatim():
    raw = example_raw()
    raw["stops"][1]["place_name"] = "The Louvre"
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == ["stop s2: place_name 'The Louvre' isn't copied from the cleaned text"]


def test_text_limits_apply_to_the_cleaned_text():
    images = "\n[pic](https://images.openai.com/abc?purpose=inline)\n" * 300  # ~17K raw
    text = EXAMPLE_TEXT
    assert example(text=text + images).clean_text == example().clean_text
    with pytest.raises(ValidationError, match="once cleaned"):
        example(text=text + "Louvre again.\n" * 1100)


def test_no_leg_to_or_from_a_stop_without_a_location():
    raw = example_raw()
    raw["stops"][2] |= {"place_name": "Café Marly", "located": False}  # as if named "Lunch"
    itin = DatasetItinerary.model_validate(raw)
    # the example's s2 → s3 and s3 → s4 legs now touch a stop with no location
    assert itinerary_errors(itin, hotels()) == [
        "leg s2 → s3: one end has no location (located: false)",
        "leg s3 → s4: one end has no location (located: false)",
    ]
    raw["legs"] = [leg for leg in raw["legs"] if "s3" not in (leg["from"], leg["to"])]
    assert itinerary_errors(DatasetItinerary.model_validate(raw), hotels()) == []


def test_leg_must_join_consecutive_stops():
    raw = example_raw()
    raw["legs"].append({"from": "s1", "to": "s3", "mode": "walk", "minutes": 4, "source": "x"})
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == ["leg s1 → s3: not consecutive stops on a day"]


def test_stops_must_be_in_order():
    raw = example_raw()
    raw["stops"][3], raw["stops"][4] = raw["stops"][4], raw["stops"][3]
    raw["legs"] = []
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert "stop s4: starts before stop s5; keep stops in order" in errors


def label(**fields) -> dict:
    return {"check": "daily_load", "day": 1, "severity": "warning", "evidence": "e",
            "source": "s", "confidence": "estimated", **fields}  # fmt: skip


def test_quoted_words_must_be_verbatim():
    raw = example_raw()
    raw["violations"] = [
        label(check="opening_hours", place="The Louvre", time="9:00 – 12:00"),
        label(check="travel_time", place="Eiffel Tower", from_place="The Orsay", time="16:10"),
        label(),
        label(),
    ]
    raw["unknowns"] = [{"check": "opening_hours", "place": "Bateaux", "reason": "r"},
                       {"check": "daily_load", "day": 1, "reason": "r"}]  # fmt: skip
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == [
        "violation 1 (opening_hours, day 1): place 'The Louvre' isn't copied from the cleaned text",
        "violation 2 (travel_time, day 1): from_place 'The Orsay' isn't copied from the cleaned "
        "text",
        "violation 4 (daily_load, day 1): labelled twice",
    ]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"check": "opening_hours"}, "opening_hours labels name a place"),
        ({"check": "travel_time"}, "travel_time labels name a place"),
        ({"place": "Louvre Museum"}, "daily_load labels name a day, not a place"),
        ({"check": "opening_hours", "place": "x", "from_place": "y"}, "only travel_time"),
        ({"check": "hotel_red_flags"}, "Input should be"),  # from hotels.yaml, not the file
        ({"confidence": "sure"}, "Input should be"),
        ({"day": None}, "Input should be a valid integer"),
    ],
)
def test_violation_shape(fields, message):
    with pytest.raises(ValidationError, match=message):
        example(violations=[label(**fields)])


def test_hotel_stops():
    raw = example_raw()
    raw["stops"][0]["hotel"] = "london-cavendish"
    raw["stops"][1]["hotel"] = "paris-regina"
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == [
        "stop s1: hotel london-cavendish is in london",
        "stop s2: only hotel stops have a `hotel` key",
    ]


def test_a_draft_counts_once_verified():
    itin = example(drafted_by="gemini-x", verified_by_human=False)
    assert not itin.annotated and gold_violations(itin, hotels()) == []
    assert itinerary_errors(itin, hotels()) == [
        "still to do: verify the draft (verified_by_human: true)"
    ]
    assert example().annotated


def test_gold_needs_no_stops():
    raw = example_raw()
    del raw["stops"], raw["legs"]
    itin = DatasetItinerary.model_validate(raw)
    assert itin.annotated and itinerary_errors(itin, hotels()) == []
    assert len(gold_violations(itin, hotels())) == 3


def test_parts_go_together():
    raw = example_raw()
    del raw["stops"]
    with pytest.raises(ValidationError, match="add stops before legs"):
        DatasetItinerary.model_validate(raw)
    with pytest.raises(ValidationError, match="go together"):
        example(drafted_by=None)
    with pytest.raises(ValidationError, match="nothing to verify"):
        example(violations=None, drafted_by=None)


def test_stops_are_dev_only():
    assert itinerary_errors(example(split="test"), hotels()) == [
        "stops and legs are dev only (parser debugging)"
    ]


def test_synthetic_needs_a_parent():
    with pytest.raises(ValidationError, match="parent_id"):
        example(source="synthetic")
    with pytest.raises(ValidationError, match="parent_id"):
        example(parent_id="paris-01")
    assert example(source="synthetic", parent_id="paris-01").parent_id == "paris-01"


def test_stop_field_formats():
    itin = example(
        stops=[
            stop("a", "9:00", "9:30", "Louvre Museum", osm="https://www.openstreetmap.org/way/1"),
            stop("b", None, None, "Eiffel Tower", osm="relation/7"),
        ],
        legs=[],
        violations=[],
    )
    assert itin.stops is not None
    assert itin.stops[0].start_time == time(9) and itin.stops[0].osm.id == 1
    assert itin.stops[1].osm.type == "relation"
    with pytest.raises(ValidationError, match="not both"):
        example(stops=[{**stop("a", "09:00", None, "x"), "time_of_day": "morning"}])
    with pytest.raises(ValidationError, match="way/123"):
        example(stops=[stop("a", None, None, "x", osm="Louvre")])


def test_raw_text_limit():
    with pytest.raises(ValidationError, match="50000"):
        example(text="x" * 50_001)


@pytest.fixture
def dataset(tmp_path) -> Path:
    (tmp_path / "itineraries").mkdir()
    (tmp_path / "texts").mkdir()
    shutil.copy(DOCS / "hotels.yaml", tmp_path / "hotels.yaml")
    return tmp_path


def write(root: Path, name: str, text: str | None = EXAMPLE_TEXT, **changes) -> None:
    """The example, renamed and changed, as a dataset file and its text (None: no text)."""
    raw = load_yaml(EXAMPLE) | {"id": name, **changes}
    (root / "itineraries" / f"{name}.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    )
    if text is not None:
        (root / "texts" / f"{name}.md").write_text(text + "\n")  # as save_reply.sh writes it


def test_partial_dataset(dataset):
    write(dataset, "paris-01")
    write(dataset, "vienna-01", city="vienna", split="dev", text="Wien", stops=None, legs=None,
          violations=None, drafted_by=None, verified_by_human=False, hotel=None, unknowns=[],
          other_issues=[])  # fmt: skip
    report = validate_dataset(dataset, partial=True)
    assert [i.id for i in report.itineraries] == ["paris-01", "vienna-01"]
    assert report.errors == [
        "dev/vienna: 1 itineraries, expected 0",
        "vienna-01: vienna is held out, so it must be in test",
    ]


def test_complete_dataset_needs_everything(dataset):
    write(dataset, "paris-01")
    errors = validate_dataset(dataset).errors
    assert "dev: 1 itineraries, expected 15" in errors
    assert "hotels.yaml: paris-regina has no red_flags label yet" in errors


def test_file_name_must_match_id(dataset):
    write(dataset, "paris-01")
    (dataset / "itineraries" / "paris-01.yaml").rename(dataset / "itineraries" / "paris-02.yaml")
    (dataset / "texts" / "paris-01.md").rename(dataset / "texts" / "paris-02.md")
    errors = validate_dataset(dataset, partial=True).errors
    assert errors == ["itineraries/paris-02.yaml: id 'paris-01' doesn't match the file name"]


def test_schema_errors_name_the_file(dataset):
    write(dataset, "paris-01", city="rome")
    errors = validate_dataset(dataset, partial=True).errors
    assert len(errors) == 1 and errors[0].startswith("itineraries/paris-01.yaml: city: Input")


def test_version_changes_with_content(dataset):
    write(dataset, "paris-01")
    before = dataset_version(dataset)
    assert before == dataset_version(dataset)
    write(dataset, "paris-01", notes="edited")
    assert dataset_version(dataset) != before


def test_text_comes_from_the_text_file(dataset):
    write(dataset, "paris-01", text=EXAMPLE_TEXT + "\n\nExtra line.")
    [itin] = validate_dataset(dataset, partial=True).itineraries
    assert itin.text.endswith("Extra line.")


def test_text_file_is_required_and_text_stays_out_of_the_yaml(dataset):
    write(dataset, "paris-01", text=None)
    write(dataset, "paris-02", text_in_yaml="x")
    raw = load_yaml(dataset / "itineraries" / "paris-02.yaml")
    raw["text"] = raw.pop("text_in_yaml")
    (dataset / "itineraries" / "paris-02.yaml").write_text(yaml.safe_dump(raw))
    assert validate_dataset(dataset, partial=True).errors == [
        "itineraries/paris-01.yaml: no text at texts/paris-01.md (scripts/save_reply.sh paris-01)",
        "itineraries/paris-02.yaml: no `text` here; the reply lives in texts/<id>.md",
    ]


def test_a_text_without_its_yaml_is_fine_until_the_dataset_is_complete(dataset):
    (dataset / "texts" / "paris-09.md").write_text("Day 1\n")
    assert validate_dataset(dataset, partial=True).errors == []
    assert "texts/paris-09.md: no itineraries/paris-09.yaml" in validate_dataset(dataset).errors


def test_version_covers_the_texts(dataset):
    write(dataset, "paris-01")
    before = dataset_version(dataset)
    (dataset / "texts" / "paris-01.md").write_text(EXAMPLE_TEXT + " edited\n")
    assert dataset_version(dataset) != before
