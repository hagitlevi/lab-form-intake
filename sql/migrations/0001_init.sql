-- Supersedes the old skeleton schema (documents/forms-with-inline-fields/commitments) -
-- nothing in it was ever written to by real code, so this replaces it outright rather than
-- ALTERing around it. Only the 7 fields, their bbox/page and reasons are stored - never
-- full OCR text or OCR words (see CLAUDE.md "Privacy").

DROP TABLE IF EXISTS commitments CASCADE;
DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS pages CASCADE;
DROP TABLE IF EXISTS forms CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS batches CASCADE;

CREATE TABLE batches (
    id SERIAL PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'done', 'interrupted', 'approved')),
    total_pages INTEGER NOT NULL DEFAULT 0,
    pages_done INTEGER NOT NULL DEFAULT 0,
    total_forms INTEGER NOT NULL DEFAULT 0,
    forms_done INTEGER NOT NULL DEFAULT 0,
    skipped_pages JSONB NOT NULL DEFAULT '[]',  -- [{"filename":..., "page_index":...}, ...]
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE files (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    position INTEGER NOT NULL  -- original upload order, 0-based
);

CREATE TABLE forms (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved')),
    flagged BOOLEAN NOT NULL DEFAULT false,
    flag_reasons TEXT[] NOT NULL DEFAULT '{}',   -- from grouping (classify_page reason)
    probable_continuation BOOLEAN NOT NULL DEFAULT false,  -- set by step 4, clearable via API
    split_by_ai BOOLEAN NOT NULL DEFAULT false,
    error TEXT,  -- step 4/5 error for this form, if any - the form is still saved
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pages (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page_index INTEGER NOT NULL,  -- 1-based within file
    form_id INTEGER REFERENCES forms(id) ON DELETE SET NULL,  -- set once grouping assigns it
    image BYTEA NOT NULL,
    error TEXT,  -- step 2 error for this page, if any - processing continues regardless
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per field *item*: central_number/commitment_number can repeat (item_index > 0
-- for the 2nd, 3rd, ... value); every other field is a single row at item_index 0.
CREATE TABLE fields (
    id SERIAL PRIMARY KEY,
    form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (name IN (
        'first_name', 'last_name', 'national_id', 'central_number',
        'received_date', 'sender', 'commitment_number'
    )),
    item_index INTEGER NOT NULL DEFAULT 0,
    value TEXT,
    state TEXT NOT NULL CHECK (state IN ('safe', 'review', 'corrected')),
    reasons TEXT[] NOT NULL DEFAULT '{}',
    bbox INTEGER[],  -- [x, y, w, h] in the page image's coordinates, or NULL
    page_id INTEGER REFERENCES pages(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (form_id, name, item_index)
);

CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
    field_id INTEGER REFERENCES fields(id) ON DELETE SET NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_files_batch_id ON files(batch_id);
CREATE INDEX idx_forms_batch_id ON forms(batch_id);
CREATE INDEX idx_pages_batch_id ON pages(batch_id);
CREATE INDEX idx_pages_form_id ON pages(form_id);
CREATE INDEX idx_fields_form_id ON fields(form_id);
CREATE INDEX idx_fields_name_value ON fields(name, value);
CREATE INDEX idx_audit_log_form_id ON audit_log(form_id);
