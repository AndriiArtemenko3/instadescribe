"""The /api/v1 router — the single token boundary.

The authentication dependency is mounted HERE, once; every route included
beneath this router inherits it automatically (structurally tested). Health
and readiness live outside it and stay public.
"""

from fastapi import APIRouter, Depends

from app.api.jobs import router as jobs_router
from app.api.manifest import router as manifest_router
from app.api.projects import router as projects_router
from app.api.scenes import router as scenes_router
from app.core.security import verify_portfolio_token

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_portfolio_token)])
router.include_router(jobs_router)
router.include_router(manifest_router)
router.include_router(scenes_router)
router.include_router(projects_router)
