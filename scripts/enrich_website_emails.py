import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from scrapy import Request, Spider
from scrapy.crawler import CrawlerProcess
from scrapy.linkextractors import LinkExtractor
from supabase import create_client


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
MAX_DOMAINS = max(1, int(os.getenv("MAX_DOMAINS", "50")))
MAX_PAGES_PER_DOMAIN = max(1, int(os.getenv("MAX_PAGES_PER_DOMAIN", "8")))
RETRY_NOT_FOUND = os.getenv("RETRY_NOT_FOUND", "false").lower() == "true"
RETRY_FAILED = os.getenv("RETRY_FAILED", "false").lower() == "true"
STALE_PROCESSING_HOURS = 3

# Conservative pattern for public business email discovery. Excludes URL paths,
# package versions (e.g. library@1.2.3), and other strings that merely contain @.
EMAIL_RE = re.compile(
    r"(?i)(?<![a-z0-9._%+/-])"
    r"([a-z0-9_%+-]+(?:\\.[a-z0-9_%+-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
    r"\\.[a-z]{2,})"
    r"(?![a-z0-9-])"
)
INVALID_EXAMPLES = {
    "example@example.com", "name@example.com", "email@example.com",
    "user@email.com", "user@website.com", "test@test.com",
}
PRIORITY_WORDS = ("contact", "about", "team", "service", "location", "quote", "estimate", "support", "get-in-touch")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_domain(value):
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlparse(raw).hostname or "").lower().strip(".")
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or host in {"localhost", "example.com"}:
        return None
    return host


def normalize_website(value):
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return raw


def clean_email(value):
    email = value.strip().strip(".,;:!?()[]{}<>\"'").lower()
    if not email or email in INVALID_EXAMPLES:
        return None
    if "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return None
    if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return None
    if email in INVALID_EXAMPLES:
        return None
    tld = domain.rsplit(".", 1)[-1]
    if not tld.isalpha() or len(tld) < 2:
        return None
    return email


