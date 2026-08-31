import pathlib
import sys

# Make hutchlib importable without an install step.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
