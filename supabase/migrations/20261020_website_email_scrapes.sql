create table if not exists public.website_email_scrapes (
  normalized_domain text primary key,
  website text not null,
  status text not null default 'pending'
    check (status = any (array['pending','processing','found','not_found','failed'])),
  emails text[] not null default '{}'::text[],
  source_urls text[] not null default '{}'::text[],
  attempt_count integer not null default 0 check (attempt_count >= 0),
  last_attempt_at timestamptz,
  last_success_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists website_email_scrapes_status_idx
  on public.website_email_scrapes (status);
