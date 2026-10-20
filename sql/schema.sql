CREATE TABLE IF NOT EXISTS batches (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    filename TEXT NOT NULL,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS forms (
    id SERIAL PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    national_id TEXT,
    central_numbers TEXT[] NOT NULL DEFAULT '{}',
    received_date DATE,
    sender TEXT,
    field_meta JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pages (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    form_id INTEGER REFERENCES forms(id),
    position INTEGER NOT NULL,
    image BYTEA NOT NULL
);

CREATE TABLE IF NOT EXISTS commitments (
    id SERIAL PRIMARY KEY,
    form_id INTEGER NOT NULL REFERENCES forms(id),
    commitment_number TEXT NOT NULL,
    label TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    form_id INTEGER REFERENCES forms(id),
    action TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
