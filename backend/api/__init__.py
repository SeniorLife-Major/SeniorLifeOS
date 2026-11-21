from .auth import router as auth_router
from .benefits import router as benefits_router
from .demo import router as demo_router
from .documents import router as documents_router
from .health_requests import router as health_requests_router
from .profile import router as profile_router

__all__ = ["auth_router", "benefits_router", "demo_router", "documents_router", "health_requests_router", "profile_router"]
