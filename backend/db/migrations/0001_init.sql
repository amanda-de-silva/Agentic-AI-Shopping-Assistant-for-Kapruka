-- Sessions & messages: the backbone of short-term memory.
create extension if not exists "uuid-ossp";

create table if not exists sessions (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid null,
    channel text not null default 'web',
    created_at timestamptz not null default now(),
    last_active_at timestamptz not null default now()
);

create table if not exists messages (
    id uuid primary key default uuid_generate_v4(),
    session_id uuid not null references sessions(id) on delete cascade,
    role text not null check (role in ('user', 'assistant', 'system')),
    content text not null,
    intent text null,
    created_at timestamptz not null default now()
);

create index if not exists idx_messages_session_created
    on messages (session_id, created_at desc);

create table if not exists schema_migrations (
    version text primary key,
    applied_at timestamptz not null default now()
);
