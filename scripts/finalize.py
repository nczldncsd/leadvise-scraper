import json, os
from datetime import datetime, timezone
from supabase import create_client
sb=create_client(os.environ["SUPABASE_URL"],os.environ["SUPABASE_SERVICE_ROLE_KEY"])
d=json.load(open("claimed.json")); batch=d.get("batch_id"); rows=d.get("rows",[])
if not batch: raise SystemExit(0)
sb.table("grid_queue").update({"status":"completed","processed_at":datetime.now(timezone.utc).isoformat()}).in_("id",[r["id"] for r in rows]).execute()
s=json.load(open("ingest_stats.json"))
sb.table("scrape_batches").update({"status":"completed","grid_urls_completed":len(rows),"grid_urls_failed":0,"records_found":s["valid"],"records_inserted":s["ingested"],"records_rejected":s["rejected"],"ingestion_errors":s["errors"]}).eq("id",batch).execute()
print(f"Finalized batch {batch}")
