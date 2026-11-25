"""Line up `=` the way `terraform fmt` does.

Templates can't: how far a column moves depends on the longest name next to it,
and names come from the inventory. So every generated Terraform project failed
`terraform fmt -check`, which is the first step of every pipeline this tool
generates (ADR 0084).

This is the alignment half of `terraform fmt` and nothing else. Indentation and
spacing inside expressions are the templates' job. The rules are hclwrite's:

* consecutive lines that each hold one whole `name = value` form a group, and
  their `=` align to the longest name in it;
* a blank line, a comment line, or a line whose value opens a bracket that it
  does not close ends the group (that line keeps a single space);
* trailing comments on consecutive lines align too.

The tests compare it with the real `tofu fmt` on every target.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_HEREDOC = re.compile(r"<<-?\s*\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*$")
_OPENING = re.compile(r"^(\s*(?:\"[^\"]*\"|[\w.\-]+))\s+=\s+")


def _split(line: str) -> Tuple[str, Optional[str], str]:
    """(lead, assignment or None, trailing comment) of one line."""
    stack: List[str] = []  # '"' inside a string, '{' inside an interpolation
    equal, brackets, comment = -1, 0, len(line)
    i = 0
    while i < len(line):
        ch = line[i]
        if stack and stack[-1] == '"':
            if ch == "\\":
                i += 1
            elif ch == '"':
                stack.pop()
            elif ch in "$%" and line[i + 1:i + 2] == "{" and line[i - 1:i] != ch:
                stack.append("{")
                i += 1
        elif ch == '"':
            stack.append('"')
        elif stack and ch == "{":
            stack.append("{")
        elif stack and ch == "}":
            stack.pop()
        elif not stack:
            if ch == "#" or line[i:i + 2] == "//":
                comment = i
                break
            if equal >= 0 and ch in "{[(":
                brackets += 1
            elif equal >= 0 and ch in "}])":
                brackets -= 1
            elif (ch == "=" and equal < 0 and line[i + 1:i + 2] not in ("=", ">")
                  and line[i - 1:i] not in ("=", "!", "<", ">")):
                equal = i
        i += 1
    code, trailing = line[:comment].rstrip(), line[comment:].rstrip()
    if equal <= 0 or brackets != 0 or not code[:equal].strip():
        return code, None, trailing
    return code[:equal].rstrip(), code[equal + 1:].strip(), trailing


def _emit(group: List[Tuple[str, str, str]], bodies: Dict[int, List[str]]) -> List[str]:
    """One aligned group, with runs of trailing comments aligned as well.

    `bodies` holds heredoc text by the index of the line that opens it.
    """
    width = max(len(lead) for lead, _, _ in group)
    codes = [f"{lead.ljust(width)} = {value}" for lead, value, _ in group]
    out: List[str] = []
    i = 0
    while i < len(group):
        if not group[i][2]:
            out.append(codes[i])
            out.extend(bodies.get(i, []))
            i += 1
            continue
        j = i
        while j < len(group) and group[j][2]:
            j += 1
        column = max(len(c) for c in codes[i:j])
        out.extend(f"{codes[k].ljust(column)} {group[k][2]}" for k in range(i, j))
        i = j
    return out


def format_hcl(text: str) -> str:
    out: List[str] = []
    group: List[Tuple[str, str, str]] = []
    bodies: Dict[int, List[str]] = {}
    heredoc: Optional[str] = None
    sink = out
    for line in text.split("\n"):
        if heredoc is not None:
            sink.append(line)
            if line.strip() == heredoc:
                heredoc = None
            continue
        lead, value, comment = _split(line)
        opened = _HEREDOC.search(lead if value is None else value)
        if value is not None:
            # A heredoc is one assignment to `fmt`: it aligns with its group and
            # doesn't end it. Its text stays exactly as written.
            group.append((lead, value, comment))
            if opened:
                heredoc, sink = opened.group(1), bodies.setdefault(len(group) - 1, [])
            continue
        if group:
            out.extend(_emit(group, bodies))
            group, bodies = [], {}
        if opened:
            heredoc, sink = opened.group(1), out
            out.append(line)
        else:
            # `name   = {` keeps one space before the "=".
            out.append(_OPENING.sub(r"\1 = ", line.rstrip()))
    if group:
        out.extend(_emit(group, bodies))
    return "\n".join(out)


def is_hcl(name: str) -> bool:
    return name.endswith((".tf", ".tfvars"))
