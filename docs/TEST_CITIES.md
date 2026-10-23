# Test-set cities and hotels

The dataset covers **Paris, London, Barcelona and Vienna**: four of the six cities in the
Kaggle "515K Hotel Reviews Data in Europe" set (CC0, Booking.com reviews from 2015–2017), so
the hotel red-flags check can be evaluated in every city.

Each city has 4 hotels for writing itineraries: 2 high-scoring and 2 low-scoring. The
low-scoring ones should give the red-flags check something real to find. The high-scoring
ones are clean controls, so a flag on one of them is a false positive.

**Vienna is held out of the dev set** (decided 2026-10-24): dev is 5 itineraries each from
Paris, London and Barcelona; test is the other 15 plus all 10 from Vienna. Rules in PLAN.md
"Decisions".

## How they were picked

- At least 300 reviews in the CSV, so retrieval has enough material, and coordinates present.
- Ranked by `Average_Score`; ties broken by the mean `Reviewer_Score` in the CSV.
- Hotels outside the tourist core (airport, Greenwich O2, Chigwell, outer districts) were
  skipped, since an itinerary wouldn't plausibly use them.
- Low-scoring picks were checked for recurring complaints: the share of reviews whose
  negative text mentions a theme (keyword match, rough). Hilton Vienna (8.0) was swapped for
  Suite Hotel 900 m zur Oper (also 8.0) because its complaints were barely above the
  high-scoring hotels' rates.

"Low" is relative: Booking.com scores skew high, so the lowest central hotels score 6.4–7.7.

## Hotels

Names are as written in the CSV, which drops accented characters ("H tel" = "Hôtel").
Itineraries should use the real name.

| City | Hotel (real name) | Group | Avg score | Reviews in CSV | Noise | Cleanliness | Location |
|---|---|---|---|---|---|---|---|
| Paris | Hyatt Regency Paris Etoile | low | 7.1 | 874 | 11% | 12% | 2% |
| Paris | Mercure Paris Terminus Nord | low | 7.4 | 1459 | 4% | 14% | 3% |
| Paris | Hôtel Regina | high | 9.0 | 372 | 2% | 2% | 1% |
| Paris | La Clef Tour Eiffel | high | 9.0 | 315 | 2% | 6% | 2% |
| London | Hotel Cavendish | low | 6.4 | 920 | 12% | 22% | 2% |
| London | Commodore Hotel | low | 6.7 | 953 | 10% | 19% | 2% |
| London | Rosewood London | high | 9.4 | 390 | 2% | 2% | 2% |
| London | Batty Langley's | high | 9.4 | 345 | 1% | 0% | 1% |
| Barcelona | Catalonia Park Putxet | low | 7.4 | 442 | 14% | 10% | 14% |
| Barcelona | Expo Hotel Barcelona | low | 7.6 | 805 | 9% | 7% | 2% |
| Barcelona | Hotel Casa Camper | high | 9.6 | 301 | 6% | 1% | 1% |
| Barcelona | Catalonia Magdalenes | high | 9.4 | 311 | 2% | 3% | 1% |
| Vienna | Exe Vienna | low | 7.7 | 351 | 9% | 13% | 7% |
| Vienna | Suite Hotel 900 m zur Oper | low | 8.0 | 439 | 12% | 11% | 1% |
| Vienna | Best Western Premier Kaiserhof Wien | high | 9.4 | 334 | 1% | 2% | 2% |
| Vienna | Hotel Rathaus Wein & Design | high | 9.3 | 338 | 2% | 2% | 2% |

Safety complaints were 0–2% everywhere, so no hotel here tests the safety theme.

The reviews are from 2015–2017. A hotel may have been renamed or closed since; that is fine
for the eval (the check reads the dataset, not live data), but Nominatim geocoding needs the
name it has today.
