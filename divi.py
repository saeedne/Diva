"""Client helpers for Divar's public web search endpoint.

This is an unofficial integration. Divar can change the internal web API at any time.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import requests


DIVAR_API = "https://api.divar.ir/v8"
KENAR_API = "https://open-api.divar.ir/v1/open-platform/assets"
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "text/plain;charset=UTF-8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
}

class DivarError(Exception):
    """An expected error while parsing a request or reading Divar's public API."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


session = requests.Session()
session.headers.update(BASE_HEADERS)
_detail_local = threading.local()


def _detail_session() -> requests.Session:
    client = getattr(_detail_local, "session", None)
    if client is None:
        client = requests.Session()
        client.headers.update(BASE_HEADERS)
        _detail_local.session = client
    return client


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def get_city_list() -> list[dict[str, str]]:
    """Return Divar's current city directory with Persian names and URL slugs."""
    try:
        response = session.get(f"{DIVAR_API}/places/cities", timeout=25)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise DivarError(502, f"دریافت فهرست شهرها از دیوار ناموفق بود: {exc}") from exc
    except ValueError as exc:
        raise DivarError(502, "پاسخ فهرست شهرهای دیوار JSON معتبر نیست.") from exc

    cities: dict[str, dict[str, str]] = {}
    for item in _walk_dicts(payload):
        slug = next((str(item[key]) for key in ("slug", "city_slug", "seo_slug", "name_en")
                     if item.get(key)), None)
        name = next((str(item[key]) for key in ("display", "name_persian", "name_fa", "city_name", "name", "title")
                     if item.get(key)), None)
        if slug and name and len(slug) < 80:
            cities[slug.casefold()] = {"slug": slug, "name": name}
    if not cities:
        raise DivarError(502, "ساختار فهرست شهرهای دیوار شناخته نشد.")
    return sorted(cities.values(), key=lambda city: city["name"])


def _category_items(payload: Any) -> list[dict[str, Any]]:
    """Normalize nested or flat category asset responses into selectable paths."""
    if isinstance(payload, dict):
        for key in ("categories", "category", "items", "data"):
            if key in payload:
                found = _category_items(payload[key])
                if found:
                    return found
        return []
    if not isinstance(payload, list):
        return []

    result: list[dict[str, Any]] = []

    def visit(nodes: list[Any], parents: list[str]) -> None:
        for item in nodes:
            if not isinstance(item, dict):
                continue
            slug = next((str(item[key]) for key in ("slug", "category_slug", "id", "value")
                         if item.get(key) is not None), "")
            name = next((str(item[key]) for key in ("display", "name", "title", "label")
                         if item.get(key)), "")
            path = parents + ([name] if name else [])
            if slug and name:
                result.append({"slug": slug, "name": name, "path": " / ".join(path),
                               "depth": len(parents)})
            children = next((item[key] for key in ("children", "subcategories", "sub_categories", "categories")
                             if isinstance(item.get(key), list)), [])
            visit(children, path)

    visit(payload, [])
    return result


