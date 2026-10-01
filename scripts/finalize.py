import json, os
from datetime import datetime, timezone
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
d = json.load(open("claimed.json"))
batch = d.get("batch_id")
rows = d.get("rows", [])
if not batch:
    raise SystemExit(0)

run_status = os.getenv("RUN_STATUS", "success")

gosom_failed = False
gosom_exit = 0
gosom_timed_out = False
if os.path.exists("gosom_status.json"):
    try:
        gosom = json.load(open("gosom_status.json"))
        gosom_exit = int(gosom.get("exit_code", 0))
        gosom_timed_out = bool(gosom.get("timed_out", False))
        gosom_failed = gosom_exit != 0
    except Exception:
        gosom_failed = True
else:
    # Older/manual runs may not have this file.
    gosom_failed = False

failed = run_status != "success" or gosom_failed
now = datetime.now(timezone.utc).isoformat()
ids = [r["id"] for r in rows]

stats = {}
if os.path.exists("ingest_stats.json"):
    try:
        stats = json.load(open("ingest_stats.json"))
    except Exception:
        stats = {}

raw_business_objects = int(stats.get("raw_business_objects", 0))
unique_businesses = int(stats.get("unique_businesses", 0))
duplicate_objects = int(stats.get("duplicate_objects", 0))
extraction_errors = int(stats.get("extraction_errors", 0))
network_responses = int(stats.get("network_responses", 0))
ingested = int(stats.get("ingested", 0))
inserted = int(stats.get("records_inserted", 0))
updated = int(stats.get("records_updated", 0))
rejected = int(stats.get("rejected", 0))
errors = int(stats.get("errors", 0))

batch_metrics = {
    "completed_at": now,
    "records_found": unique_businesses,
    "records_inserted": inserted,
    "records_updated": updated,
    "records_rejected": rejected,
    "network_responses": network_responses,
    "raw_business_objects": raw_business_objects,
    "unique_businesses": unique_businesses,
    "duplicate_objects": duplicate_objects,
    "extraction_errors": extraction_errors,
    "ingestion_errors": errors,
}

if failed:
    error_text = os.getenv("FAILURE_REASON", "GitHub Actions job failed")
    sb.table("grid_queue").update({
        "status": "failed",
        "processed_at": now,
        "last_error": error_text,
        "last_error_at": now,
        "last_heartbeat_at": now,
    }).in_("id", ids).execute()

    sb.table("scrape_batches").update({
        **batch_metrics,
        "status": "failed",
        "grid_urls_completed": 0,
        "grid_urls_failed": len(rows),
    }).eq("id", batch).execute()
    print(f"Marked batch {batch} failed | grids={len(rows)}")
    raise SystemExit(0)

sb.table("grid_queue").update({
    "status": "completed",
    "processed_at": now,
    "last_heartbeat_at": now,
    "last_error": None,
}).in_("id", ids).execute()

sb.table("scrape_batches").update({
    **batch_metrics,
    "status": "completed",
    "grid_urls_completed": len(rows),
    "grid_urls_failed": 0,
}).eq("id", batch).execute()

print(
    f"Finalized batch {batch} | grids={len(rows)} | "
    f"unique={unique_businesses} | inserted={inserted} | updated={updated}"
)
