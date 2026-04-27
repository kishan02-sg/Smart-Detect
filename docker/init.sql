-- docker/init.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- PostgreSQL initialisation script for the Metro Person Tracking System.
-- Automatically run by Docker Compose on FIRST startup of the postgres container.
--
-- This script:
--   1. Enables the pgvector extension for fast embedding similarity search
--   2. Creates all application tables (persons, stations, sightings)
--   3. Seeds 8 metro stations so the system is immediately usable
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Enable pgvector extension
--    Allows storing 512-dim ArcFace embeddings and doing cosine similarity in SQL.
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create stations table
CREATE TABLE IF NOT EXISTS stations (
    id       VARCHAR(64)  PRIMARY KEY,
    name     VARCHAR(128) NOT NULL,
    location VARCHAR(256) NOT NULL
);

-- 3. Create persons table
--    face_embedding: stored as TEXT (JSON array) for SQLite compat, or vector(512) for pgvector
CREATE TABLE IF NOT EXISTS persons (
    id              VARCHAR(36)  PRIMARY KEY DEFAULT gen_random_uuid()::text,
    unique_code     VARCHAR(32)  NOT NULL UNIQUE,
    face_embedding  TEXT,                        -- JSON list OR pgvector column
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    entry_station   VARCHAR(64)
);

-- Index for fast code lookups
CREATE INDEX IF NOT EXISTS idx_persons_unique_code ON persons (unique_code);

-- 4. Create sightings table
CREATE TABLE IF NOT EXISTS sightings (
    id                   VARCHAR(36)  PRIMARY KEY DEFAULT gen_random_uuid()::text,
    person_id            VARCHAR(36)  NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    station_id           VARCHAR(64)  REFERENCES stations(id) ON DELETE SET NULL,
    camera_id            VARCHAR(64)  NOT NULL,
    seen_at              TIMESTAMP    NOT NULL DEFAULT NOW(),
    confidence           FLOAT        NOT NULL,
    frame_snapshot_path  TEXT
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_sightings_person_id ON sightings (person_id);
CREATE INDEX IF NOT EXISTS idx_sightings_station_id ON sightings (station_id);
CREATE INDEX IF NOT EXISTS idx_sightings_seen_at ON sightings (seen_at);

-- 5. Seed metro stations
--    ON CONFLICT DO NOTHING = safe to re-run without errors
INSERT INTO stations (id, name, location) VALUES
    ('STA-001', 'Central Station',   'City Centre, Line 1 & 2'),
    ('STA-002', 'Airport Terminal',  'International Airport, Line 3'),
    ('STA-003', 'North Junction',    'North District, Line 1'),
    ('STA-004', 'South Gate',        'South District, Line 2'),
    ('STA-005', 'East Plaza',        'East Commercial Zone, Line 2'),
    ('STA-006', 'West Terminal',     'West Residential, Line 1'),
    ('STA-007', 'University Stop',   'University District, Line 3'),
    ('STA-008', 'Market Square',     'Old Town Market, Line 1 & 3')
ON CONFLICT (id) DO NOTHING;
