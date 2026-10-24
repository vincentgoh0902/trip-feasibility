from trip_feasibility.parse.clean import clean_text

# The shapes a ChatGPT reply takes when copied: image links, a citation block, entities,
# escapes, widget counters and map hints. Made up; no real reply is committed.
CHATGPT = """\
# Lyon in 2 days

[Old town at dusk - Some Blog](https://images.openai.com/static-rsc-4/AbC123?purpose=inline)

[Cathedral photo](https://images.openai.com/static-rsc-4/DeF456?purpose=inline)

8

## Day 1 — Monday

### 9:00–11:00 a.m. — Musée des Beaux-Arts

It opens at 10 on Mondays.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.mba-lyon.fr\\&sz=32)

Plan your visit

+1



### 12:00–1:00 p.m. — Lunch at [Les Halles](https://www.halles-de-lyon-paulbocuse.com/)

Fish \\& chips it isn't. ![photo](https://example.com/halles.jpg) Book ahead.

canvas

4.7

Use two fingers to move the map

Use ⌘ + scroll to zoom the map

### Your pre-trip checklist

0 of 3
"""


def test_strips_chat_debris_and_keeps_the_itinerary():
    assert clean_text(CHATGPT) == (
        "# Lyon in 2 days\n\n"
        "## Day 1 — Monday\n\n"
        "### 9:00–11:00 a.m. — Musée des Beaux-Arts\n\n"
        "It opens at 10 on Mondays.\n\n"
        "### 12:00–1:00 p.m. — Lunch at Les Halles\n\n"
        "Fish & chips it isn't.  Book ahead.\n\n"
        "### Your pre-trip checklist"
    )


def test_plain_text_is_unchanged():
    text = "Day 1\n09:00 Louvre\n12:30 Lunch at Café Marly\n\nDay 2\n10:00 Musée d'Orsay"
    assert clean_text(text) == text


def test_a_favicon_without_a_citation_count_keeps_the_next_line():
    text = "[image](https://www.google.com/s2/favicons?domain=x.fr)\n\n10:00 Sainte-Chapelle"
    assert clean_text(text) == "10:00 Sainte-Chapelle"


def test_ordinary_links_keep_their_text():
    text = "[Louvre](https://www.louvre.fr) at 9, then [photo](https://cdn.x.com/a.png)"
    assert clean_text(text) == "Louvre at 9, then"


def test_numbers_inside_lines_and_times_stay():
    text = "10:30 Tower of London\n2 hours here\n4.5 km walk"
    assert clean_text(text) == text
