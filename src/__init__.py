# Standard library
import asyncio
import base64
import json
import math
import multiprocessing
import os
import platform as _platform
import random
import signal
import socket
import subprocess
import sys
import textwrap
import time
import tomllib
import uuid
import webbrowser
import string
import re
import shutil
import traceback
import importlib.util as ILUtil
from datetime import datetime, timezone, date, timedelta
from io import BytesIO
from pathlib import Path
from threading import Thread, Lock, Event as ThreadEvent, enumerate as thread_enum
from types import ModuleType
from urllib.parse import urlencode
from typing import TextIO

from dynaconf import Dynaconf

# PyQt6
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QStackedWidget, QSizePolicy,
    QDialog, QScrollArea, QFrame, QToolButton,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    QRect, QPoint, QSize, pyqtSignal, QObject, QThread,
    QEvent,
)
from PyQt6.QtGui import (
    QFont, QFontDatabase, QColor, QPalette, QIcon,
    QLinearGradient, QGradient, QPainter, QBrush, QPen,
    QMouseEvent, QResizeEvent,
)

# Third-party libraries
import psutil
import requests
from dotenv import load_dotenv, find_dotenv, set_key as set_env_key
from PIL import Image, ImageEnhance, ImageFilter

# Typing
from typing import Callable, Literal, Optional

# Platform-specific imports — guarded so the app loads on both Windows and Linux
_SYSTEM = _platform.system()

if _SYSTEM == "Windows":
    try:
        import pyautogui as pag
        import pygetwindow as gw
    except ImportError:
        pag = None
        gw = None

    try:
        from pynput.keyboard import Controller, Key
    except ImportError:
        Controller = None
        Key = None

    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
        )
    except ImportError:
        MediaManager = None
        PlaybackStatus = None

    # MPV via Chocolatey — Windows only
    MPV_PATH = r"C:\ProgramData\chocolatey\lib\mpvio.install\tools"
    if MPV_PATH not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + MPV_PATH

else:

    os.environ["QT_STYLE_OVERRIDE"] = ""
    
    # Linux / macOS stubs for anything that might be referenced at module level
    pag = None
    gw = None
    MediaManager = None
    PlaybackStatus = None

    try:
        from pynput.keyboard import Controller, Key
    except ImportError:
        Controller = None
        Key = None

# NLP
import spacy
NLP_MODEL = spacy.load("en_core_web_sm", disable=["parser", "ner"])

# ── App-wide constants ──────────────────────────────────────────────────────
EVENT_LEVELS = Literal["debug", "info", "warning", "error", "critical"]
PLUGIN_SPEC_MARKER = "assistant_plugins"
APP_NAME = "Desktop Home Assistant"

EVENTS = Literal[
    "initialized",
    "on_focus",
    "on_un_focus",
    "on_visit",
    "on_leave",
    "on_update",
    "on_minimize",
    "on_maximize",
    "on_fullscreen",
    "on_state_change",
    "on_close",
    "on_settings_saved",
    "on_woke_assistant",
    "on_assistant_transcribed",
]

from src.styling import *
from src.main import Client