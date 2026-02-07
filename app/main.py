import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Query


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data" / "laptops.json"

app = FastAPI(title="Laptop Dashboard (JSON-based)")

# Static + Templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# -----------------------------
# Helpers
# -----------------------------
_cache: Dict[str, Any] = {"mtime": None, "items": []}


def slugify(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t or "item"


def parse_price(value: Any) -> int:
    """
    Accepts: 45000, "45,000", "Rs 45,000"
    Returns: int
    """
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value)
    s = s.replace(",", "").strip()
    s = re.sub(r"[^0-9.]", "", s)
    if s == "":
        return 0
    return int(float(s))


def normalize_pics(raw: Any) -> List[str]:
    # User wants: pic: [..2..]
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def resolve_pic_url(pic: str) -> str:
    """
    If pic is:
    - full URL: keep
    - starts with "/": keep
    - otherwise treat as under /static/ (e.g. "images/a.jpg" -> "/static/images/a.jpg")
    """
    pic = (pic or "").strip()
    if not pic:
        return ""
    if pic.startswith("http://") or pic.startswith("https://"):
        return pic
    if pic.startswith("/"):
        return pic
    return f"/static/{pic}"


def load_laptops() -> List[Dict[str, Any]]:
    """
    Cached load with mtime check.
    """
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Missing data file: {DATA_FILE}")

    mtime = DATA_FILE.stat().st_mtime
    if _cache["mtime"] == mtime and _cache["items"]:
        return _cache["items"]

    with DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("laptops.json must be a LIST of objects.")

    items: List[Dict[str, Any]] = []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            continue

        name = str(row.get("name", "")).strip()
        if not name:
            name = f"Untitled Laptop {idx+1}"

        price = parse_price(row.get("price", 0))

        # accept both "pic" and "pics"
        pics_raw = row.get("pic", None)
        if pics_raw is None:
            pics_raw = row.get("pics", None)
        pics = normalize_pics(pics_raw)

        desc = str(row.get("description", "")).strip()

        # stable id (unique even if same name)
        item_id = f"{slugify(name)}-{idx+1}"

        items.append(
            {
                "id": item_id,
                "name": name,
                "price": price,
                "pics": pics,
                "description": desc,
            }
        )

    # sort by price (low to high)
    items.sort(key=lambda x: x["price"])

    _cache["mtime"] = mtime
    _cache["items"] = items
    return items


def filter_laptops(
    items: List[Dict[str, Any]],
    min_price: Optional[int],
    max_price: Optional[int],
    q: Optional[str],
) -> List[Dict[str, Any]]:
    out = items

    if min_price is not None:
        out = [x for x in out if x["price"] >= min_price]
    if max_price is not None:
        out = [x for x in out if x["price"] <= max_price]

    if q:
        qq = q.strip().lower()
        out = [x for x in out if qq in x["name"].lower()]

    return out

# add this helper near other helpers
def normalize_range_value(v: Optional[int]) -> Optional[int]:
    """
    If user passes 50-60, assume 50k-60k (Pakistan common style).
    If value is < 1000, multiply by 1000.
    """
    if v is None:
        return None
    if 0 < v < 1000:
        return v * 1000
    return v



# -----------------------------
# Routes
# -----------------------------

@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,

    # old style
    min_price: Optional[int] = Query(default=None, ge=0),
    max_price: Optional[int] = Query(default=None, ge=0),

    # new short style for WhatsApp links
    min: Optional[int] = Query(default=None, ge=0),
    max: Optional[int] = Query(default=None, ge=0),

    q: Optional[str] = Query(default=None),
):
    items = load_laptops()

    # pick whichever user provided (min/max takes priority if present)
    effective_min = normalize_range_value(min if min is not None else min_price)
    effective_max = normalize_range_value(max if max is not None else max_price)

    filtered = filter_laptops(items, effective_min, effective_max, q)

    for x in filtered:
        x["pic_urls"] = [resolve_pic_url(p) for p in x.get("pics", [])]
        if not x["pic_urls"]:
            x["pic_urls"] = ["https://via.placeholder.com/160x120?text=No+Image"]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "items": filtered,
            "min_price": (min if min is not None else (min_price or "")),
            "max_price": (max if max is not None else (max_price or "")),
            "q": q or "",
            "effective_min": effective_min or "",
            "effective_max": effective_max or "",
        },
    )


@app.get("/product/{item_id}", response_class=HTMLResponse)
def product_page(request: Request, item_id: str):
    items = load_laptops()
    item = next((x for x in items if x["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Laptop not found")

    pics = [resolve_pic_url(p) for p in item.get("pics", [])]
    if len(pics) == 0:
        pics = ["https://via.placeholder.com/900x600?text=No+Image"]
    if len(pics) == 1:
        # ensure 2 slots (user asked 2 pictures)
        pics = [pics[0], pics[0]]

    return templates.TemplateResponse(
        "product.html",
        {
            "request": request,
            "item": item,
            "pics": pics[:2],
        },
    )


# Optional JSON API (useful later)
@app.get("/api/laptops")
def api_laptops(
    min_price: Optional[int] = Query(default=None, ge=0),
    max_price: Optional[int] = Query(default=None, ge=0),
    q: Optional[str] = Query(default=None),
):
    items = load_laptops()
    filtered = filter_laptops(items, min_price, max_price, q)
    return {"count": len(filtered), "items": filtered}
