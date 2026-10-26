"""Paths shared by the source checkout and relocated portable bundle."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONSOLE_DIR = ROOT / 'console-ui'
FRONTEND_DIR = CONSOLE_DIR / 'frontend'
DEFAULT_STATE_DIR = ROOT / '.agentfleet'
