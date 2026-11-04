-- One row per glossary domain: how many terms it has and how complete they are.
select
    coalesce(domain, 'unassigned')        as domain,
    count(*)                              as term_count,
    count(owner)                          as terms_with_owner,
    count(*) - count(owner)               as terms_without_owner,
    round(avg(length(definition)), 0)     as avg_definition_length,
    max(loaded_at)                        as last_loaded_at
from {{ ref('dim_glossary') }}
group by 1
