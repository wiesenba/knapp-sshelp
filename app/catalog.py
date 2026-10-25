"""Read literal SSH aliases without executing config directives or probing hosts."""
from __future__ import annotations

import glob
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
PROJECT = re.compile(r"\b[A-Z]{2}\d{5}\b")
ENVIRONMENTS = ("all", "live", "dev", "sbx", "other")


def safe_text(value: str) -> str:
    """Never emit config-provided terminal control sequences or TSV delimiters."""
    return "".join(c if c.isprintable() else " " for c in str(value)).strip()


def environment(alias: str, description: str = "") -> str:
    for label, pattern in (
        ("live", r"[-_]live(?:[-_]sv\d+)?$"),
        ("dev", r"[-_]dev\d*$"),
        ("sbx", r"[-_](?:sbx|sandbox)$"),
    ):
        if re.search(pattern, alias, re.I):
            return label
    tail = description.rsplit(" - ", 1)[-1].strip().lower()
    return {"live": "live", "dev": "dev", "sbx": "sbx", "sandbox": "sbx"}.get(tail, "other")


@dataclass
class Host:
    alias: str
    project: str = ""
    project_name: str = ""
    environment: str = "other"
    description: str = ""
    source: str = ""
    line: int = 0
    declared: dict[str, str] = field(default_factory=dict)

    def search_text(self) -> str:
        return " ".join((self.alias, self.project, self.project_name, self.environment, self.description)).casefold()


class Catalog:
    def __init__(self, config: Path, home: Path):
        self.config, self.home = config, home
        self.hosts: dict[str, Host] = {}
        self.warnings: list[str] = []
        self._files = 0
        self._read(config, [], [[], ""])

    def _read(self, path: Path, stack: list[Path], context: list) -> None:
        try:
            resolved = path.resolve()
            if resolved in stack or len(stack) >= 16 or self._files >= 512:
                self.warnings.append(f"Skipped recursive/oversized Include: {safe_text(str(path))}")
                return
            self._files += 1
            if path.stat().st_size > 2_000_000:
                self.warnings.append(f"Skipped large config: {safe_text(str(path))}")
                return
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            self.warnings.append(f"Cannot read {safe_text(str(path))}: {safe_text(str(error))}")
            return
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                comment = safe_text(stripped.lstrip("# "))
                if comment and set(comment) != {"-"}:
                    context[1] = comment
                continue
            match = re.match(r"^\s*([A-Za-z][A-Za-z0-9]*)(?:\s*=\s*|\s+)(.*?)\s*$", line)
            if not match:
                continue
            keyword, tail = match.group(1).lower(), match.group(2)
            try:
                values = shlex.split(tail, comments=True)
            except ValueError:
                self.warnings.append(f"Cannot parse {safe_text(str(path))}:{number}")
                continue
            if keyword == "include":
                for value in values:
                    expanded = value.replace("%d", str(self.home))
                    if expanded.startswith("~/"):
                        expanded = str(self.home / expanded[2:])
                    include = Path(expanded)
                    if not include.is_absolute():
                        include = self.home / ".ssh" / include
                    for child in sorted(glob.glob(str(include))):
                        self._read(Path(child), stack + [resolved], context)
            elif keyword == "host":
                current = []
                comment = context[1]
                project = PROJECT.search(comment)
                pieces = comment.split(" - ")
                for alias in values:
                    if not ALIAS.fullmatch(alias):
                        continue  # Patterns, negations and shell metacharacters are not destinations.
                    key = alias.casefold()
                    if key not in self.hosts:
                        self.hosts[key] = Host(
                            alias=alias,
                            project=project.group() if project else "",
                            project_name=safe_text(pieces[1]) if project and len(pieces) > 1 else "",
                            environment=environment(alias, comment),
                            description=comment,
                            source=str(path), line=number,
                        )
                    current.append(self.hosts[key])
                context[:] = [current, ""]
            elif keyword == "match":
                context[:] = [[], ""]
            elif values:
                for host in context[0]:
                    host.declared.setdefault(keyword, safe_text(" ".join(values)))

    def lookup(self, alias: str) -> Host | None:
        return self.hosts.get(alias.casefold())

    def filtered(self, env: str = "all", project: str = "", query: str = "") -> list[Host]:
        terms = query.casefold().split()
        return sorted(
            (host for host in self.hosts.values()
             if (env == "all" or host.environment == env)
             and (not project or project.casefold() in (host.project + " " + host.project_name + " " + host.alias).casefold())
             and all(term in host.search_text() for term in terms)),
            key=lambda host: (host.project or "ZZZZZ", host.alias.casefold()),
        )