def get_category_list() -> list[dict[str, Any]]:
    """Return Divar's documented category directory (including subcategories)."""
    try:
        headers = {}
        api_key = os.environ.get("DIVAR_KENAR_API_KEY", "").strip()
        if api_key:
            headers["x-api-key"] = api_key
        response = session.get(f"{KENAR_API}/category", headers=headers, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        if getattr(exc.response, "status_code", None) in {401, 403}:
            raise DivarError(502, "فهرست رسمی دسته‌بندی‌ها نیازمند کلید API کنار دیوار است؛ متغیر DIVAR_KENAR_API_KEY را تنظیم کن.") from exc
        raise DivarError(502, f"دریافت دسته‌بندی‌ها از دیوار ناموفق بود: {exc}") from exc
    except ValueError as exc:
        raise DivarError(502, "پاسخ دسته‌بندی‌های دیوار JSON معتبر نیست.") from exc
    categories = _category_items(payload)
    if not categories:
        raise DivarError(502, "ساختار فهرست دسته‌بندی‌های دیوار شناخته نشد.")
    return categories


def _city_id(city_slug: str) -> str:
    """Resolve the current city ID from Divar's own city directory."""
    try:
        response = session.get(f"{DIVAR_API}/places/cities", timeout=25)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise DivarError(502, f"Could not read Divar city directory: {exc}") from exc
    except ValueError as exc:
        raise DivarError(502, "Divar city directory returned invalid JSON") from exc

    for item in _walk_dicts(data):
        slugs = [item.get(k) for k in ("slug", "city_slug", "seo_slug", "name_en")]
        if city_slug.casefold() in [str(s).casefold() for s in slugs if s is not None]:
            for key in ("id", "city_id", "value"):
                if item.get(key) is not None:
                    return str(item[key])
    raise DivarError(404, f"City slug not found in Divar's city list: {city_slug}")


def get_district_list(city_slug: str) -> list[dict[str, str]]:
    """Return a city's public Divar neighborhood list with IDs used by web search."""
    city_id = _city_id(city_slug)
    try:
        response = session.get(f"{DIVAR_API}/places/cities/{city_id}/districts", timeout=25)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise DivarError(502, f"دریافت فهرست محله‌ها از دیوار ناموفق بود: {exc}") from exc
    except ValueError as exc:
        raise DivarError(502, "پاسخ فهرست محله‌های دیوار JSON معتبر نیست.") from exc

    items = payload
    if isinstance(payload, dict):
        items = next((payload[key] for key in ("districts", "items", "data")
                      if isinstance(payload.get(key), list)), [])
    districts: dict[str, dict[str, str]] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            district_id = next((str(item[key]) for key in ("id", "district_id", "value")
                                if item.get(key) is not None), "")
            name = next((str(item[key]) for key in ("name", "display", "name_persian", "title")
                         if item.get(key)), "")
            slug = next((str(item[key]) for key in ("slug", "second_slug") if item.get(key)), "")
            if district_id.isdigit() and name:
                districts[district_id] = {"id": district_id, "name": name, "slug": slug}
    if not districts:
        raise DivarError(502, "ساختار فهرست محله‌های دیوار شناخته نشد.")
    return sorted(districts.values(), key=lambda district: district["name"])


def _parse_divar_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"divar.ir", "www.divar.ir"}:
        raise DivarError(400, "url must be an https://divar.ir/s/{city}/{category} URL")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 3 or parts[0] != "s":
        raise DivarError(400, "Expected a category URL such as /s/mashhad/buy-residential")
    return parts[1], parts[2]


def _normalize_post(widget: dict[str, Any]) -> dict[str, Any] | None:
    data = widget.get("data") or {}
    action = data.get("action") or {}
    payload = action.get("payload") or {}
    web_info = payload.get("web_info") or {}
    token = payload.get("token") or widget.get("post_token") or data.get("token")
    if not token:
        return None
    return {
        "token": token,
        "title": data.get("title") or web_info.get("title"),
        "price": data.get("middle_description_text"),
        "subtitle": data.get("bottom_description_text"),
        "description": data.get("top_description_text"),
        "city": web_info.get("city_persian"),
        "district": web_info.get("district_persian"),
        "image_url": data.get("image_url"),
        "url": f"https://divar.ir/v/{token}",
    }


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _detail_sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sections = payload.get("sections") or []
    return sections if isinstance(sections, list) else []


def _listing_attributes(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, bool]]:
    attributes: dict[str, str] = {}
    amenities: dict[str, bool] = {}
    for section in _detail_sections(payload):
        for widget in section.get("widgets", []) or []:
            data = widget.get("data") or {}
            kind = str(data.get("@type", ""))
            if kind.endswith("GroupInfoRow"):
                for item in data.get("items", []) or []:
                    title, value = _clean_text(item.get("title")), _clean_text(item.get("value"))
                    if title and value:
                        attributes[title] = value
            elif kind.endswith("UnexpandableRowData"):
                title, value = _clean_text(data.get("title")), _clean_text(data.get("value"))
                if title and value:
                    attributes[title] = value
            elif kind.endswith("GroupFeatureRow"):
                for item in data.get("items", []) or []:
                    title = _clean_text(item.get("title"))
                    if title:
                        amenities[title] = bool(item.get("available", False))

    return attributes, amenities


