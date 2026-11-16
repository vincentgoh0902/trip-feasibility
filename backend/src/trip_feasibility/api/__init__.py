"""The REST API under /api/v1 (PRD "API design")."""

from fastapi import APIRouter

from trip_feasibility.api import checks, demo, fixes, itineraries, me

router = APIRouter()
for module in (itineraries, checks, demo, fixes, me):
    router.include_router(module.router)
