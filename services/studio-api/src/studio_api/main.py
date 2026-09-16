"""FedOps Agent Studio API composition root."""

import re

import socketio
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from studio_runtime.agents import AGENT_SERVING_PORT_MAX, AGENT_SERVING_PORT_MIN

from .config import LOCAL_BROWSER_ORIGINS, SOCKET_CORS_ORIGINS, TRUSTED_HOSTS, WEB_DIST_DIR
from .features.agent_builder.router import router as agent_builder_router
from .features.agents.router import router as agents_router
from .features.auth.router import router as auth_router
from .features.environments.router import router as environments_router
from .features.federated_learning.router import router as federated_learning_router
from .features.registry.router import router as registry_router
from .features.system.router import router as system_router
from .features.workspace.realtime import register_terminal_events
from .features.workspace.router import router as workspace_router


fastapi_app = FastAPI(title="FedOps Agent Studio API", version="0.1.0")
fastapi_app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=SOCKET_CORS_ORIGINS,
)
socket_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


class DedicatedServingPortApp:
    """Expose only the portable Agent API surface on dedicated local ports."""

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        server = scope.get("server")
        port = int(server[1]) if isinstance(server, tuple) and len(server) == 2 else None
        dedicated = (
            port is not None
            and AGENT_SERVING_PORT_MIN <= port <= AGENT_SERVING_PORT_MAX
        )
        path = str(scope.get("path") or "")
        direct_tool_path = re.fullmatch(r"/tools/[^/]+/(?:invoke|predict)", path)
        if dedicated and path not in {"/health", "/info", "/chat"} and not direct_tool_path:
            if scope.get("type") == "http":
                await JSONResponse(
                    {"detail": "This port serves one enabled Agent API only."},
                    status_code=404,
                )(scope, receive, send)
            elif scope.get("type") == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        await self.application(scope, receive, send)


app = DedicatedServingPortApp(socket_app)


def browser_origin_is_allowed(method: str, origin: str | None) -> bool:
    return (
        method in {"GET", "HEAD", "OPTIONS"}
        or not origin
        or "*" in LOCAL_BROWSER_ORIGINS
        or origin in LOCAL_BROWSER_ORIGINS
    )


@fastapi_app.middleware("http")
async def reject_cross_origin_mutation(request: Request, call_next):
    if not browser_origin_is_allowed(request.method, request.headers.get("origin")):
        return JSONResponse({"error": "Untrusted browser origin."}, status_code=403)
    return await call_next(request)

if (WEB_DIST_DIR / "assets").is_dir():
    fastapi_app.mount(
        "/assets",
        StaticFiles(directory=WEB_DIST_DIR / "assets"),
        name="studio-assets",
    )


@fastapi_app.get("/", include_in_schema=False)
async def root():
    index = WEB_DIST_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(
        {"detail": "The Agent Studio web build is not available."},
        status_code=503,
    )

fastapi_app.include_router(system_router)
fastapi_app.include_router(auth_router)
fastapi_app.include_router(workspace_router)
fastapi_app.include_router(environments_router)
fastapi_app.include_router(registry_router)
fastapi_app.include_router(federated_learning_router)
fastapi_app.include_router(agent_builder_router)
fastapi_app.include_router(agents_router)
register_terminal_events(sio)

__all__ = ["app", "fastapi_app", "sio"]
