from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from .masking import mask_secrets

SNIPPET_MAX = 160
SNIPPET_MASK_WINDOW = 1024  # characters masked before a long snippet is cut to SNIPPET_MAX


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

    def __post_init__(self) -> None:
        text = self.snippet.strip()
        if len(text) > SNIPPET_MAX:
            # Mask before cutting, so a secret straddling the cut cannot leak a partial value.
            text = mask_secrets(text[:SNIPPET_MASK_WINDOW]).strip()
        self.snippet = text[:SNIPPET_MAX]

    def to_dict(self) -> dict:
        return asdict(self)
