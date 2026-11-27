-- Reliability and authorization hardening.
-- Checkout attempts provide application-level idempotency and an auditable state
-- before an external Kapruka order is created.
create table if not exists checkout_attempts (
    id uuid primary key default uuid_generate_v4(),
    session_id uuid not null references sessions(id) on delete cascade,
    idempotency_key text not null,
    status text not null default 'pending' check (status in ('pending', 'succeeded', 'failed', 'uncertain')),
    order_id uuid null references orders(id) on delete set null,
    external_order_number text null,
    checkout_url text null,
    total numeric(12, 2) null,
    currency text null,
    error_message text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (session_id, idempotency_key)
);

create index if not exists idx_checkout_attempts_session on checkout_attempts (session_id, created_at desc);

-- Atomic cart increment. The distinct-item limit is enforced inside the same
-- database statement that inserts/updates the row, eliminating read/modify/write
-- races between concurrent add operations.
create or replace function add_cart_item_atomic(
    p_session_id uuid,
    p_product_id text,
    p_name text,
    p_price numeric,
    p_currency text,
    p_quantity integer,
    p_recipient_id uuid default null,
    p_gift_message text default null,
    p_max_distinct_items integer default 50
)
returns setof cart_items
language plpgsql
as $$
begin
    -- Serialize cart mutations for one session so the distinct-item limit and
    -- quantity increment are both atomic across concurrent requests.
    perform pg_advisory_xact_lock(hashtextextended(p_session_id::text, 0));

    if p_quantity < 1 then
        raise exception 'Quantity must be at least 1';
    end if;

    if not exists (
        select 1 from cart_items
        where session_id = p_session_id and product_id = p_product_id
    ) then
        if (
            select count(*) from cart_items where session_id = p_session_id
        ) >= p_max_distinct_items then
            raise exception 'Cart has reached its maximum number of distinct items';
        end if;
    end if;

    return query
    insert into cart_items (
        session_id, product_id, name, price, currency, quantity, recipient_id, gift_message
    ) values (
        p_session_id, p_product_id, p_name, p_price, p_currency, p_quantity, p_recipient_id, p_gift_message
    )
    on conflict (session_id, product_id)
    do update set
        name = excluded.name,
        price = excluded.price,
        currency = excluded.currency,
        quantity = cart_items.quantity + excluded.quantity,
        recipient_id = coalesce(excluded.recipient_id, cart_items.recipient_id),
        gift_message = coalesce(excluded.gift_message, cart_items.gift_message)
    returning *;
end;
$$;