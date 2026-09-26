import json, os, re, sys
from supabase import create_client
sb=create_client(os.environ["SUPABASE_URL"],os.environ["SUPABASE_SERVICE_ROLE_KEY"])
batch_id=json.load(open("claimed.json")).get("batch_id")
if not batch_id: sys.exit(0)
data=json.load(open("results.json"))
items=data if isinstance(data,list) else (data.get("results") or data.get("places") or []) if isinstance(data,dict) else []
valid=[]; rejected=0
for x in items:
    if not isinstance(x,dict): rejected+=1; continue
    pid=x.get("place_id") or x.get("placeId")
    if not isinstance(pid,str) or not re.fullmatch(r"ChI[A-Za-z0-9_-]{10,}",pid): rejected+=1; continue
    website=x.get("website") or x.get("website_url")
    if isinstance(website,str) and website.startswith("https://www.google.com/"): website=None
    valid.append({"placeId":pid,"businessName":x.get("title") or x.get("name"),"address":x.get("address") or x.get("complete_address"),"phone":x.get("phone") or x.get("phone_number"),"website":website,"rating":x.get("review_rating") or x.get("rating"),"reviewCount":x.get("review_count") or x.get("reviews_count"),"latitude":x.get("latitude"),"longitude":x.get("longitude"),"googleMapsUrl":x.get("link") or x.get("google_maps_url") or x.get("url"),"openingHoursRaw":x.get("open_hours") or x.get("hours"),"categories":x.get("categories") or ([x["category"]] if x.get("category") else []),"emails":x.get("emails") or [],"raw":x})
errors=0
for p in valid:
    try: sb.rpc("ingest_google_place",{"p_batch_id":batch_id,"p_payload":p}).execute()
    except Exception as e: errors+=1; print(f"INGEST ERROR {p['placeId']}: {e}")
stats={"valid":len(valid),"rejected":rejected,"ingested":len(valid)-errors,"errors":errors}
json.dump(stats,open("ingest_stats.json","w"),indent=2); print(json.dumps(stats))
sys.exit(3 if errors else (2 if not valid else 0))
