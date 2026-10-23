"""A fake OSRM table service: 0.01 degrees (lat + lon, Manhattan) = 1 km.

Speeds: foot 5 km/h, bike 15 km/h, car 30 km/h, so 1 km takes 12 / 4 / 2 minutes.
Points whose latitude is 0 are unreachable (null in the table).
"""

import re

import httpx

ROUTING = re.compile(r"https://routing\.test/routed-(foot|bike|car)/table/v1/driving/(.+)")
SPEED_KMH = {"foot": 5.0, "bike": 15.0, "car": 30.0}


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (abs(a[0] - b[0]) + abs(a[1] - b[1])) * 100


def respond(request: httpx.Request) -> httpx.Response:
    profile, coords = ROUTING.match(str(request.url).split("?")[0]).groups()
    points = [tuple(reversed([float(x) for x in c.split(",")])) for c in coords.split(";")]
    distances, durations = [], []
    for a in points:
        drow, trow = [], []
        for b in points:
            if 0.0 in (a[0], b[0]) and a != b:
                drow.append(None)
                trow.append(None)
                continue
            km = km_between(a, b)
            drow.append(km * 1000)
            trow.append(km / SPEED_KMH[profile] * 3600)
        distances.append(drow)
        durations.append(trow)
    return httpx.Response(200, json={"code": "Ok", "distances": distances, "durations": durations})


def install(respx_mock):
    return respx_mock.get(url__regex=ROUTING.pattern).mock(side_effect=respond)
