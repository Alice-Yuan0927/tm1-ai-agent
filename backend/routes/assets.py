from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import FRONTEND_DIR

router = APIRouter()

_ALLOWED_JS_ASSETS = frozenset({
    "frontend.tailwind.js",
    "config.js", "markdown.js", "charts.js", "table.js",
    "store.js", "share.js", "ui.js", "sidebar.js",
    "cubes.js", "render.js", "api.js", "main.js",
})


def _serve_js(asset_name: str) -> FileResponse:
    if asset_name not in _ALLOWED_JS_ASSETS:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / "js" / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/")
def index():
    return FileResponse(
        FRONTEND_DIR / "frontend.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/{asset_name}")
def frontend_asset(asset_name: str):
    return _serve_js(asset_name)


@router.get("/js/{asset_name}")
def frontend_js_asset(asset_name: str):
    return _serve_js(asset_name)


@router.get("/js/vendor/{asset_name}")
def frontend_vendor_js_asset(asset_name: str):
    allowed_assets = {"chartjs-plugin-datalabels.min.js"}
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / "js" / "vendor" / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/assets/{asset_name}")
def frontend_image_asset(asset_name: str):
    allowed_assets = {"logo.svg", "block2.png"}
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / "assets" / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )
