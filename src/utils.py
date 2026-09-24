from __future__ import annotations

import json
import os
import re
import time
import hashlib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union


# ----------------------------
# Filesystem helpers
# ----------------------------

def ensure_dir(path: Union[str, Path]) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_json(path: Union[str, Path], obj: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_json(path: Union[str, Path], default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(path: Union[str, Path], obj: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ----------------------------
# Text normalization / matching
# ----------------------------

ZW_CHARS = [
    "\u200B",  # zero width space
    "\u200C",  # zero width non-joiner
    "\u200D",  # zero width joiner
    "\uFEFF",  # zero width no-break space
]

SMART_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201B": "'",
    "\u201C": '"', "\u201D": '"', "\u201F": '"',
}

DASHES = {
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2212": "-",  # minus
}

SPACES = {
    "\u00A0": " ",  # no-break space
    "\u2002": " ", "\u2003": " ", "\u2004": " ", "\u2005": " ",
    "\u2006": " ", "\u2007": " ", "\u2008": " ", "\u2009": " ",
    "\u200A": " ", "\u202F": " ", "\u205F": " ", "\u3000": " ",
}


def strip_zw(text: str) -> str:
    for ch in ZW_CHARS:
        text = text.replace(ch, "")
    return text


def unify_punct(text: str) -> str:
    # Smart quotes
    for k, v in SMART_QUOTES.items():
        text = text.replace(k, v)
    # Dashes
    for k, v in DASHES.items():
        text = text.replace(k, v)
    # Spaces
    for k, v in SPACES.items():
        text = text.replace(k, v)
    return text


def remove_diacritics(text: str) -> str:
    # NFKD + filter combining marks
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))
