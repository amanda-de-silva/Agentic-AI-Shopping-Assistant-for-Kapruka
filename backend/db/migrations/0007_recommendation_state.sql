-- Short-term recommendation continuation state.
-- This is NOT durable recipient memory. It only remembers the last
-- recommendation specification and product IDs already shown in the session.

alter table if exists sessions
    add column if not exists recommendation_state jsonb null;

comment on column sessions.recommendation_state is
    'Short-term recommendation continuation state: ShoppingSpec + shown product IDs.';
