from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from sentinelflow.domain.models import SkillExecutionRequest, SkillExecutionResult, SkillReadResult
from sentinelflow.skills.executor import SentinelFlowSkillExecutor
from sentinelflow.skills.loader import SentinelFlowSkillLoader
from sentinelflow.skills.resolver import SentinelFlowSkillResolver


class SentinelFlowSkillRuntime:
    """Unified runtime entrypoint used by tools, workflows, and agents."""

    def __init__(self, skill_root: Path) -> None:
        self.loader = SentinelFlowSkillLoader(skill_root)
        self.resolver = SentinelFlowSkillResolver(skill_root)
        self.executor = SentinelFlowSkillExecutor()

    def list_skills(self) -> list[str]:
        return [skill.name for skill in self.loader.list_skills()]

    def read_skill(self, name: str) -> SkillReadResult:
        skill = self.resolver.resolve(name)
        return SkillReadResult(
            name=skill.spec.name,
            type=skill.spec.type,
            description=skill.spec.description,
            category=skill.spec.category,
            markdown=skill.markdown,
            executable=skill.executable,
            approval_required=skill.spec.approval_required,
            input_schema=skill.spec.input_schema,
            output_schema=skill.spec.output_schema,
            entry=skill.spec.entry,
            mode=skill.spec.mode,
            completion_policy=asdict(skill.spec.completion_policy),
        )

    def execute_skill(
        self,
        name: str,
        arguments: dict | None = None,
        context: dict | None = None,
    ) -> SkillExecutionResult:
        skill = self.resolver.resolve(name)
        request = SkillExecutionRequest(
            name=name,
            arguments=arguments or {},
            context=context or {},
        )
        result = self.executor.execute(skill, request)
        # Enforce the skill contract: data must be a dict so that downstream
        # consumers (agent tools, evaluate_worker_result) can safely parse it
        # as JSON. Non-dict results indicate a malformed skill implementation.
        if result.success and not isinstance(result.data, dict):
            return SkillExecutionResult(
                success=False,
                skill=name,
                error=(
                    f"Skill [{name}] returned a non-dict value "
                    f"(got {type(result.data).__name__!r}). "
                    "Skill entry must return a JSON-serialisable dict."
                ),
                data={"raw": result.data} if result.data is not None else {},
            )
        return result
