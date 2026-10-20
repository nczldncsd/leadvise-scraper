# LeadVise Scraper

A GitHub Actions-based lead collection pipeline for LeadVise. It generates Google Maps search grids for target US locations, runs [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper), ingests and deduplicates business records in Supabase, and can extract publicly listed business email addresses from qualified businesses' websites.

**Repository:** https://github.com/nczldncsd/leadvise-scraper

## What this repository does

- Seeds Google Maps search URLs for target locations into the Supabase `grid_queue`.
- Runs gosom against queued URLs through GitHub Actions.
- Validates Google Place IDs and normalizes scraped fields before ingestion.
- Uses Supabase's `ingest_google_place` RPC to insert or update business records and refresh qualification checks.
- Tracks scraper batches and requeues grid URLs when a run cannot be confirmed as complete.
- Extracts public email addresses from websites belonging to businesses that passed absolute qualification.
- Tracks email extraction attempts by normalized domain, including successful, unsuccessful, and failed attempts.

This repository does **not** send cold emails, verify email deliverability, or perform the dynamic website research/qualification process.

## Pipeline overview

```text
data/target_locations.csv
        |
        v
Seed Grid Queue (manual GitHub Action)
        |
        v
Supabase: grid_queue
        |
        v
Claim pending URLs + create scrape batch
        |
        v
gosom/google-maps-scraper (Docker)
        |
        v
Parse, validate, deduplicate results
        |
        v
Supabase: businesses + qualification
        |
        v
Finalize batch and complete/requeue grid URLs

Separate workflow:
Absolute-qualified businesses with websites
        |
        v
Group by normalized domain and check attempt history
        |
        v
Crawl homepage + selected same-domain pages
        |
        v
Save emails to businesses.email / businesses.emails
        |
        v
Record attempt in website_email_scrapes
```

## Repository structure

```text
.
├── .github/
│   └── workflows/
│       ├── scrape.yml
│       ├── seed-grid.yml
│       ├── test-gosom.yml
│       └── website-email-extraction.yml
├── data/
│   └── target_locations.csv
├── scripts/
│   ├── claim_urls.py
│   ├── enrich_website_emails.py
│   ├── finalize.py
│   ├── ingest.py
│   └── seed_grid_from_database.py
├── supabase/
│   └── migrations/
│       └── 20261020_website_email_scrapes.sql
├── requirements.txt
└── README.md
```

### Main files

| File | Responsibility |
|---|---|
| `data/target_locations.csv` | Target locations, grid spacing, zoom, target level, and seeding status |
| `scripts/seed_grid_from_database.py` | Geocodes location boundaries, generates polygon-contained grid points, creates Google Maps URLs, and inserts new URLs into Supabase |
| `scripts/claim_urls.py` | Claims pending grid URLs, creates a scrape batch, and writes the URLs to `queries.txt` |
| `scripts/ingest.py` | Parses gosom output, validates Place IDs, normalizes business data, and calls the Supabase ingestion RPC |
| `scripts/finalize.py` | Records batch metrics and marks URLs complete only when the run passes completion checks; otherwise requeues them |
| `scripts/enrich_website_emails.py` | Crawls qualified business websites and records extracted emails and crawl attempts |
| `supabase/migrations/20261020_website_email_scrapes.sql` | Defines the domain-level website email extraction tracking table |

## Requirements

- A GitHub repository with GitHub Actions enabled.
- A Supabase project containing the LeadVise schema and functions used by the scripts.
- The `grid_queue`, `scrape_batches`, `businesses`, and `qualification` tables.
- The `ingest_google_place` and `refresh_business_qualification` Supabase functions.
- GitHub repository secrets described below.
- Docker support in the GitHub Actions runner for gosom workflows.

The GitHub Actions workflows install their Python dependencies. The grid seeder also installs OSMnx and Shapely directly in its workflow.

## GitHub secrets

