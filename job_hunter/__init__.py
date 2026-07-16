"""LinkedIn job hunter & application tracker.

Pulls public job listings from LinkedIn's guest (no-login) search endpoint
for a set of configured searches, scores them against your keywords, and
tracks every job through an application pipeline in SQLite.

Entry point: ``jobs.py`` at the repo root (see ``docs/job_hunter.md``).
"""
