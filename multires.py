"""Workspace launcher. Existing ``python multires.py ...`` commands still work."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from multires_app.__main__ import main

if __name__=='__main__':
    raise SystemExit(main())
