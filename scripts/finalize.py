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
failed = run_status != "success"
now = datetime.now(timezone.utc).isoformat()
ids = [r["id"] for r in rows]

stats = {}
if os.path.exists("ingest_stats.json"):
    try:
        stats = json.load(open("ingest_stats.json"))
    except Exception:
        stats = {}

valid = int(stats.get("valid", 0))
ingested = int(stats.get("ingested", 0))
rejected = int(stats.get("rejected", 0))
errors = int(stats.get("errors", 0))

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
        "status": "failed",
        "grid_urls_completed": 0,
        "grid_urls_failed": len(rows),
        "records_found": valid,
        "records_inserted": ingested,
        "records_rejected": rejected,
        "ingestion_errors": errors,
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
    "status": "completed",
    "grid_urls_completed": len(rows),
    "grid_urls_failed": 0,
    "records_found": valid,
    "records_inserted": ingested,
    "records_rejected": rejected,
    "ingestion_errors": errors,
}).eq("id", batch).execute()

print(f"Finalized batch {batch} | grids={len(rows)}")
