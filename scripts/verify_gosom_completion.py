import json
import re
from pathlib import Path

STATUS_PATH = Path("gosom_status.json")
LOG_PATH = Path("gosom_container.log")
QUERIES_PATH = Path("queries.txt")
RESULTS_PATH = Path("results.json")


def main():
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    log = LOG_PATH.read_text(encoding="utf-8", errors="replace") if LOG_PATH.exists() else ""
    queries = [
        line.strip()
        for line in QUERIES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if QUERIES_PATH.exists() else []

    lines = log.splitlines()
    successful_search_jobs = [
        line for line in lines
        if re.search(r'"status"\s*:\s*"success"', line) and "https://www.google.com/maps/search/" in line
    ]
    failed_jobs = [
        line for line in lines
        if re.search(r'"status"\s*:\s*"failed"', line)
    ]
    failed_job_counts = [
        int(value)
        for value in re.findall(r'"numOfJobsFailed"\s*:\s*(\d+)', log)
    ]

    successful_queries = [
        query for query in queries
        if any(query in line for line in successful_search_jobs)
    ]
    all_inputs_succeeded = bool(queries) and len(successful_queries) == len(queries)
    no_failed_jobs = not failed_jobs and (not failed_job_counts or max(failed_job_counts) == 0)
    output_present = RESULTS_PATH.is_file() and RESULTS_PATH.stat().st_size > 0
    scrapemate_exited = bool(re.search(r'"message"\s*:\s*"scrapemate exited"', log))
    normal_process_exit = int(status.get("exit_code", 1)) == 0 and not status.get("timed_out", False)
    watchdog_triggered = bool(status.get("shutdown_watchdog_triggered", False))

    # Forced container cleanup is not a scrape failure when Scrapemate exited,
    # every requested search URL has a successful job record, no failed jobs
    # were recorded, and a non-empty result file was preserved.
    verified = (
        output_present
        and all_inputs_succeeded
        and no_failed_jobs
        and (
            normal_process_exit
            or (watchdog_triggered and scrapemate_exited)
        )
    )

    status["scrape_completed"] = verified
    status["input_urls_expected"] = len(queries)
    status["input_urls_succeeded"] = len(successful_queries)
    status["failed_job_log_entries"] = len(failed_jobs)
    status["shutdown_cleanup_forced"] = watchdog_triggered and verified
    STATUS_PATH.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    print(
        "Gosom completion verification: "
        f"scrape_completed={verified} | "
        f"input_urls={len(successful_queries)}/{len(queries)} | "
        f"failed_job_entries={len(failed_jobs)} | "
        f"result_file_present={output_present} | "
        f"watchdog_triggered={watchdog_triggered}"
    )


if __name__ == "__main__":
    main()