def chunks(items, size=100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class WebsiteEmailSpider(Spider):
    name = "leadvise_website_email_extractor"

    def __init__(self, sites, output_path, max_pages=8, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sites = sites
        self.output_path = output_path
        self.max_pages = int(max_pages)
        self.results = {
            site["normalized_domain"]: {
                "emails": set(),
                "source_urls": set(),
                "pages_fetched": 0,
                "errors": [],
            }
            for site in sites
        }
        self.scheduled = {site["normalized_domain"]: 1 for site in sites}
        self.link_extractor = LinkExtractor(
            deny_extensions=[
                "7z", "avi", "css", "doc", "docx", "exe", "gif", "gz", "ico",
                "jpeg", "jpg", "js", "mp3", "mp4", "pdf", "png", "ppt", "pptx",
                "rar", "svg", "tar", "tgz", "wav", "webm", "webp", "xls", "xlsx",
                "zip",
            ],
            unique=True,
        )

    async def start(self):
        # Scrapy 2.13+ uses start() for initial requests. The dependency range
        # allows newer Scrapy versions where start_requests() is no longer called.
        for site in self.sites:
            yield Request(
                site["website"],
                callback=self.parse_page,
                errback=self.request_failed,
                meta={"domain_key": site["normalized_domain"]},
                dont_filter=True,
            )

    def parse_page(self, response):
        domain_key = response.meta["domain_key"]
        result = self.results[domain_key]

        # Do not collect data from a cross-domain redirect.
        if normalize_domain(response.url) != domain_key:
            result["errors"].append("Website redirected outside its normalized domain")
            return

        result["pages_fetched"] += 1
        result["source_urls"].add(response.url)

        for match in EMAIL_RE.findall(response.text or ""):
            email = clean_email(match)
            if email:
                result["emails"].add(email)

        for href in response.css("a::attr(href)").getall():
            if href.lower().startswith("mailto:"):
                for match in EMAIL_RE.findall(href[7:].split("?")[0]):
                    email = clean_email(match)
                    if email:
                        result["emails"].add(email)

        if self.scheduled[domain_key] >= self.max_pages:
            return

        links = self.link_extractor.extract_links(response)
        same_site = [
            link for link in links
            if normalize_domain(link.url) == domain_key
            and urlparse(link.url).scheme in {"http", "https"}
        ]
        same_site.sort(
            key=lambda link: (
                0 if any(word in (link.url + " " + link.text).lower() for word in PRIORITY_WORDS) else 1,
                link.url,
            )
        )

        seen = set()
        for link in same_site:
            if self.scheduled[domain_key] >= self.max_pages:
                break
            if link.url in seen:
                continue
            seen.add(link.url)
            self.scheduled[domain_key] += 1
            yield Request(
                link.url,
                callback=self.parse_page,
                errback=self.request_failed,
                meta={"domain_key": domain_key},
            )

    def request_failed(self, failure):
        domain_key = failure.request.meta.get("domain_key")
        if domain_key in self.results:
            self.results[domain_key]["errors"].append(str(failure.value)[:300])

    def closed(self, reason):
        serializable = {}
        for domain, result in self.results.items():
            serializable[domain] = {
                "emails": sorted(result["emails"]),
                "source_urls": sorted(result["source_urls"]),
                "pages_fetched": result["pages_fetched"],
                "errors": result["errors"],
                "crawl_reason": reason,
            }
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)


def load_candidates(sb):
    qualification_rows = sb.table("qualification").select("business_id").eq(
        "absolute_gate", "passed"
    ).execute().data or []
    business_ids = list(dict.fromkeys(
        row["business_id"] for row in qualification_rows if row.get("business_id")
    ))

    candidates = []
    for batch in chunks(business_ids, 100):
        response = sb.table("businesses").select(
            "id,business_name,website,normalized_domain,email,emails"
        ).in_("id", batch).execute()
        candidates.extend(response.data or [])

    grouped = {}
    for business in candidates:
        website = normalize_website(business.get("website"))
        if not website:
            continue

        existing_emails = business.get("emails") or []
        if isinstance(existing_emails, str):
            existing_emails = [existing_emails]
        if any(str(e).strip() for e in existing_emails) or str(business.get("email") or "").strip():
            continue

        domain = normalize_domain(website) or normalize_domain(business.get("normalized_domain"))
        if not domain:
            continue

        business["normalized_domain"] = domain
        grouped.setdefault(domain, {"normalized_domain": domain, "website": website, "businesses": []})
        grouped[domain]["businesses"].append(business)

    return grouped


def load_tracking_rows(sb, domains):
    tracking = {}
    for batch in chunks(list(domains), 100):
        response = sb.table("website_email_scrapes").select(
            "normalized_domain,status,emails,source_urls,attempt_count,last_attempt_at,last_error"
        ).in_("normalized_domain", batch).execute()
        for row in response.data or []:
            tracking[row["normalized_domain"]] = row
    return tracking


def should_process(row, retry_not_found, retry_failed):
    if not row:
        return True
    status = row.get("status")
    if status == "found":
        return False
    if status == "not_found" and not retry_not_found:
        return False
    if status == "failed" and not retry_failed:
        return False
    if status == "processing":
        attempted = row.get("last_attempt_at")
        try:
            attempted_dt = datetime.fromisoformat(attempted.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - attempted_dt < timedelta(hours=STALE_PROCESSING_HOURS):
                return False
        except (ValueError, AttributeError):
            pass
    # Failed domains are skipped unless retry_failed is explicitly enabled.
    # Stale processing rows remain eligible so interrupted runs can recover.
    return True


def mark_processing(sb, site, previous):
    attempt_count = int((previous or {}).get("attempt_count") or 0) + 1
    payload = {
        "normalized_domain": site["normalized_domain"],
        "website": site["website"],
        "status": "processing",
        "attempt_count": attempt_count,
        "last_attempt_at": now_iso(),
        "last_error": None,
        "updated_at": now_iso(),
        "emails": (previous or {}).get("emails") or [],
        "source_urls": (previous or {}).get("source_urls") or [],
    }
    sb.table("website_email_scrapes").upsert(payload, on_conflict="normalized_domain").execute()
    return attempt_count


def update_business_emails(sb, site, found_emails):
    for business in site["businesses"]:
        business_id = business["id"]
        existing = business.get("emails") or []
        if isinstance(existing, str):
            existing = [existing]
        merged = sorted({str(e).strip().lower() for e in existing if str(e).strip()} | set(found_emails))
        update = {
            "emails": merged,
            "normalized_domain": site["normalized_domain"],
            "updated_at": now_iso(),
        }
        if not str(business.get("email") or "").strip() and merged:
            update["email"] = merged[0]
        sb.table("businesses").update(update).eq("id", business_id).execute()
        # Recalculate the existing absolute qualification/email-format fields.
        sb.rpc("refresh_business_qualification", {"p_business_id": business_id}).execute()


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    grouped = load_candidates(sb)
    domains = list(grouped.keys())
    tracking = load_tracking_rows(sb, domains)

    eligible = []
    skipped_found = skipped_not_found = skipped_failed = skipped_processing = 0
    synced_from_existing = 0
    for domain, site in grouped.items():
        row = tracking.get(domain)
        if (row or {}).get("status") == "found":
            # Reuse a prior domain result for newly added businesses without recrawling.
            saved_emails = sorted({
                email for raw in ((row or {}).get("emails") or [])
                if (email := clean_email(str(raw)))
            })
            if saved_emails:
                update_business_emails(sb, site, saved_emails)
                synced_from_existing += len(site["businesses"])
        if not should_process(row, RETRY_NOT_FOUND, RETRY_FAILED):
            status = (row or {}).get("status")
            if status == "found":
                skipped_found += 1
            elif status == "not_found":
                skipped_not_found += 1
            elif status == "failed":
                skipped_failed += 1
            else:
                skipped_processing += 1
            continue
        eligible.append(site)

    eligible = eligible[:MAX_DOMAINS]
    print(
        f"Absolute-qualified businesses grouped by domain: {len(grouped)} | "
        f"eligible domains: {len(eligible)} | limit: {MAX_DOMAINS} | "
        f"skipped found: {skipped_found} | synced from saved domain results: {synced_from_existing} | "
        f"skipped no-email: {skipped_not_found} | skipped failed: {skipped_failed} | "
        f"skipped active processing: {skipped_processing}"
    )

    if not eligible:
        print("No eligible website domains to process.")
        with open("email_crawl_results.json", "w", encoding="utf-8") as f:
            json.dump({}, f)
        return

    attempts = {}
    sites_for_crawler = []
    for site in eligible:
        previous = tracking.get(site["normalized_domain"])
        attempts[site["normalized_domain"]] = mark_processing(sb, site, previous)
        # Keep the normalized host on businesses for future runs.
        for business in site["businesses"]:
            if business.get("normalized_domain") != site["normalized_domain"]:
                sb.table("businesses").update({
                    "normalized_domain": site["normalized_domain"],
                    "updated_at": now_iso(),
                }).eq("id", business["id"]).execute()
        sites_for_crawler.append({
            "normalized_domain": site["normalized_domain"],
            "website": site["website"],
        })

    process = CrawlerProcess(settings={
        "LOG_LEVEL": "WARNING",
        "ROBOTSTXT_OBEY": True,
        "USER_AGENT": "LeadViseEmailExtractor/1.0 (+public business contact discovery)",
        "DOWNLOAD_TIMEOUT": 15,
        "RETRY_TIMES": 1,
        "CONCURRENT_REQUESTS": 16,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.2,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 0.2,
        "AUTOTHROTTLE_MAX_DELAY": 2.0,
        "TELNETCONSOLE_ENABLED": False,
        "LOGSTATS_INTERVAL": 0,
    })
    process.crawl(
        WebsiteEmailSpider,
        sites=sites_for_crawler,
        output_path="email_crawl_results.json",
        max_pages=MAX_PAGES_PER_DOMAIN,
    )
    process.start()

    try:
        with open("email_crawl_results.json", encoding="utf-8") as f:
            results = json.load(f)
    except (OSError, json.JSONDecodeError):
        results = {}

    summary = {"domains_processed": len(sites_for_crawler), "found": 0, "not_found": 0, "failed": 0, "emails_found": 0}
    for site in eligible:
        domain = site["normalized_domain"]
        result = results.get(domain, {})
        found_emails = sorted({
            email for raw in result.get("emails", [])
            if (email := clean_email(str(raw)))
        })
        source_urls = result.get("source_urls", [])
        pages_fetched = int(result.get("pages_fetched") or 0)
        errors = result.get("errors") or []
        attempt_count = attempts[domain]

        if found_emails:
            status = "found"
            last_error = None
            summary["found"] += 1
            summary["emails_found"] += len(found_emails)
            update_business_emails(sb, site, found_emails)
        elif pages_fetched > 0 and not errors:
            status = "not_found"
            last_error = None
            summary["not_found"] += 1
        else:
            status = "failed"
            last_error = "; ".join(errors[:3])[:1000] or "Crawler returned no pages and no diagnostic error."
            summary["failed"] += 1

        sb.table("website_email_scrapes").upsert({
            "normalized_domain": domain,
            "website": site["website"],
            "status": status,
            "emails": found_emails,
            "source_urls": source_urls,
            "attempt_count": attempt_count,
            "last_attempt_at": now_iso(),
            "last_success_at": now_iso() if status == "found" else None,
            "last_error": last_error,
            "updated_at": now_iso(),
        }, on_conflict="normalized_domain").execute()

        print(
            f"{domain}: status={status}, pages={pages_fetched}, "
            f"emails={len(found_emails)}, businesses_updated={len(site['businesses']) if found_emails else 0}"
        )

    with open("email_extraction_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("EMAIL EXTRACTION SUMMARY " + json.dumps(summary))


if __name__ == "__main__":
    main()
