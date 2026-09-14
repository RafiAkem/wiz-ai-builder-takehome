from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = ROOT_DIR / "data"
DEFAULT_DATABASE_PATH = DATA_DIR / "leads.db"
SEED_CSV_PATH = DATA_DIR / "leads_seed.csv"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
