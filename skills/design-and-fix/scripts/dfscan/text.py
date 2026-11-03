from __future__ import annotations

import re
from typing import List

# Characters that can start a comment or a string; everything between them is copied in one slice.
_HASH_STARTS = re.compile(r"[#\"']")
_SLASH_STARTS = re.compile(r"//|/\*|[\"'`]")


def strip_comments(text: str, lang: str) -> str:
    """Blank //, /* */ (and # for Python/Ruby/Elixir) comments; keep strings and every newline.

    Risks: JS regex literals containing quotes can confuse the string tracker;
    Python docstrings are strings and are therefore kept.
    """
    hash_style = bool(lang) and lang.startswith(("Python", "Ruby", "Elixir"))
    starts = _HASH_STARTS if hash_style else _SLASH_STARTS
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        m = starts.search(text, i)
        if m is None:
            out.append(text[i:])
            break
        if m.start() > i:
            out.append(text[i:m.start()])
            i = m.start()
        c = text[i]
        two = text[i:i + 2]
        if hash_style and c == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif not hash_style and two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif not hash_style and two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c in "\"'`" and not (c == "`" and hash_style):
            if hash_style and text[i:i + 3] in ('"""', "'''"):
                q = text[i:i + 3]
                j = text.find(q, i + 3)
                j = n if j < 0 else j + 3
            else:
                multiline = c == "`"
                j = i + 1
                while j < n and text[j] != c:
                    if text[j] == "\\":
                        j += 1
                    elif text[j] == "\n" and not multiline:
                        break
                    j += 1
                j = min(j + 1, n) if j < n and text[j] == c else min(j, n)
            out.append(text[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_config_comments(text: str) -> str:
    """Blank `#` comments in dotenv / YAML / TOML / INI / properties text; keep every offset.

    A `#` starts a comment only at the start of a line or after a space or tab, and not inside a
    quoted string on that line, as in dotenv and YAML: `API_KEY=abc#def` keeps its whole value.
    """
    out: List[str] = []
    for line in text.split("\n"):
        quote = ""
        cut = -1
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if quote:
                if c == "\\":
                    i += 1
                elif c == quote:
                    quote = ""
            elif c in "\"'":
                quote = c
            elif c == "#" and (i == 0 or line[i - 1] in " \t"):
                cut = i
                break
            i += 1
        out.append(line if cut < 0 else line[:cut] + " " * (n - cut))
    return "\n".join(out)