Open **GitHub → repository → Settings → Secrets and variables → Actions → Repository secrets** and configure:

| Secret | Purpose |
|---|---|
| `SUPABASE_URL` | URL of the Supabase project |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side key used by the workflows to access Supabase |

Keep the service-role key private. It bypasses normal Row Level Security protections and must never be committed to the repository, placed in the CSV, or exposed in frontend code. The scripts use environment variables rather than hard-coded credentials.

The scraper script has a fallback Supabase URL, but the workflows should still use the `SUPABASE_URL` secret so the target project is explicit.

## Workflow 1: Seed the grid queue

**Workflow:** [Seed LeadVise Grid Queue](.github/workflows/seed-grid.yml)

This workflow is manually triggered. It reads pending rows from `data/target_locations.csv`, looks up the boundary for each location with OSMnx, creates a grid inside the boundary, and inserts new Google Maps URLs into Supabase's `grid_queue`.

### CSV inputs

The CSV currently uses these columns:

| Column | Meaning |
|---|---|
| `location_name` | Location name sent to OSMnx for boundary lookup |
| `State` | State label saved with each queued URL |
| `target_level` | Target geography level or grouping label |
| `step_meters` | Approximate spacing between grid points |
| `zoom` | Google Maps URL zoom value |
| `Status` | `Pending` locations are eligible for seeding; successful locations become `Seeded` |

Other existing columns are preserved when the CSV is rewritten.

### Grid generation logic

For each selected pending location:

1. OSMnx retrieves a geographic boundary polygon.
2. The script reads the polygon's bounding coordinates.
3. Latitude spacing is calculated as `step_meters / 111000`.
4. Longitude spacing is adjusted for latitude using `step_meters / (111000 × cos(latitude))`.
5. Candidate points are kept only when the polygon contains them.
6. Each point receives one keyword from a rotating list:
   - `Appliance Repair service`
   - `Refrigerator Repair Service`
   - `Washer & Dryer Repair Service`
   - `Dishwasher Repair Service`
7. A Google Maps search URL is created for each point.
8. URLs already present in `grid_queue` are skipped. New rows are inserted in batches of 100.

This is LeadVise's own polygon-aware grid generator. It is not gosom's built-in `-grid-bbox` mode: the seeder creates the individual Google Maps URLs before gosom runs.

### Run it

1. Open the repository's **Actions** tab.
2. Select **Seed LeadVise Grid Queue**.
3. Click **Run workflow**.
4. Set `max_locations`:
   - `2` is the current workflow default.
   - Use a positive number to process up to that many pending locations.
   - Use `0` to process all pending locations.
5. Start the workflow.

After a successful run, the workflow commits updated statuses in `data/target_locations.csv`. If boundary lookup fails, the affected location stays pending so it can be retried. Previously inserted URLs are skipped on the next attempt.

## Workflow 2: Google Maps scraping

**Workflow:** [LeadVise Google Maps Scraper](.github/workflows/scrape.yml)

This workflow runs on a schedule and can also be started manually. The current schedule is twice daily, at 11:34 UTC and 14:34 UTC. GitHub Actions schedules use UTC and may not start at the exact minute.

### Manual run

1. Open **Actions**.
2. Select **LeadVise Google Maps Scraper**.
3. Click **Run workflow**.
4. Set `max_urls`, the maximum number of pending grid URLs to claim. The current default is `10`.
5. Start the workflow.

### What happens during a run

1. `claim_urls.py` recovers stale processing claims older than two hours, selects pending URLs, creates a row in `scrape_batches`, marks the selected queue rows as processing, and writes their URLs to `queries.txt`.
2. gosom runs in Docker with JSON output, depth `10`, concurrency `4`, and a 30-minute process timeout. It exits after three minutes of inactivity.
3. `ingest.py` reads gosom's JSON or JSONL output, validates Google Place IDs, filters known Google-owned website URLs, normalizes the scraped fields, and calls `ingest_google_place` for each valid record.
4. The database ingestion function inserts or updates `businesses` records by `place_id` and refreshes qualification fields.
5. `finalize.py` records batch metrics. It marks the claimed URLs completed only if the completion checks pass. If the run times out, fails, or cannot be confirmed, the URLs are returned to pending for retry.
6. Diagnostics are uploaded as the `scraper-run` workflow artifact and retained for seven days.

