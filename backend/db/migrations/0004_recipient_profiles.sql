-- The authoritative, structured store for safety-critical facts (allergies) and
-- preferences about people the user shops for. Qdrant indexes free-text notes from
-- this table for fuzzy recall, but this table is always the source of truth.
create table if not exists recipient_profiles (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid null,
    session_id uuid not null references sessions(id) on delete cascade,
    name text not null,
    relationship text null,
    allergies text[] not null default '{}',
    likes text[] not null default '{}',
    dislikes text[] not null default '{}',
    notes text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_recipient_profiles_session on recipient_profiles (session_id);
create unique index if not exists uq_recipient_profiles_session_name
    on recipient_profiles (session_id, lower(name));

create or replace function set_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_recipient_profiles_updated on recipient_profiles;
create trigger trg_recipient_profiles_updated
    before update on recipient_profiles
    for each row execute function set_updated_at();
