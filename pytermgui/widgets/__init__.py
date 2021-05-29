"""
pytermgui.widgets
-----------------
author: bczsalba


This module provides some widgets to be used in pytermgui.
The basic usage is to create a main Container(), and use
the `+=` operator to append elements to it.
"""

from .base import Widget, Container, Splitter, Prompt, Label
from .extra import ListView, ColorPicker, InputField, ProgressBar
from .styles import (
    default_foreground,
    default_background,
    overrideable_style,
    create_markup_style,
)
from . import boxes

__all__ = [
    "Widget",
    "Splitter",
    "Container",
    "Prompt",
    "Label",
    "ListView",
    "ColorPicker",
    "InputField",
    "ProgressBar",
    "boxes",
    "create_markup_style",
]
