"""Demo routes (PRD FR-6: no sign-in, nothing saved): the samples, and parsing a pasted
sample. Starting a demo run is POST /checks with a sample or pasted stops (checks.py)."""

from fastapi import APIRouter, Request

from trip_feasibility.api.common import Svc, client_ip
from trip_feasibility.api.itineraries import parse_text
from trip_feasibility.api.schemas import ParseRequest, ParseResponse, SampleOut
from trip_feasibility.demo import sample_for_text, samples
from trip_feasibility.errors import AppError
from trip_feasibility.prepared import PreparedClient

router = APIRouter(prefix="/demo", tags=["demo"])

NOT_PREPARED = (
    "This demo reads its sample itineraries only, with their city and start date: copy one "
    "from the samples. Checking your own itinerary isn't open to visitors."
)


@router.get("/samples")
async def demo_samples() -> list[SampleOut]:
    return [SampleOut.model_validate(s.model_dump()) for s in samples().values()]


@router.post("/parse")
async def demo_parse(body: ParseRequest, request: Request, svc: Svc) -> ParseResponse:
    """Text → stops without signing in. Extraction gets only replies prepared for the
    samples (decision 100): other text, or a sample with another city or date, is refused
    without a model call."""
    svc.parse_limit.hit(f"ip:{client_ip(request)}")
    sample = sample_for_text(body.text)
    if sample is not None:
        body = body.model_copy(update={"text": sample.text})
    try:
        return await parse_text(svc, PreparedClient(svc.deps.cache), body)
    except AppError as e:
        if e.code == "not_prepared":
            raise AppError(422, "not_prepared", NOT_PREPARED) from e
        raise
