-- Kapruka's MCP has no server-side cart concept -- this app owns cart state.
create table if not exists cart_items (
    id uuid primary key default uuid_generate_v4(),
    session_id uuid not null references sessions(id) on delete cascade,
    product_id text not null,
    name text not null,
    price numeric(12, 2) not null,
    currency text not null default 'LKR',
    quantity integer not null default 1 check (quantity > 0),
    recipient_id uuid null,
    gift_message text null,
    added_at timestamptz not null default now(),
    unique (session_id, product_id)
);

create index if not exists idx_cart_items_session on cart_items (session_id);
