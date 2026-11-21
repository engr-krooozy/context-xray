"""Settings, read from environment variables (or a .env file)."""

from __future__ import annotations

import os
from pathlib import Path

try:
  from dotenv import load_dotenv

  load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # python-dotenv is optional
  pass

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
WEB_DIR = ROOT / "web"

MODEL = os.getenv("XRAY_MODEL", "gemini-3.5-flash")
DB_PATH = Path(os.getenv("XRAY_DB", str(ROOT / "xray_memory.db")))

# Compaction: ADK compacts after a turn whose prompt reached this many tokens,
# keeping the most recent raw events.
COMPACT_AT_TOKENS = int(os.getenv("XRAY_COMPACT_TOKENS", "4000"))
KEEP_RAW_EVENTS = int(os.getenv("XRAY_KEEP_EVENTS", "6"))

# Bound the loop: max model calls per customer message.
MAX_LLM_CALLS = int(os.getenv("XRAY_MAX_LLM_CALLS", "8"))

# Optional: USD per million input tokens, to show cost. Leave unset to hide.
PRICE_PER_MTOK = float(os.getenv("XRAY_PRICE_PER_MTOK", "0") or 0)


def has_credentials() -> bool:
  if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"):
    return bool(os.getenv("GOOGLE_CLOUD_PROJECT"))
  return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def offline() -> bool:
  forced = os.getenv("XRAY_OFFLINE", "").lower() in ("1", "true", "yes")
  return forced or not has_credentials()
