-- Exclude third-party profile/directory URLs from absolute qualification.
-- Known franchise brands fail the hard gate. Shared-domain listings across cities
-- are recorded separately as a multi-location signal and routed to review.

CREATE OR REPLACE FUNCTION public.refresh_business_qualification(p_business_id uuid)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE
  b public.businesses%rowtype;
  normalized text;
  v_domain text;
  v_multi_location boolean := false;
  v_email_check text;
  v_category_check text;
BEGIN
  SELECT * INTO b FROM public.businesses WHERE id = p_business_id;
  IF NOT FOUND THEN RETURN; END IF;

  normalized := lower(coalesce(b.business_name, '') || ' ' || coalesce(b.website, ''));

  v_domain := lower(regexp_replace(
    split_part(
      regexp_replace(
        coalesce(nullif(btrim(b.normalized_domain), ''), b.website, ''),
        '^https?://', '', 'i'
      ),
      '/', 1
    ),
    '^www[.]', ''
  ));
  v_domain := split_part(v_domain, '?', 1);
  v_domain := btrim(v_domain, '. ');

  IF v_domain <> '' AND nullif(btrim(b.place_id), '') IS NOT NULL
     AND nullif(btrim(b.city), '') IS NOT NULL THEN
    SELECT EXISTS (
      SELECT 1
      FROM public.businesses other
      WHERE other.id <> b.id
        AND nullif(btrim(other.place_id), '') IS NOT NULL
        AND nullif(btrim(other.city), '') IS NOT NULL
        AND (
          lower(btrim(other.city)) <> lower(btrim(b.city))
          OR lower(btrim(coalesce(other.state, ''))) <> lower(btrim(coalesce(b.state, '')))
        )
        AND lower(regexp_replace(
          split_part(
            regexp_replace(
              coalesce(nullif(btrim(other.normalized_domain), ''), other.website, ''),
              '^https?://', '', 'i'
            ),
            '/', 1
          ),
          '^www[.]', ''
        )) = v_domain
    ) INTO v_multi_location;
  END IF;

  v_email_check := CASE
    WHEN EXISTS (
      SELECT 1 FROM unnest(coalesce(b.emails, '{}'::text[])) e
      WHERE public.is_valid_email_format(e)
    ) THEN 'pass'
    WHEN EXISTS (
      SELECT 1 FROM unnest(coalesce(b.emails, '{}'::text[])) e
      WHERE btrim(coalesce(e, '')) <> ''
    ) THEN 'fail'
    ELSE 'missing'
  END;

  v_category_check := CASE
    WHEN cardinality(coalesce(b.categories, '{}'::text[])) = 0 THEN 'review'
    WHEN EXISTS (
      SELECT 1
      FROM unnest(coalesce(b.categories, '{}'::text[])) AS c(category)
      WHERE lower(btrim(c.category)) !~ '(auto|automotive|car|vehicle|truck|rv).*air conditioning'
        AND lower(btrim(c.category)) ~
        '(appliance.*repair|repair.*appliance|refrigerator.*repair|repair.*refrigerator|fridge.*repair|repair.*fridge|freezer.*repair|repair.*freezer|washer.*dryer.*repair|washer.*repair|repair.*washer|washing machine.*repair|repair.*washing machine|dryer.*repair|repair.*dryer|dishwasher.*repair|repair.*dishwasher|microwave.*repair|repair.*microwave|oven.*repair|repair.*oven|stove.*repair|repair.*stove|range.*repair|repair.*range|cooktop.*repair|repair.*cooktop|garbage disposal.*repair|repair.*garbage disposal|ice maker.*repair|repair.*ice maker|vacuum cleaner.*repair|repair.*vacuum cleaner|air conditioning.*(repair|service)|repair.*air conditioning|hvac.*(repair|service)|repair.*hvac|heating.*(repair|service)|repair.*heating|furnace.*repair|repair.*furnace|ventilation.*(repair|service)|repair.*ventilation)'
    ) THEN 'pass'
    WHEN NOT EXISTS (
      SELECT 1
      FROM unnest(coalesce(b.categories, '{}'::text[])) AS c(category)
      WHERE lower(btrim(c.category)) NOT IN (
        'repair service',
        'service establishment',
        'appliances customer service'
      )
    ) THEN 'review'
    ELSE 'fail'
  END;

  INSERT INTO public.qualification(
    business_id, rating_check, review_count_check, known_franchise_check,
    multiple_cities, website_check, email_format_check, category_check, updated_at
  )
  VALUES (
    b.id,
    CASE WHEN b.rating IS NULL THEN 'missing' WHEN b.rating >= 3.5 THEN 'pass' ELSE 'fail' END,
    CASE WHEN b.review_count IS NULL THEN 'missing' WHEN b.review_count BETWEEN 15 AND 600 THEN 'pass' ELSE 'fail' END,
    CASE
      WHEN normalized ~ '(mr[. ]?appliance|fuse ?hvac|ubreakifix|dryer vent wizard|mr[. ]?electric|ultrafix|mr[. ]?handyman|handyman connection|trublue|house doctors|handyman pro|home service ally|mister sparky|glass doctor|the patch boys|housemaster|kitchen tune[- ]?up|surface experts|handypro|fitness machine technicians|sears|sears home services|sears appliance repair|a&?e factory service|whirlpool factory service|ge appliances factory service|samsung certified repair|one hour heating|aire serv|ars[/ ]?rescue rooter|roto[- ]?rooter|benjamin franklin plumbing|servpro|servicemaster|neighborly)'
        THEN 'fail'
      ELSE 'clear'
    END,
    CASE
      WHEN v_multi_location THEN 'yes'
      ELSE coalesce((SELECT q.multiple_cities FROM public.qualification q WHERE q.business_id = b.id), 'pending')
    END,
    CASE
      WHEN b.website IS NULL OR btrim(b.website) = '' THEN 'missing'
      WHEN v_domain ~ '(^|[.])(instagram[.]com|facebook[.]com|fb[.]com|linkedin[.]com|tiktok[.]com|twitter[.]com|x[.]com|youtube[.]com|youtu[.]be|threads[.]net|pinterest[.]com|snapchat[.]com|linktr[.]ee|beacons[.]ai|bio[.]site|solo[.]to|taplink[.]cc|campsite[.]bio|about[.]me|yelp[.]com|yellowpages[.]com|angi[.]com|angieslist[.]com|homeadvisor[.]com|thumbtack[.]com|houzz[.]com|bbb[.]org|betterbusinessbureau[.]org|mapquest[.]com|manta[.]com|nextdoor[.]com|superpages[.]com|merchantcircle[.]com|chamberofcommerce[.]com|bark[.]com|porch[.]com|foursquare[.]com|local[.]com|expertise[.]com|business[.]site|sites[.]google[.]com|wixsite[.]com|maps[.]app[.]goo[.]gl|g[.]page)([.]|$)'
        OR lower(b.website) ~ '(google[.]com/maps|goo[.]gl/maps|google[.]com/url)'
        THEN 'fail'
      ELSE 'pass'
    END,
    v_email_check,
    v_category_check,
    now()
  )
  ON CONFLICT (business_id) DO UPDATE SET
    rating_check = excluded.rating_check,
    review_count_check = excluded.review_count_check,
    known_franchise_check = excluded.known_franchise_check,
    multiple_cities = CASE
      WHEN v_multi_location THEN 'yes'
      ELSE qualification.multiple_cities
    END,
    website_check = excluded.website_check,
    email_format_check = excluded.email_format_check,
    category_check = excluded.category_check,
    updated_at = now();
END;
$function$;

CREATE OR REPLACE FUNCTION public.sync_qualification_absolute_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
BEGIN
  NEW.absolute_gate := CASE
    WHEN NEW.rating_check = 'fail'
      OR NEW.review_count_check = 'fail'
      OR NEW.known_franchise_check = 'fail'
      OR NEW.website_check = 'fail'
      OR NEW.category_check = 'fail'
      THEN 'failed'
    WHEN NEW.multiple_cities IN ('yes', 'unclear')
      OR NEW.category_check IN ('pending', 'review')
      OR NEW.rating_check IN ('missing', 'pending')
      OR NEW.review_count_check IN ('missing', 'pending')
      OR NEW.website_check IN ('missing', 'pending')
      OR NEW.known_franchise_check = 'pending'
      THEN 'review'
    ELSE 'passed'
  END;
  RETURN NEW;
END;
$function$;

DO $requalify$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT id FROM public.businesses LOOP
    PERFORM public.refresh_business_qualification(r.id);
  END LOOP;
END;
$requalify$;
