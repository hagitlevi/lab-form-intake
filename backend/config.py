import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL")
TESSERACT_CMD = os.environ.get("TESSERACT_CMD")
LOCAL_WORKERS = int(os.environ.get("LOCAL_WORKERS", 2))
AI_WORKERS = int(os.environ.get("AI_WORKERS", 3))