def _attribute(attributes: dict[str, str], *labels: str) -> str | None:
    normalized = {re.sub(r"[\s‌\u200c]+", "", key).replace("ي", "ی").replace("ك", "ک"): value
                  for key, value in attributes.items()}
    for label in labels:
        key = re.sub(r"[\s‌\u200c]+", "", label).replace("ي", "ی").replace("ك", "ک")
        if key in normalized:
            return normalized[key]
    return None


def _price_number(value: Any) -> int | None:
    text = _clean_text(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    text = text.replace("٬", ",").replace("٫", ".")
    if any(word in text for word in ("توافقی", "رایگان", "مجانی")):
        return None
    matches = re.findall(r"([0-9][0-9,.]*)\s*(میلیارد|میلیون|هزار)?", text)
    if not matches:
        return None
    total = 0.0
    for number, unit in matches:
        try:
            amount = float(number.replace(",", ""))
        except ValueError:
            continue
        multiplier = {"میلیارد": 1_000_000_000, "میلیون": 1_000_000, "هزار": 1_000}.get(unit, 1)
        total += amount * multiplier
    return int(total) if total else None


def _description(payload: dict[str, Any]) -> str | None:
    for section in _detail_sections(payload):
        if section.get("section_name") == "DESCRIPTION":
            for widget in section.get("widgets", []) or []:
                data = widget.get("data") or {}
                if data.get("text"):
                    return _clean_text(data["text"])
    return None


def _enrich_ad(ad: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    attributes, amenities = _listing_attributes(payload)
    total_price_text = _attribute(attributes, "قیمت کل", "قیمت") or ad.get("price")
    price_total = _price_number(total_price_text)
    area_text = _attribute(attributes, "متراژ", "زیربنا", "مساحت")
    area_number = None
    if area_text:
        area_digits = area_text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        area_match = re.search(r"\d+(?:[.,]\d+)?", area_digits.replace("٬", ""))
        area_number = int(float(area_match.group().replace(",", "."))) if area_match else None
    price_per_meter_text = _attribute(attributes, "قیمت هر متر", "قیمت هر متر مربع", "قیمت متری")
    price_per_meter = _price_number(price_per_meter_text)
    if price_per_meter is None and price_total and area_number:
        price_per_meter = price_total // area_number
    if total_price_text:
        attributes.setdefault("قیمت کل", total_price_text)
    if price_per_meter_text:
        attributes.setdefault("قیمت هر متر مربع", price_per_meter_text)
    elif price_per_meter is not None:
        attributes.setdefault("قیمت هر متر مربع (محاسبه‌شده)", str(price_per_meter))
    ad.update({
        "description": _description(payload) or ad.get("description"),
        "location": ad.get("district") or ad.get("city"),
        "area": area_number,
        "year_built": _attribute(attributes, "ساخت", "سال ساخت", "سال بنا"),
        "total_price": price_total,
        "total_price_text": total_price_text,
        "price_per_meter": price_per_meter,
        "price_per_meter_text": price_per_meter_text,
        "rooms": _attribute(attributes, "اتاق", "تعداد خواب", "خواب"),
        "floor": _attribute(attributes, "طبقه"),
        "amenities": amenities,
        "attributes": attributes,
    })
    return ad


def _enrich_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=8) as executor:
        pending = {executor.submit(get_ad_details, ad["token"]): ad for ad in posts}
        for future in as_completed(pending):
            ad = pending[future]
            try:
                _enrich_ad(ad, future.result())
            except DivarError as exc:
                ad["detail_error"] = exc.detail
                ad.update({"location": ad.get("district") or ad.get("city"), "area": None,
                           "year_built": None, "total_price": _price_number(ad.get("price")),
                           "total_price_text": ad.get("price"), "price_per_meter": None,
                           "price_per_meter_text": None, "rooms": None, "floor": None,
                           "amenities": {}, "attributes": {}})
    return posts


def search_divar(city_slug: str, category_slug: str, limit: int, query_text: str = "",
                 district_ids: list[str] | None = None, batch_size: int | None = None,
                 pause_seconds: float = 1.0) -> list[dict[str, Any]]:
    city_id = _city_id(city_slug)
    # Divar's SEO route names are not always the enum used in its search payload.
    category_route = category_slug
    category_slug = {
        "buy-residential": "residential-sell",
        "rent-residential": "residential-rent",
    }.get(category_slug, category_slug)
    form_data = {"category": {"str": {"value": category_slug}}}
    if query_text.strip():
        form_data["query"] = {"str": {"value": query_text.strip()}}
    if district_ids:
        form_data["districts"] = {"repeated_string": {"value": [str(value) for value in district_ids]}}
    search_data = {
        "form_data": {"data": form_data},
        "server_payload": {
            "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
            "additional_form_data": {"data": {"sort": {"str": {"value": "sort_date"}}}},
        },
    }
    pagination: dict[str, Any] = {
        "@type": "type.googleapis.com/post_list.PaginationData",
        "page": 1,
        "layer_page": 1,
        "search_uid": str(uuid4()),
        "cumulative_widgets_count": 0,
    }
    results: list[dict[str, Any]] = []
    posts: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Divar usually returns 24 cards per page. Batched callers can pause after each enriched chunk.
    max_pages = max(6, (limit + 23) // 24 + 3)
    for _ in range(max_pages):
        if len(results) + len(posts) >= limit:
            break
        body = {
            "city_ids": [city_id],
            "disable_recommendation": False,
            "map_state": {"camera_info": {"bbox": {}}},
            "search_data": search_data,
            "pagination_data": pagination,
            "user_selected_location": {"places": [{"place_id": city_id}]},
            "previous_user_selected_location": {"places": []},
        }
        try:
            response = session.post(
                f"{DIVAR_API}/postlist/w/search",
                data=json.dumps(body, ensure_ascii=False),
                headers={"Referer": f"https://divar.ir/s/{city_slug}/{category_route}"
                                  + (f"?q={requests.utils.quote(query_text.strip())}" if query_text.strip() else "")},
                timeout=30,
            )
            if not response.ok:
                raise DivarError(
                    502,
                    f"Divar returned HTTP {response.status_code}: {response.text[:1000]}",
                )
            payload = response.json()
        except DivarError:
            raise
        except requests.RequestException as exc:
            raise DivarError(502, f"Divar search request failed: {exc}") from exc
        except ValueError as exc:
            raise DivarError(502, "Divar search endpoint returned invalid JSON") from exc

        for widget in payload.get("list_widgets", []):
            if widget.get("widget_type") != "POST_ROW":
                continue
            post = _normalize_post(widget)
            if post and post["token"] not in seen:
                seen.add(post["token"])
                post["category"] = category_slug
                posts.append(post)
                if len(results) + len(posts) >= limit:
                    break

        if batch_size and batch_size > 0:
            while len(posts) >= batch_size and len(results) < limit:
                batch = posts[:batch_size]
                del posts[:batch_size]
                results.extend(_enrich_posts(batch))
                if len(results) < limit and pause_seconds > 0:
                    time.sleep(pause_seconds)
        if len(results) + len(posts) >= limit:
            break

        next_data = (payload.get("pagination") or {}).get("data")
        has_next = (payload.get("pagination") or {}).get("has_next_page", bool(next_data))
        if not next_data or not has_next:
            break
        pagination = next_data

    if batch_size and batch_size > 0:
        while posts and len(results) < limit:
            batch = posts[:min(batch_size, limit - len(results))]
            del posts[:len(batch)]
            results.extend(_enrich_posts(batch))
            if posts and len(results) < limit and pause_seconds > 0:
                time.sleep(pause_seconds)
        return results[:limit]
    return _enrich_posts(posts[:limit])


def get_ad_details(token: str) -> dict[str, Any]:
    """Fetch one public listing's detail JSON from Divar's web endpoint."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", token):
        raise DivarError(400, "Invalid listing token")
    try:
        response = _detail_session().get(f"{DIVAR_API}/posts-v2/web/{token}", timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise DivarError(502, f"Divar detail request failed: {exc}") from exc
    except ValueError as exc:
        raise DivarError(502, "Divar detail endpoint returned invalid JSON") from exc
