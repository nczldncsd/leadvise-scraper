import json, os, re, sys
from urllib.parse import urlparse
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
batch_id = json.load(open("claimed.json")).get("batch_id")
if not batch_id:
    sys.exit(0)

# gosom may write JSON Lines (one object per line) rather than one JSON array.
raw = open("results.json", encoding="utf-8").read().strip()
parse_errors = 0
try:
    data = json.loads(raw)
    items = data if isinstance(data, list) else (data.get("results") or data.get("places") or []) if isinstance(data, dict) else []
except json.JSONDecodeError:
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                items.append(obj)
        except json.JSONDecodeError:
            parse_errors += 1

raw_business_objects = len(items)
valid = []
rejected = 0

def clean_website(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        host = urlparse(value).netloc.lower().split(":")[0]
    except Exception:
        return None
    bad_hosts = (
        "google.com",
        "googleusercontent.com",
        "googleapis.com",
        "gstatic.com",
    )
    if any(host == h or host.endswith("." + h) for h in bad_hosts):
        return None
    return value

for x in items:
    if not isinstance(x, dict):
        rejected += 1
        continue

    pid = x.get("place_id") or x.get("placeId")
    if not isinstance(pid, str) or not re.fullmatch(r"ChI[A-Za-z0-9_-]{10,}", pid):
        rejected += 1
        continue

    # Gosom's actual website field is web_site.
    # web_site takes precedence so a stale/different mapper field cannot win.
    website_raw = x.get("web_site")
    website = clean_website(
        website_raw
        if website_raw is not None
        else (x.get("website") or x.get("website_url"))
    )

    complete = x.get("complete_address") or {}
    raw_longitude = x.get("longitude")
    if raw_longitude is None:
        raw_longitude = x.get("longtitude")

    location = {
        "lat": x.get("latitude"),
        "lng": raw_longitude,
    }

    valid.append({
        "placeId": pid,
        "title": x.get("title") or x.get("name"),
        "address": x.get("address"),
        "city": complete.get("city") if isinstance(complete, dict) else None,
        "state": complete.get("state") if isinstance(complete, dict) else None,
        "countryCode": complete.get("country") if isinstance(complete, dict) else None,
        "postalCode": complete.get("postal_code") if isinstance(complete, dict) else None,
        "location": location,
        "website": website,
        "websiteFoundInRaw": website_raw is not None,
        "phone": x.get("phone") or x.get("phone_number"),
        "phoneUnformatted": x.get("phone_unformatted") or x.get("phone"),
        "normalizedPhone": None,
        "normalizedDomain": None,
        "url": x.get("link") or x.get("google_maps_url") or x.get("url"),
        "openingHoursFormatted": json.dumps(x.get("open_hours")) if x.get("open_hours") else None,
        "openingHoursRaw": x.get("open_hours") or x.get("hours"),
        "totalScore": x.get("review_rating") if x.get("review_rating") is not None else x.get("rating"),
        "reviewsCount": x.get("review_count") if x.get("review_count") is not None else x.get("reviews_count"),
        "categories": x.get("categories") or ([x["category"]] if x.get("category") else []),
        "rank": x.get("rank"),
        "raw": x
    })

unique_place_ids = {p["placeId"] for p in valid}
duplicate_objects = max(0, len(valid) - len(unique_place_ids))

errors = 0
for p in valid:
    try:
        sb.rpc("ingest_google_place", {
            "p_batch_id": batch_id,
            "p_payload": p
        }).execute()
    except Exception as e:
        errors += 1
        print(f"INGEST ERROR {p['placeId']}: {e}")

stats = {
    "raw_business_objects": raw_business_objects,
    "unique_businesses": len(unique_place_ids),
    "duplicate_objects": duplicate_objects,
    "extraction_errors": rejected + parse_errors,
    "valid": len(valid),
    "rejected": rejected,
    "ingested": len(valid) - errors,
    "errors": errors,
    "network_responses": 0,
}

json.dump(stats, open("ingest_stats.json", "w"), indent=2)
print(json.dumps(stats))
sys.exit(3 if errors else (2 if not valid else 0))