### Scraper artifacts

The workflow can upload these files when they are produced:

- `results.json`: gosom results.
- `claimed.json`: claimed URLs and scrape batch ID.
- `queries.txt`: URLs passed to gosom.
- `ingest_stats.json`: ingestion counts and errors.
- `gosom_status.json` and `gosom_container_exit.txt`: gosom process diagnostics.

The artifact is useful for troubleshooting. A partial result file alone does not prove that all claimed URLs finished successfully.

## Workflow 3: Website email extraction

**Workflow:** [LeadVise Website Email Extraction](.github/workflows/website-email-extraction.yml)

This workflow is manually triggered. It reads businesses whose `qualification.absolute_gate = 'passed'`, selects businesses that have a website but no email in `businesses.email` or `businesses.emails`, and groups them by normalized domain. This avoids repeatedly crawling the same domain for multiple business records.

### Manual run

1. Open **Actions**.
2. Select **LeadVise Website Email Extraction**.
3. Click **Run workflow**.
4. Set `max_domains`. The default is `50` unique website domains.
5. Choose whether to enable `retry_not_found`.
6. Start the workflow.

The crawler visits each website's homepage and up to eight same-domain pages, prioritizing links that look like Contact, About, Team, Service, Location, Quote, Estimate, Support, or Get in Touch pages. It respects robots.txt, uses request timeouts and throttling, and avoids following links to other domains.

It extracts email addresses from page text and `mailto:` links. It does not send emails.

### Where emails are stored

When addresses are found, the workflow:

- Merges the addresses into `businesses.emails` without replacing existing entries.
- Sets `businesses.email` to the first discovered address only if that field is empty.
- Updates `businesses.normalized_domain`.
- Calls `refresh_business_qualification` so the database's existing email-format check can be recalculated.
- Saves the domain-level extraction outcome in `website_email_scrapes`.

A website crawl is not email verification. A syntactically valid address is not proof that the mailbox exists, accepts mail, or belongs to a specific decision-maker.

### Domain attempt tracking

The `website_email_scrapes` table stores one record per normalized domain:

| Field | Meaning |
|---|---|
| `normalized_domain` | Primary key identifying the domain |
| `website` | Website URL used for the crawl |
| `status` | `pending`, `processing`, `found`, `not_found`, or `failed` |
| `emails` | Addresses extracted from the website |
| `source_urls` | Pages from which the crawler collected data |
| `attempt_count` | Number of attempts recorded |
| `last_attempt_at` | Most recent attempt time |
| `last_success_at` | Time of a successful email discovery |
| `last_error` | Error details when a crawl fails |
| `created_at`, `updated_at` | Record timestamps |

Status behavior:

- **found:** saved emails exist; the domain is not crawled again on a normal run. Saved emails can be reused for newly qualified businesses sharing that domain.
- **not_found:** pages were crawled without finding an email and without recorded request errors. This means no address was found on the pages visited, not that no email exists anywhere. The domain is skipped unless `retry_not_found` is enabled.
- **failed:** the crawl could not be completed reliably. It is eligible for retry on a later run.
- **processing:** a crawl is underway. A record older than three hours is eligible for recovery on a later run.

### Important limitations

- The crawler uses ordinary HTTP requests and does not render JavaScript. Addresses generated only in the browser may be missed.
- Each domain is limited to eight pages per run.
- Email addresses are cleaned and format-checked, but deliverability is not verified.
- The workflow only selects businesses with `absolute_gate = 'passed'`; it does not require dynamic qualification to have passed.
- The workflow does not create individual decision-maker contacts in `contacts`. It stores discovered public business emails in the `businesses` record.

