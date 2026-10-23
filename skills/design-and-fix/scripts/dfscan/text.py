from __future__ import annotations

from typing import List


def strip_comments(text: str, lang: str) -> str:
    """Blank //, /* */ (and # for Python/Ruby/Elixir) comments; keep strings and every newline.

    Risks: JS regex literals containing quotes can confuse the string tracker;
    Python docstrings are strings and are therefore kept.
    """
    hash_style = bool(lang) and lang.startswith(("Python", "Ruby", "Elixir"))
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
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
