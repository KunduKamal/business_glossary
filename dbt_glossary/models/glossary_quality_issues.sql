-- One row per glossary term that needs attention (missing owner/domain or very short definition).
select
    term,
    domain,
    owner,
    case
        when domain is null            then 'missing domain'
        when owner is null             then 'missing owner'
        when length(definition) < 40   then 'short definition'
    end                                as issue,
    loaded_at
from {{ ref('dim_glossary') }}
where domain is null
   or owner is null
   or length(definition) < 40
