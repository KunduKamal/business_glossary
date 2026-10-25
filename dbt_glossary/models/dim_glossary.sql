select
    trim(term)                     as term,
    trim(definition)               as definition,
    nullif(trim(domain), '')       as domain,
    nullif(trim(owner), '')        as owner,
    md5(trim(term) || '|' || trim(definition)) as content_hash,
    current_timestamp()            as loaded_at
from {{ ref('glossary_terms') }}