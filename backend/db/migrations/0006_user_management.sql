-- Phone-number user identity and authenticated application session lifecycle.
-- One application session is retained per user so the existing session-scoped
-- repositories remain user-specific without requiring a broad data-model rewrite.

create table if not exists users (
    id uuid primary key default uuid_generate_v4(),
    phone text not null unique,
    created_at timestamptz not null default now(),
    last_active_at timestamptz not null default now()
);

alter table sessions
    add column if not exists user_id uuid null references users(id) on delete restrict;

alter table sessions
    add column if not exists logged_out_at timestamptz null;

create unique index if not exists uq_sessions_user
    on sessions (user_id)
    where user_id is not null;

create index if not exists idx_users_phone on users (phone);

create index if not exists idx_sessions_user_active
    on sessions (user_id, last_active_at desc)
    where logged_out_at is null;

-- Preserve the existing optional recipient user_id column, but backfill it for
-- sessions that are already associated with a user.
update recipient_profiles rp
set user_id = s.user_id
from sessions s
where rp.session_id = s.id
  and rp.user_id is null
  and s.user_id is not null;

create index if not exists idx_recipient_profiles_user on recipient_profiles (user_id);

-- Clears application memory for a user/session while deliberately retaining
-- completed orders. Orders are transactional records, not assistant memory.
create or replace function clear_session_memory(p_session_id uuid)
returns void
language plpgsql
as $$
begin
    delete from checkout_attempts where session_id = p_session_id;
    delete from cart_items where session_id = p_session_id;
    delete from recipient_profiles where session_id = p_session_id;
    delete from messages where session_id = p_session_id;
end;
$$;