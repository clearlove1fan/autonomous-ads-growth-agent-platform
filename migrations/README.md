# Alembic Migrations

This directory contains database migrations for the production-skeleton schema.

The first migration creates a local PostgreSQL + pgvector schema with partition-ready fields. The tables are not native partitioned in v0.1; instead, they include `tenant_id`, `partition_key`, `partition_bucket`, and `partition_date` so a later migration can move high-volume tables into native range/hash partitions or distributed shards without changing application-level contracts.
