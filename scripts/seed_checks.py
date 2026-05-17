#!/usr/bin/env python3
"""
Seed the default check template library.

Usage:
  python scripts/seed_checks.py

Idempotent — skips templates that already exist by ID.
"""
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402 — needs sys.path set first

with app.app_context():
    from core.check_seed_data import seed_default_check_templates
    n = seed_default_check_templates()
    print(f'Seeded {n} check template(s) (skipped already-existing ones).')
