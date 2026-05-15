from __future__ import annotations

from pathlib import Path
from typing import Any

from sentinelflow.domain.enums import SkillRuntimeMode, SkillType
from sentinelflow.domain.errors import SkillConfigurationError
from sentinelflow.domain.models import SkillCompletionPolicy, SkillSpec
from sentinelflow.skills.models import SentinelFlowSkill

try:
    import yaml  # type: ignore
except ModuleNotFoundError:
    yaml = None


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    Extract YAML frontmatter and body from a SKILL.md string.

    A valid frontmatter block starts with a line containing exactly '---'
    and ends with another such line.  Everything after the closing '---'
    is the document body.

    Returns (metadata_dict, body_text).  If no valid frontmatter is found,
    returns ({}, full_text).
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_index: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break

    if end_index is None:
        return {}, text

    frontmatter_text = "".join(lines[1:end_index])
    body = "".join(lines[end_index + 1:]).lstrip("\n")

    if yaml is not None:
        try:
            parsed = yaml.safe_load(frontmatter_text)
            if isinstance(parsed, dict):
                return parsed, body
        except yaml.YAMLError:
            pass

    return _parse_minimal_yaml(frontmatter_text), body


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.lower() in {"null", "none", "~"}:
        return None
    if (
        len(raw) >= 2
        and raw[0] == raw[-1]
        and raw[0] in {"'", '"'}
    ):
        return raw[1:-1]
    return raw


def _next_content_line(lines: list[str], start: int) -> tuple[int, str] | None:
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if stripped and not stripped.startswith("#"):
            return idx, lines[idx].rstrip()
    return None


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    """
    Parse the small YAML subset used in Skill frontmatter when PyYAML is absent.

    This intentionally supports only mappings, scalar values, and scalar lists,
    which is enough for execute_policy, completion_policy, and input_schema.
    """
    lines = text.splitlines()

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        next_line = _next_content_line(lines, index)
        if next_line is None:
            return {}, index

        _, first_raw = next_line
        is_list = _line_indent(first_raw) == indent and first_raw.strip().startswith("- ")
        container: Any = [] if is_list else {}

        i = index
        while i < len(lines):
            raw = lines[i].rstrip()
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            current_indent = _line_indent(raw)
            if current_indent < indent:
                break
            if current_indent > indent:
                # Nested content is consumed by the parent key that introduced it.
                break

            if isinstance(container, list):
                if not stripped.startswith("- "):
                    break
                item = stripped[2:].strip()
                container.append(_parse_scalar(item))
                i += 1
                continue

            if ":" not in stripped:
                i += 1
                continue

            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                container[key] = _parse_scalar(value)
                i += 1
                continue

            child_line = _next_content_line(lines, i + 1)
            if child_line is None:
                container[key] = {}
                i += 1
                continue

            child_index, child_raw = child_line
            child_indent = _line_indent(child_raw)
            if child_indent <= current_indent:
                container[key] = {}
                i += 1
                continue

            child, new_index = parse_block(child_index, child_indent)
            container[key] = child
            i = new_index

        return container, i

    parsed, _ = parse_block(0, 0)
    return parsed if isinstance(parsed, dict) else {}


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return default


def _normalize_completion_policy(value: Any) -> SkillCompletionPolicy:
    if not isinstance(value, dict):
        return SkillCompletionPolicy()
    enabled = _coerce_bool(value.get("enabled"), False)
    action_kind = str(value.get("action_kind", "other")).strip() or "other"
    completion_effect = str(value.get("completion_effect", "none")).strip() or "none"
    allowed_action_kinds = {"ban_ip", "notify", "closure", "collect_context", "other"}
    allowed_effects = {"containment", "notification", "closure", "none"}
    if action_kind not in allowed_action_kinds:
        action_kind = "other"
    if completion_effect not in allowed_effects:
        completion_effect = "none"
    return SkillCompletionPolicy(
        enabled=enabled,
        action_kind=action_kind,
        completion_effect=completion_effect,
    )


def _normalize_skill_category(value: Any) -> str:
    category = str(value or "other").strip().lower() or "other"
    return category if category in {"query", "disposal", "other"} else "other"


