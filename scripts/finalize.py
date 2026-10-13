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
gosom_exit = 0
gosom_timed_out = False
gosom_status_present = os.path.exists("gosom_status.json")
gosom_failed = False

if gosom_status_present:
    try:
        gosom = json.load(open("gosom_status.json"))
        gosom_exit = int(gosom.get("exit_code", 0))
        gosom_timed_out = bool(gosom.get("timed_out", False))
        gosom_failed = gosom_exit != 0 or gosom_timed_out
    except (OSError, ValueError, TypeError):
        gosom_failed = True
else:
    # Without a scraper status file, do not claim that the URLs completed.
    gosom_failed = True

now = datetime.now(timezone.utc).isoformat()
ids = [r["id"] for r in rows]

stats = {}
stats_present = os.path.exists("ingest_stats.json")
if stats_present:
    try:
        stats = json.load(open("ingest_stats.json"))
        if not isinstance(stats, dict):
            stats = {}
            stats_present = False
    except (OSError, ValueError, TypeError):
        stats = {}
        stats_present = False

raw_business_objects = int(stats.get("raw_business_objects", 0) or 0)
unique_businesses = int(stats.get("unique_businesses", 0) or 0)
duplicate_objects = int(stats.get("duplicate_objects", 0) or 0)
extraction_errors = int(stats.get("extraction_errors", 0) or 0)
network_responses = int(stats.get("network_responses", 0) or 0)
ingested = int(stats.get("ingested", 0) or 0)
inserted = int(stats.get("records_inserted", 0) or 0)
updated = int(stats.get("records_updated", 0) or 0)
rejected = int(stats.get("rejected", 0) or 0)
errors = int(stats.get("errors", 0) or 0)

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

# A batch is complete only when the scraper exited normally and ingestion
# produced valid statistics without errors. A timeout is not proof that every
# URL failed, and a result file is not proof that every URL finished.
fully_completed = (
    run_status == "success"
    and gosom_status_present
    and not gosom_failed
    and stats_present
    and errors == 0
    and extraction_errors == 0
    and unique_businesses > 0
    and ingested > 0
)

has_persisted_results = ingested > 0 or inserted > 0 or updated > 0
if fully_completed:
    batch_status = "completed"
    error_text = None
elif has_persisted_results:
    batch_status = "partial"
    if gosom_timed_out:
        error_text = (
            f"Gosom timed out after its configured limit (exit code {gosom_exit}). "
            f"{ingested} business records were ingested, but per-URL completion "
            "cannot be confirmed. Grid URLs have been returned to pending for retry."
        )
    elif gosom_failed:
        error_text = (
            f"Gosom did not finish successfully (exit code {gosom_exit}). "
            f"{ingested} business records were ingested; grid URLs have been "
            "returned to pending because per-URL completion cannot be confirmed."
        )
    elif run_status != "success":
        error_text = (
            f"A workflow step failed (RUN_STATUS={run_status}). "
            f"{ingested} business records were ingested; grid URLs have been "
            "returned to pending for retry."
        )
    elif errors:
        error_text = (
            f"Ingestion reported {errors} error(s). Some business records were "
            "saved; grid URLs have been returned to pending for a safe retry."
        )
    else:
        error_text = (
            "The batch did not meet all completion checks. Some business records "
            "were saved; grid URLs have been returned to pending for retry."
        )
else:
    batch_status = "failed"
    if gosom_timed_out:
        error_text = (
            f"Gosom timed out after its configured limit (exit code {gosom_exit}) "
            "and no successful ingestion was recorded. Grid URLs have been "
            "returned to pending for retry."
        )
    elif gosom_failed:
        error_text = (
            f"Gosom did not finish successfully (exit code {gosom_exit}) or its "
            "status file was unavailable. Grid URLs have been returned to pending."
        )
    elif run_status != "success":
        error_text = (
            f"A workflow step failed (RUN_STATUS={run_status}) and no successful "
            "ingestion was recorded. Grid URLs have been returned to pending."
        )
    else:
        error_text = (
            "No successfully ingested business records were confirmed. "
            "Grid URLs have been returned to pending for retry."
        )

if fully_completed:
    sb.table("grid_queue").update({
        "status": "completed",
        "processed_at": now,
        "last_heartbeat_at": now,
        "last_error": None,
        "last_error_at": None,
    }).in_("id", ids).execute()
else:
    # Batch-level failures do not prove that every individual grid URL failed.
    # Keep the URLs retryable; only mark URLs failed when failure is known per URL.
    sb.table("grid_queue").update({
        "status": "pending",
        "processed_at": None,
        "last_error": error_text,
        "last_error_at": now,
        "last_heartbeat_at": now,
    }).in_("id", ids).execute()

sb.table("scrape_batches").update({
    **batch_metrics,
    "status": batch_status,
    "grid_urls_completed": len(rows) if fully_completed else 0,
    # Per-URL failures are not measured by the current Gosom output, so do not
    # falsely report every claimed URL as failed on a batch-level timeout.
    "grid_urls_failed": 0 if not fully_completed else 0,
}).eq("id", batch).execute()

print(
    f"Finalized batch {batch} | status={batch_status} | grids={len(rows)} | "
    f"unique={unique_businesses} | ingested={ingested} | "
    f"inserted={inserted} | updated={updated} | "
    f"grid_action={'completed' if fully_completed else 'requeued'}"
)
