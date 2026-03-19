import sys
from pathlib import Path

# Ensure salesforce_converter package is importable when tests are run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
