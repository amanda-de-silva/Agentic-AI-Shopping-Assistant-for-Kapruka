create table if not exists orders (
    id uuid primary key default uuid_generate_v4(),
    session_id uuid not null references sessions(id) on delete cascade,
    kapruka_order_number text null,
    status text not null default 'pending',
    checkout_url text null,
    total numeric(12, 2) null,
    currency text not null default 'LKR',
    created_at timestamptz not null default now()
);

create index if not exists idx_orders_session on orders (session_id);
create index if not exists idx_orders_kapruka_number on orders (kapruka_order_number);

-- Short-TTL cache in front of kapruka_track_order so repeated "where's my order"
-- questions in one session don't re-hit the MCP server every time.
create table if not exists order_status_cache (
    order_number text primary key,
    last_status text null,
    last_synced_at timestamptz not null default now(),
    raw_payload jsonb null
);
