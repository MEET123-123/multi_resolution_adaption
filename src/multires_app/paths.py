"""Paths relative to this editable project, independent of a module's group."""
import os
from pathlib import Path

PROJECT_ROOT=Path(os.environ.get('MULTIRES_PROJECT_ROOT',Path(__file__).resolve().parents[2])).resolve()
SOURCE_ROOT=PROJECT_ROOT/'src'
CONFIG_ROOT=PROJECT_ROOT/'configs'
REPORT_ROOT=PROJECT_ROOT/'artifacts/reports'
LOG_ROOT=PROJECT_ROOT/'artifacts/logs'
