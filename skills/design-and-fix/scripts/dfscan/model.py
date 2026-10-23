from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


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


@dataclass
class Signal:
    rule: str
    category: str
    severity_hint: str
    file: str
    line: int
    snippet: str
    confidence: str

    def __post_init__(self) -> None:
        self.snippet = self.snippet.strip()[:160]

    def to_dict(self) -> dict:
        return asdict(self)
