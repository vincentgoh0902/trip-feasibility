"""The REST API under /api/v1 (PRD "API design")."""

from fastapi import APIRouter

from trip_feasibility.api import checks, fixes, itineraries, me

router = APIRouter()
for module in (itineraries, checks, fixes, me):
    router.include_router(module.router)
