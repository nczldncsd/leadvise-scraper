# LeadVise scraper

Google Maps lead scraping and post-qualification enrichment for the LeadVise Supabase database.

## Website email extraction

The manually triggered GitHub Actions workflow **LeadVise Website Email Extraction** extracts publicly listed email addresses from business websites after absolute qualification.

### How it works

1. Reads qualification.absolute_gate = 'passed'.
2. Selects businesses with a website and no email in businesses.email or businesses.emails.
3. Groups businesses by normalized domain to avoid crawling the same website repeatedly.
4. Uses Scrapy to crawl the homepage and up to 8 same-domain pages, prioritizing Contact/About/service pages. It does not run a JavaScript browser.
5. Saves discovered addresses into businesses.emails and the first address into businesses.email when empty, then refreshes the existing qualification record.
6. Records each domain's attempt, status, emails, source URLs, and errors in website_email_scrapes.

### Tracking statuses

- **found**: one or more email addresses were extracted. Skipped on future runs.
- **not_found**: pages were crawled successfully but no email was found. Skipped on future runs unless retry_not_found is enabled.
- **failed**: the crawl could not be completed reliably. Eligible for retry on a later run.
- **processing**: a crawl is in progress. A record stuck in this status for more than 3 hours can be retried.

A not_found result means no address was found in the pages crawled, not that the business has no email address anywhere. This crawler does not render JavaScript and does not verify deliverability.

### Run manually

1. Open the repository's **Actions** tab.
2. Select **LeadVise Website Email Extraction**.
3. Click **Run workflow**.
4. Choose the maximum number of unique domains (default 50). Set retry_not_found to true only when you want to revisit domains previously crawled without finding an email.
5. Review the run logs and download the website-email-extraction-report artifact if needed.

The workflow uses the existing SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY GitHub repository secrets. The tracking table migration is in supabase/migrations/20261020_website_email_scrapes.sql.
