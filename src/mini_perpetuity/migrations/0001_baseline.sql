-- One row naming the instance this database belongs to; `mp status` checks it against the config.
CREATE TABLE instance_meta (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    instance  text NOT NULL
);