### Database migration

The tracking table definition is in:

`supabase/migrations/20261020_website_email_scrapes.sql`

The migration has been applied to the connected Supabase project for this setup. For a different Supabase project, apply the SQL migration before running the email extraction workflow.

## Workflow 4: Test gosom depth

**Workflow:** [Test Gosom](.github/workflows/test-gosom.yml)

This manually triggered workflow runs a fixed Google Maps search at depths 1, 5, and 10. It compares valid Google Place IDs across the results to show how deeper scraping changes coverage. The output is uploaded as the `gosom-depth-comparison` artifact and retained for seven days.

This is a diagnostic workflow, not the production queue pipeline. It does not ingest its test results into Supabase.

## Dependencies

The repository's `requirements.txt` includes:

- `supabase`: Supabase Python client.
- `Scrapy`: website crawling and email extraction.

The seeding workflow additionally installs:

- `osmnx`: location boundary lookup.
- `shapely`: geographic point-in-polygon checks.

The Google Maps scraping and depth-test workflows run gosom using its Docker image.

## Supabase tables and functions

The main scripts expect these database objects to exist:

| Object | Role |
|---|---|
| `grid_queue` | Pending, processing, and completed Google Maps search URLs |
| `scrape_batches` | Run-level metrics and status |
| `businesses` | Business records, websites, normalized domains, emails, and scraped data |
| `qualification` | Absolute qualification and email-format check fields |
| `website_email_scrapes` | Domain-level email extraction attempt history |
| `ingest_google_place(p_batch_id, p_payload)` | Inserts or updates a business and refreshes qualification |
| `refresh_business_qualification(p_business_id)` | Recalculates existing qualification checks |

The project may contain additional tables and views for downstream prospecting, research, QA, and outreach. These workflows do not automatically populate every downstream table.

## Troubleshooting

### A workflow cannot connect to Supabase

- Confirm both GitHub repository secrets are set and spelled correctly.
- Confirm the service-role key belongs to the intended Supabase project.
- Check the failed step's log for authentication, permissions, or schema errors.

### The grid seeder adds no URLs

- Check that `data/target_locations.csv` contains rows with `Status = Pending`.
- Check that `location_name` can be resolved by OSMnx.
- Existing URLs are intentionally skipped. If the same grid URLs already exist in `grid_queue`, no duplicates are inserted.

### The Google Maps workflow says there are no pending URLs

Run **Seed LeadVise Grid Queue** first, or inspect `grid_queue` in Supabase to confirm pending rows exist.

### Gosom produced a result file but the batch was requeued

A result file can contain partial data. Check `gosom_status.json`, the gosom container exit code, `ingest_stats.json`, and the finalization logs. The workflow deliberately avoids marking every URL complete when per-URL completion cannot be confirmed.

### Website email extraction found no emails

- Check the crawl report artifact and the workflow logs.
- The website may not publish an email address, may rely on JavaScript, or may expose it on a page outside the crawl limit.
- A `not_found` domain is skipped by default. Run the workflow with `retry_not_found = true` if you want to try it again.
- A `failed` domain is eligible for a later retry.

### A website is never retried

Check its row in `website_email_scrapes`. A `found` domain is intentionally skipped, a `not_found` domain requires the retry option, and a recent `processing` row is left alone to avoid overlapping work.

## Operational notes

- Keep the service-role key in GitHub Secrets only.
- Start with small batches and inspect artifacts before increasing the workload.
- Do not treat an extraction result as proof of email deliverability.
- The scraper and crawler should be used responsibly and in accordance with applicable website terms, privacy requirements, and outreach laws.
- GitHub Actions scheduled jobs can be delayed during periods of high GitHub Actions load.

## License

No license is declared in this repository at the time this README was written. Add a `LICENSE` file if you want to specify how others may use, modify, or redistribute the project.
