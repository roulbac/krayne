"""The spec library: plain SkyPilot YAML files under ``~/.stratus/specs``.

Layout::

    ~/.stratus/specs/
      clusters/<name>.yaml   # sky launch -c <name> <file>
      jobs/<name>.yaml       # sky jobs launch <file>
      services/<name>.yaml   # sky serve up -n <name> <file>
      volumes/<name>.yaml    # sky volumes apply <file>
      history.jsonl          # what stratus launched, when, and the request id

Files are the source of truth: stratus re-reads them every time, so editing
them by hand (or with ``e``/``E`` in the TUI) is fully supported.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from stratus.settings import specs_dir
from stratus.specs.models import Spec, SpecKind, spec_from_yaml

_HEADER = (
    "# Managed by stratus — edit freely, stratus re-reads this file.\n"
    "# Use without stratus: {usage}\n"
)

_USAGE = {
    SpecKind.CLUSTER: "sky launch -c {name} {path}",
    SpecKind.JOB: "sky jobs launch -n {name} {path}",
    SpecKind.SERVICE: "sky serve up -n {name} {path}",
    SpecKind.VOLUME: "sky volumes apply {path}",
}


@dataclass(frozen=True)
class StoredSpec:
    kind: SpecKind
    name: str
    path: Path
    modified: float
    spec: Spec | None
    error: str | None = None

    @property
    def text(self) -> str:
        try:
            return self.path.read_text()
        except OSError:
            return ""


class SpecStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else specs_dir()

    def dir_for(self, kind: SpecKind) -> Path:
        return self.root / kind.folder

    def path_for(self, kind: SpecKind, name: str) -> Path:
        return self.dir_for(kind) / f"{name}.yaml"

    def exists(self, kind: SpecKind, name: str) -> bool:
        return self.path_for(kind, name).exists()

    def render(self, spec: Spec) -> str:
        path = self.path_for(spec.kind, spec.name)
        usage = _USAGE[spec.kind].format(name=spec.name, path=_tilde(path))
        return _HEADER.format(usage=usage) + spec.to_yaml()

    def save(self, spec: Spec) -> Path:
        path = self.path_for(spec.kind, spec.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(self.render(spec))
        tmp.replace(path)
        return path

    def save_text(self, kind: SpecKind, name: str, text: str) -> Spec:
        """Validate raw YAML and save it verbatim (keeps the user's comments)."""
        spec = spec_from_yaml(kind, name, text)
        path = self.path_for(kind, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n")
        return spec

    def load(self, kind: SpecKind, name: str) -> StoredSpec:
        path = self.path_for(kind, name)
        return self._read(kind, path)

    def get(self, kind: SpecKind, name: str) -> Spec | None:
        if not self.exists(kind, name):
            return None
        return self.load(kind, name).spec

    def delete(self, kind: SpecKind, name: str) -> None:
        self.path_for(kind, name).unlink(missing_ok=True)

    def list(self, kind: SpecKind | None = None) -> list[StoredSpec]:
        kinds = [kind] if kind else list(SpecKind)
        out: list[StoredSpec] = []
        for k in kinds:
            folder = self.dir_for(k)
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.yaml")):
                out.append(self._read(k, path))
        return out

    def names(self, kind: SpecKind) -> set[str]:
        folder = self.dir_for(kind)
        if not folder.is_dir():
            return set()
        return {p.stem for p in folder.glob("*.yaml")}

    def _read(self, kind: SpecKind, path: Path) -> StoredSpec:
        name = path.stem
        try:
            text = path.read_text()
            mtime = path.stat().st_mtime
        except OSError as exc:
            return StoredSpec(kind, name, path, 0.0, None, str(exc))
        try:
            spec = spec_from_yaml(kind, name, text)
        except Exception as exc:  # yaml or validation error — surface, don't crash
            return StoredSpec(kind, name, path, mtime, None, _short_error(exc))
        return StoredSpec(kind, name, path, mtime, spec)

    # ── history ──────────────────────────────────────────────

    @property
    def history_path(self) -> Path:
        return self.root / "history.jsonl"

    def record(self, kind: SpecKind, name: str, action: str, request_id: str | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), "kind": kind.value, "name": name, "action": action, "request_id": request_id}
        with self.history_path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def last_used(self) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = {}
        if not self.history_path.exists():
            return out
        for line in self.history_path.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[(e.get("kind"), e.get("name"))] = e.get("ts", 0.0)
        return out


def _tilde(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _short_error(exc: Exception) -> str:
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        errs = exc.errors()
        if errs:
            e = errs[0]
            loc = ".".join(str(p) for p in e.get("loc", ()))
            msg = e.get("msg", "invalid")
            return f"{loc}: {msg}" if loc else msg
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__
