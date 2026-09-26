import json, os, sys
from datetime import datetime, timedelta, timezone
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
max_urls = int(os.getenv("MAX_URLS", "20"))
now = datetime.now(timezone.utc)
cutoff = (now - timedelta(hours=2)).isoformat()

sb.table("grid_queue").update({"status":"pending","scrape_batch_id":None,"last_error":"Recovered stale processing claim"}).eq("status","processing").lt("last_heartbeat_at",cutoff).execute()
rows = sb.table("grid_queue").select("id,url,keyword,location_name,target_level,state,latitude,longitude").eq("status","pending").order("created_at").limit(max_urls).execute().data or []
if not rows:
    open("queries.txt","w").write("")
    json.dump({"batch_id":None,"rows":[]},open("claimed.json","w"),indent=2)
    print("No pending URLs")
    sys.exit(0)
batch = sb.table("scrape_batches").insert({"source":"google_maps_gosom","status":"started","records_found":0,"records_inserted":0,"records_updated":0,"records_rejected":0,"grid_urls_requested":len(rows)}).execute().data[0]
batch_id = batch["id"]
ids = [r["id"] for r in rows]
sb.table("grid_queue").update({"status":"processing","scrape_batch_id":batch_id,"processing_started_at":now.isoformat(),"last_heartbeat_at":now.isoformat(),"attempt_count":1}).in_("id",ids).execute()
open("queries.txt","w").write("\n".join(r["url"] for r in rows)+"\n")
json.dump({"batch_id":batch_id,"rows":rows},open("claimed.json","w"),indent=2)
print(f"Claimed {len(rows)} URLs | batch={batch_id}")
