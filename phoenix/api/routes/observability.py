"""PHX-115 ``GET /metrics`` (ADR-146): runtime metrics and recent event logs.

Behind the PHX-101 middleware like every route (bound Host, allowed Origin,
bearer token). The body holds counts, millisecond timings, byte sizes, IDs and
closed values only; no prompt, answer, tool output, path or secret.
"""

from fastapi import APIRouter, Request

from phoenix.api.observability import MetricsOut
from phoenix.api.routes.common import ApiError
from phoenix.api.routes.common import services as backend

router = APIRouter()


@router.get("/metrics", response_model=MetricsOut)
async def get_metrics(request: Request) -> MetricsOut:
    collector = backend(request).observability
    if collector is None:
        raise ApiError(503, "service_unavailable")
    return await collector.snapshot()
