"""Vercel entrypoint for repo-root deployments."""
import os
import sys

# Ensure the root directory is in the python path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.append(root_dir)

try:
    from cargo_fish_app.app import app
except ImportError:
    # Fallback for different project structures
    try:
        from app import app
    except ImportError:
        raise ImportError("Could not find 'app' in 'cargo_fish_app.app' or 'app'.")

