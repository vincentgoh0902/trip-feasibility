import pytest
import yaml

from trip_feasibility.dataset.layout import DEFAULT_PREFS, SKELETON, LayoutError, format_itinerary
from trip_feasibility.dataset.validate import _Loader
from trip_feasibility.itinerary import TravelPreferences

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
    duration_max: 60
    day: 1
    duration_min: 45
    time_of_day: afternoon
    place_name: "Café, Example"
legs: [{to: s2, from: s1, minutes: 12, mode: walk, source: Google Maps}]
hotel: paris-regina
violations:
- {severity: warning, check: daily_load, day: 1, evidence: "Too much, even for a long day",
   source: judgement, confidence: estimated}
unknowns: [{check: opening_hours, day: 1, place: Musée d’Ici, reason: "No hours: closed?"}]
other_issues: [Needs a booking]
drafted_by: human
verified_by_human: true
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
hotel: paris-regina
# text: texts/paris-99.md

# --- gold ---
drafted_by: human
verified_by_human: true
violations:
  - check: daily_load
    day: 1
    severity: warning
    evidence: Too much, even for a long day
    source: judgement
    confidence: estimated
unknowns:
  - check: opening_hours
    day: 1
    place: Musée d’Ici
    reason: "No hours: closed?"
other_issues:
  - Needs a booking
notes: Made-up test itinerary.

# --- dev only, for parser debugging (frozen rules) ---
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
    duration_min: 45
    duration_max: 60
    place_name: Café, Example
    type: meal
legs:
  - { from: s1, to: s2, mode: walk, minutes: 12, source: Google Maps }
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
    assert "preferences:\n  pace: moderate\n  max_walk_km: 10\n" in out
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


def test_written_out_defaults_match_the_model_defaults():
    assert TravelPreferences(**DEFAULT_PREFS) == TravelPreferences()


def test_files_from_the_old_skeleton_still_format():
    old = (
        "id: x-01\ncity: paris\nsplit: dev\nsource: claude\ntrip_start: 2027-01-05\n"
        "# preferences: defaults (moderate, 10 km, walk + transit)\n"
        '#   - { id: s1, day: 1, start_time: "09:00", end_time: "12:00", '
        "place_name: ..., type: venue }\n"
    )
    assert "  pace: moderate" in format_itinerary(old)
