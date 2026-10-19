import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'data' / 'onfile.db'
load_dotenv(BASE_DIR / '.env')

NICRRON_BASE_URL = os.getenv('NICRRON_BASE_URL', 'https://api.nicrron.ai/v1')
NICRRON_MODEL = os.getenv('NICRRON_MODEL', 'gpt-4o-mini')
NICRRON_API_KEY = os.getenv('NICRRON_API_KEY', '')
ONFILE_API_KEY = os.getenv('ONFILE_API_KEY', '')
PRISM_API_URL = os.getenv('PRISM_API_URL', 'https://prism.blockconvey.com/api/traces')
PRISM_API_KEY = os.getenv('PRISM_API_KEY', '')
PRISM_PROJECT_ID = os.getenv('PRISM_PROJECT_ID', 'onfile-demo')
