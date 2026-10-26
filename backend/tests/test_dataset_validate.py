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
    gold = {(g.check, g.severity, g.target, g.derived) for g in gold}
    assert gold == {
        ("opening_hours", "blocker", ("stop", "s2"), False),
        ("travel_time", "warning", ("leg", "s3", "s4"), True),
        ("travel_time", "blocker", ("leg", "s4", "s5"), True),
    }


def test_hotel_red_flags_give_one_label_per_hotel():
    raw = example_raw()
    raw["stops"].append(
        {
            "id": "s7",
            "day": 1,
            "time_of_day": "evening",
            "place_name": "Hôtel Regina",
            "type": "hotel",
            "hotel": "paris-regina",
        }  # fmt: skip
    )
    itin = DatasetItinerary.model_validate(raw)
    gold = gold_violations(itin, hotels(paris_regina=["noise"]))
    assert [g.target for g in gold if g.check == "hotel_red_flags"] == [("hotel", "paris-regina")]
    assert gold_violations(itin, hotels(paris_regina=[])) == gold_violations(itin, hotels())


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


def test_tight_leg_needs_a_travel_time():
    raw = example_raw()
    raw["legs"] = [leg for leg in raw["legs"] if leg["from"] != "s3"]
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == ["leg s3 → s4: only 15 min apart, so record its real travel time"]


def test_duration_counts_as_an_end_for_which_legs_to_record():
    raw = example_raw()
    raw["legs"] = [leg for leg in raw["legs"] if leg["from"] != "s2"]
    raw["stops"][1] |= {"end_time": None, "duration_min": 150, "duration_max": 170}
    itin = DatasetItinerary.model_validate(raw)
    errors = itinerary_errors(itin, hotels())
    # 09:00 + 170 min = 11:50, 25 min before lunch at 12:15
    assert errors == [
        "leg s2 → s3: only 25 min apart counting s2's duration, so record its real travel time"
    ]
    # a duration never produces a travel-time label: labels need a stated end
    assert not any(g.target == ("leg", "s2", "s3") for g in gold_violations(itin, hotels()))

    raw["stops"][1] |= {"duration_min": 60, "duration_max": None}  # ends 10:00: no leg needed
    assert itinerary_errors(DatasetItinerary.model_validate(raw), hotels()) == []


def test_stated_journey_needs_a_leg_and_labels_when_well_short():
    raw = example_raw()
    raw["stops"][1] |= {"end_time": None}  # Louvre 09:00, no end: no gap to go on
    raw["stops"][2] |= {"start_time": None, "end_time": None, "travel_min": 5, "travel_max": 8}
    raw["legs"] = [leg for leg in raw["legs"] if leg["from"] != "s2"]
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == [
        "leg s2 → s3: the itinerary says how long this journey takes, so record its real "
        "travel time"
    ]

    def label(minutes):
        legs = raw["legs"] + [{"from": "s2", "to": "s3", "mode": "walk", "minutes": minutes,
                               "source": "x"}]  # fmt: skip
        itin = DatasetItinerary.model_validate(raw | {"legs": legs})
        assert itinerary_errors(itin, hotels()) == []
        return [g.severity for g in gold_violations(itin, hotels()) if g.target[1:] == ("s2", "s3")]

    assert label(18) == []  # 8 claimed + 10 margin
    assert label(19) == ["warning"]


def test_stated_departure_is_the_gap_start():
    raw = example_raw()
    # leave the Louvre at 12:05 for lunch at 12:15 (a 3-min walk): a warning, where 15 min was fine
    raw["stops"][2] |= {"travel_start": "12:05"}
    gold = {g.target: g.severity for g in gold_violations(DatasetItinerary.model_validate(raw),
                                                         hotels())}  # fmt: skip
    assert gold[("leg", "s2", "s3")] == "warning"


def test_leg_must_join_consecutive_stops():
    raw = example_raw()
    raw["legs"].append({"from": "s1", "to": "s3", "mode": "walk", "minutes": 4, "source": "x"})
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == ["leg s1 → s3: not consecutive stops on a day"]


def test_overlapping_stops_are_a_blocker_without_a_leg():
    raw = example_raw()
    raw["stops"][2]["start_time"] = "11:30"  # lunch starts before the Louvre visit ends
    raw["legs"] = [leg for leg in raw["legs"] if leg["from"] != "s2"]
    itin = DatasetItinerary.model_validate(raw)
    assert itinerary_errors(itin, hotels()) == []
    gold = {(g.check, g.severity, g.target) for g in gold_violations(itin, hotels())}
    assert ("travel_time", "blocker", ("leg", "s2", "s3")) in gold


def test_stops_must_be_in_order():
    raw = example_raw()
    raw["stops"][3], raw["stops"][4] = raw["stops"][4], raw["stops"][3]
    raw["legs"] = []
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert "stop s4: starts before stop s5; keep stops in order" in errors


def test_violation_targets():
    raw = example_raw()
    raw["violations"] = [
        {"check": "opening_hours", "severity": "warning", "stop": "s9", "note": "n"},
        {"check": "daily_load", "severity": "warning", "day": 3, "note": "n"},
        {"check": "public_holidays", "severity": "warning", "day": 1, "note": "n"},
        {"check": "public_holidays", "severity": "warning", "day": 1, "note": "again"},
    ]
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == [
        "violation opening_hours: stop s9 doesn't exist",
        "violation daily_load: day 3 has no stops",
        "violation public_holidays on 1: labelled twice",
    ]


@pytest.mark.parametrize(
    ("violation", "message"),
    [
        ({"check": "opening_hours", "day": 1}, "opening_hours labels name a stop, not a day"),
        ({"check": "daily_load", "stop": "s1"}, "daily_load labels name a day, not a stop"),
        ({"check": "travel_time", "stop": "s1"}, "Input should be"),
    ],
)
def test_violation_shape(violation, message):
    with pytest.raises(ValidationError, match=message):
        example(violations=[{"severity": "warning", "note": "n", **violation}])


def test_hotel_stops():
    raw = example_raw()
    raw["stops"][0]["hotel"] = "london-cavendish"
    raw["stops"][1]["hotel"] = "paris-regina"
    errors = itinerary_errors(DatasetItinerary.model_validate(raw), hotels())
    assert errors == [
        "stop s1: hotel london-cavendish is in london",
        "stop s2: only hotel stops have a `hotel` key",
    ]


def test_annotation_parts_go_together():
    raw = example_raw()
    del raw["legs"]
    with pytest.raises(ValidationError, match="missing: legs"):
        DatasetItinerary.model_validate(raw)


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
          violations=None)  # fmt: skip
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
