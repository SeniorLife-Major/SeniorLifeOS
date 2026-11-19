"""The web app (frontend/, built by `npm run build` into frontend/dist), served by this backend.

The site and the API are then one address: the app calls /api/v1 on its own site, so browsers need
no CORS, and nginx or the ingress just pass everything here (frontend Decision 76). The Docker
image builds the app (Dockerfile). Without a build (local development uses Vite on port 5173)
nothing is added and / still opens the API docs. SENIORLIFE_WEB_DIR names another folder.

- /assets/...: Vite's files, named by their content, so a browser may keep them for a year.
- a file at the top of the build (favicon.svg, apple-touch-icon.png, ...): sent as it is.
- any other page address (/doctor/abc, /documents): index.html, which the app's router turns into
  that page; never cached, so a new version shows at once. Addresses of the API, its docs and the
  actuator never get the app: an unknown one stays a 404 in the API's own shape.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles


DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
WEB_DIR_SETTING = "SENIORLIFE_WEB_DIR"
NOT_THE_APP = {"api", "docs", "redoc", "openapi.json", "actuator", "metrics"}  # first part of the path
NO_CACHE = {"Cache-Control": "no-cache"}
KEEP_A_YEAR = "public, max-age=31536000, immutable"


def built_web_app(folder: Path | str | None = None) -> Path | None:
    """The folder holding the built app (with index.html), or None when it is not built here."""
    folder = Path(folder or os.getenv(WEB_DIR_SETTING) or DEFAULT_WEB_DIR).resolve()
    return folder if (folder / "index.html").is_file() else None


def app_page(folder: Path) -> FileResponse:
    return FileResponse(folder / "index.html", headers=NO_CACHE)


class AssetFiles(StaticFiles):
    """Vite's files under /assets. Their names change with their content, so they never go stale."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = KEEP_A_YEAR
        return response


def install_web_app(target: FastAPI, folder: Path | None) -> bool:
    """Serve the built app from folder (see built_web_app). False when there is nothing to serve.

    It is the router's last step (router.default), not a route: every route, the 405 answer and
    the slash redirect (/actuator to /actuator/) are tried first, whenever they were added, and
    only an address nothing else answers gets the app."""
    if folder is None:
        return False
    if (folder / "assets").is_dir():
        target.mount("/assets", AssetFiles(directory=folder / "assets"), name="web-assets")
    not_found = target.router.default

    async def web_page(scope, receive, send) -> None:
        path = scope["path"].lstrip("/") if scope["type"] == "http" else ""
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD") or                 path.split("/", 1)[0] in NOT_THE_APP:
            await not_found(scope, receive, send)  # the API's own 404
            return
        file = (folder / path).resolve()
        if path and file.parent == folder and file.is_file():  # only files at the top, never ../
            response = FileResponse(file, headers=NO_CACHE)
        else:
            response = app_page(folder)
        await response(scope, receive, send)

    target.router.default = web_page
    return True
