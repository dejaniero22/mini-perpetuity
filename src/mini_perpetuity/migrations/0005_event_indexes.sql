-- Rate limiting (plan M5) counts recent events per actor and per activity; keep those counts cheap.
CREATE INDEX events_time_idx ON events (occurred_at);
CREATE INDEX events_actor_time_idx ON events (actor, occurred_at);
