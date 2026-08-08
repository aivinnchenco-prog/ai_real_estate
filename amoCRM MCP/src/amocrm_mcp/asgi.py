"""ASGI app with /health and MCP bearer auth."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .config import Settings
from .server import build_server


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if not self.token:
            return JSONResponse({"error": "MCP_API_TOKEN not configured"}, status_code=503)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self.token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    mcp = build_server(settings)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        host=settings.mcp_host,
    )
    routes = [
        Route("/health", endpoint=health, methods=["GET"]),
        Mount(settings.mcp_path, app=mcp_app),
    ]
    middleware = [Middleware(BearerAuthMiddleware, token=settings.mcp_api_token)]
    return Starlette(debug=False, routes=routes, middleware=middleware)
