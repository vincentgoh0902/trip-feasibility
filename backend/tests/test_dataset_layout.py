import pytest
import yaml

from trip_feasibility.dataset.layout import SKELETON, LayoutError, format_itinerary
from trip_feasibility.dataset.validate import _Loader

MESSY = """\
id: paris-99
city: paris
split: dev
source: claude
trip_start: 2027-01-05
preferences: {pace: relaxed, max_walk_km: 6, transport_modes: [walk, transit]}
stops:
  - {id: s1, day: 1, start_time: '9:00', end_time: "10:30", place_name: Musée d’Ici, type: venue}
  - id: s2
    type: meal
    day: 1
    time_of_day: afternoon
    place_name: "Café, Example"
legs: [{to: s2, from: s1, minutes: 12, mode: walk, source: Google Maps}]
violations:
- check: daily_load
  severity: warning
  day: 1
  note: Too much, even for a long day
notes: Made-up test itinerary.
"""

STANDARD = """\
id: paris-99
city: paris
split: dev
source: claude
trip_start: 2027-01-05
preferences:
  pace: relaxed
  max_walk_km: 6
  transport_modes: [walk, transit]
# text: texts/paris-99.md

# --- annotation ---
stops:
  - id: s1
    day: 1
    start_time: "9:00"
    end_time: "10:30"
    place_name: Musée d’Ici
    type: venue
  - id: s2
    day: 1
    time_of_day: afternoon
    place_name: Café, Example
    type: meal
legs:
  - { from: s1, to: s2, mode: walk, minutes: 12, source: Google Maps }
violations:
  - { check: daily_load, severity: warning, day: 1, note: "Too much, even for a long day" }
notes: Made-up test itinerary.
"""


def load(text):
    return yaml.load(text, Loader=_Loader)


def test_messy_file_gets_the_standard_layout_with_the_same_data():
    assert format_itinerary(MESSY) == STANDARD
    assert load(STANDARD) == load(MESSY)


def test_formatting_is_idempotent():
    assert format_itinerary(STANDARD) == STANDARD


def test_unannotated_skeleton_keeps_its_comment_block():
    skeleton = "id: london-99\ncity: london\nsplit: test\nsource: gemini\ntrip_start: 2027-03-01\n"
    out = format_itinerary(skeleton)
    assert "# preferences: defaults" in out
    assert out.endswith(SKELETON + "\n")
    assert format_itinerary(out) == out


def test_long_prompt_folds_and_reads_back_the_same():
    prompt = ("Plan a made-up trip " * 20).strip()
    text = (
        f"id: x-01\ncity: paris\nsplit: dev\nsource: claude\nprompt: {prompt}\n"
        "trip_start: 2027-01-05\n"
    )
    out = format_itinerary(text)
    assert "prompt: >-\n" in out
    assert load(out)["prompt"] == prompt


def test_files_with_their_own_comments_are_refused():
    with pytest.raises(LayoutError, match="comments"):
        format_itinerary(STANDARD.replace("notes:", "# check this again\nnotes:"))


def test_invalid_yaml_is_refused_without_quoting_it():
    with pytest.raises(LayoutError, match=r"line \d+") as err:
        format_itinerary("id: x\nstops: [secret text\n")
    assert "secret" not in str(err.value)
