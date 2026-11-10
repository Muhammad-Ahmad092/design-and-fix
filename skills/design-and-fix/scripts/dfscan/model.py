from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

from .masking import mask_secrets

SNIPPET_MAX = 160
SNIPPET_MASK_WINDOW = 1024  # characters masked before a long snippet is cut to SNIPPET_MAX
REASON_MAX = 240  # uncertain_reason cap (masked before the cut, like the snippet)
_TOKEN_CHAR_RE = re.compile(r"[0-9A-Za-z_.-]")  # characters of the masked token shapes
_PARTIAL_TAIL_RE = re.compile(r"[0-9A-Za-z_.-]+$")


def _masked_window(text: str) -> str:
    """mask_secrets over the first SNIPPET_MASK_WINDOW characters. When the window edge splits a
    word, that partial word is dropped first: a token cut there could be too short for the token
    patterns, and an earlier mask that shrinks the text could pull the fragment into the part
    that is kept."""
    window = text[:SNIPPET_MASK_WINDOW]
    if len(text) > SNIPPET_MASK_WINDOW and _TOKEN_CHAR_RE.match(text, SNIPPET_MASK_WINDOW):
        window = _PARTIAL_TAIL_RE.sub("", window) or window
    return mask_secrets(window).strip()


@dataclass
class FileInfo:
    rel: str             # root-relative POSIX path
    abs: str             # absolute path
    ext: str             # lowercase extension incl. dot
    lang: Optional[str]  # language label for code files, else None
    loc: int             # line count (0 for non-code)
    size: int            # bytes
    is_test: bool
    is_config: bool = False  # .env / YAML / JSON / TOML / INI / properties (secret scan only)
    is_example: bool = False  # under an examples/demo folder (skipped by route and folder rules)
    is_secret_file: bool = False  # *.pem / *.key / id_rsa* / *.p12 (private-key check only)
    is_infra: bool = False  # Dockerfile / k8s workload YAML / CI config (infra rules only, no LOC)


@dataclass
class Signal:
    rule: str
    category: str
    severity_hint: str
    file: str
    line: int
    snippet: str
    confidence: str
    fingerprint: str = ""
    rule_version: str = ""
    # Why the rule could not decide (for example auth configured outside the scanned scope);
    # serialised only when non-empty, so existing schema-3 output is unchanged.
    uncertain_reason: str = ""

    def __post_init__(self) -> None:
        text = self.snippet.strip()
        if len(text) > SNIPPET_MAX:
            # Mask before cutting, so a secret straddling the cut cannot leak a partial value.
            text = _masked_window(text)
        self.snippet = text[:SNIPPET_MAX]
        # A reason may quote source text: always mask it, and mask before cutting.
        reason = self.uncertain_reason.strip()
        if reason:
            reason = _masked_window(reason)[:REASON_MAX]
        self.uncertain_reason = reason

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["uncertain_reason"]:
            del d["uncertain_reason"]
        return d
