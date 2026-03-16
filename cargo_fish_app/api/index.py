"""Vercel entrypoint for both repo-root and subdir deployments."""
try:
    from cargo_fish_app.app import app  # Repo root deployment
except Exception:
    from app import app  # Subdirectory deployment
