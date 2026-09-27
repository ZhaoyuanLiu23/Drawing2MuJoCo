"""Local Web V0.1: drawing upload, 2D preview and the existing CAD CLI."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import UploadError
from app.routes.jobs import router
from app.routes.cad import router as cad_router
from app.routes.simulation import router as simulation_router

app = FastAPI(
    title="Drawing2MuJoCo Web",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.include_router(router)
app.include_router(cad_router)
app.include_router(simulation_router)


@app.exception_handler(UploadError)
async def upload_error_handler(request: Request, exc: UploadError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


@app.get("/api/health")
def health() -> dict[str, str]:
    """Report API availability, not CAD or simulation readiness."""
    return {"status": "ok"}