class SentinelFlowSkillLoader:
    """
    Discover and load SentinelFlow skills from a plugin directory.

    Each skill lives in its own sub-directory and must contain a SKILL.md
    file whose YAML frontmatter holds all configuration metadata:

        ---
        name: my-skill
        description: One-line description shown to the agent
        type: doc           # or: hybrid
        # --- hybrid-only fields ---
        mode: subprocess
        entry: main.py
        execute_policy:
          enabled: true
          approval_required: false
          audit: true
        ---

        # Skill documentation body (what the agent reads)
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_skill_dirs(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_dir())

    def list_skills(self) -> list[SentinelFlowSkill]:
        skills: list[SentinelFlowSkill] = []
        for path in self.list_skill_dirs():
            try:
                skills.append(self.load_from_dir(path))
            except SkillConfigurationError:
                continue
        return skills

    def load(self, name: str) -> SentinelFlowSkill:
        return self.load_from_dir(self.root / name)

    def load_from_dir(self, skill_dir: Path) -> SentinelFlowSkill:
        if not skill_dir.is_dir():
            raise SkillConfigurationError(
                f"Skill directory does not exist: {skill_dir}"
            )

        doc_path = skill_dir / "SKILL.md"
        if not doc_path.is_file():
            raise SkillConfigurationError(
                f"Missing SKILL.md in {skill_dir}"
            )

        raw_text = doc_path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw_text)

        if not meta:
            raise SkillConfigurationError(
                f"SKILL.md has no frontmatter in {skill_dir}"
            )

        spec = self._build_spec(skill_dir, doc_path, meta)
        # Expose the full original markdown (frontmatter + body) to agents
        return SentinelFlowSkill(spec=spec, markdown=raw_text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_spec(
        self, skill_dir: Path, doc_path: Path, meta: dict[str, Any]
    ) -> SkillSpec:
        name = str(meta.get("name", "")).strip()
        description = str(meta.get("description", "")).strip()
        type_raw = str(meta.get("type", "doc")).strip().lower()

        # Legacy alias kept for hand-crafted files
        if type_raw == "exec":
            type_raw = "hybrid"

        if not name:
            raise SkillConfigurationError(
                f"'name' is required in SKILL.md frontmatter: {skill_dir}"
            )
        if not description:
            raise SkillConfigurationError(
                f"'description' is required in SKILL.md frontmatter: {skill_dir}"
            )

        try:
            skill_type = SkillType(type_raw)
        except ValueError as exc:
            raise SkillConfigurationError(
                f"Unsupported skill type {type_raw!r} in {skill_dir}"
            ) from exc

        # --- mode (runtime mode for hybrid skills) ---
        mode_raw = str(meta.get("mode", "")).strip().lower()
        runtime_mode: SkillRuntimeMode | None = None
        if mode_raw:
            try:
                runtime_mode = SkillRuntimeMode(mode_raw)
            except ValueError as exc:
                raise SkillConfigurationError(
                    f"Unsupported mode {mode_raw!r} in {skill_dir}"
                ) from exc

        # --- execute_policy block ---
        exec_policy: dict[str, Any] = {}
        raw_policy = meta.get("execute_policy")
        if isinstance(raw_policy, dict):
            exec_policy = raw_policy

        execute_enabled = _coerce_bool(
            exec_policy.get("enabled", skill_type == SkillType.HYBRID), False
        )
        approval_required = _coerce_bool(exec_policy.get("approval_required"), False)
        audit_enabled = _coerce_bool(exec_policy.get("audit", True), True)

        # --- entry (only meaningful for hybrid) ---
        entry: str | None = None
        if skill_type == SkillType.HYBRID:
            entry_raw = str(meta.get("entry", "main.py")).strip()
            if runtime_mode is None:
                raise SkillConfigurationError(
                    f"Hybrid skill requires 'mode' in frontmatter: {skill_dir}"
                )
            entry_path = skill_dir / entry_raw
            if not entry_path.is_file():
                raise SkillConfigurationError(
                    f"Skill entry file not found: {entry_path}"
                )
            entry = entry_raw

        # --- optional schemas ---
        input_schema: dict[str, Any] = {}
        output_schema: dict[str, Any] = {}
        if isinstance(meta.get("input_schema"), dict):
            input_schema = meta["input_schema"]
        if isinstance(meta.get("output_schema"), dict):
            output_schema = meta["output_schema"]

        return SkillSpec(
            name=name,
            type=skill_type,
            description=description,
            base_dir=skill_dir,
            doc_path=doc_path,
            category=_normalize_skill_category(meta.get("category")),
            entry=entry,
            mode=runtime_mode,
            input_schema=input_schema,
            output_schema=output_schema,
            execute_enabled=execute_enabled,
            approval_required=approval_required,
            audit_enabled=audit_enabled,
            completion_policy=_normalize_completion_policy(meta.get("completion_policy")),
        )
