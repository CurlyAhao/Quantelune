"""Rule registry: a versioned, extensible collection of rules."""

from __future__ import annotations

from app.quality.base import Rule

# Bump when the rule set changes materially (added/removed rules, altered
# semantics). Persisted with every quality run for reproducibility.
RULESET_VERSION = "quality-ruleset-v1"


class RuleRegistry:
    """Holds rule instances keyed by ``rule_id``."""

    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}

    def register(self, rule: Rule) -> None:
        if not rule.rule_id:
            raise ValueError("Rule must define a non-empty rule_id")
        if rule.rule_id in self._rules:
            raise ValueError(f"Duplicate rule_id: {rule.rule_id}")
        self._rules[rule.rule_id] = rule

    def get(self, rule_id: str) -> Rule | None:
        return self._rules.get(rule_id)

    def all(self) -> list[Rule]:
        return list(self._rules.values())

    def ids(self) -> list[str]:
        return list(self._rules.keys())

    def __len__(self) -> int:
        return len(self._rules)

    def describe(self) -> list[dict[str, object]]:
        """Static metadata about every registered rule (for docs/UI)."""
        return [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "description": r.description,
                "category": r.category.value,
                "default_severity": r.default_severity.value,
                "level": r.level.value,
                "applicable_types": r.applicable_types,
                "version": r.version,
                "supports_sampling": r.supports_sampling,
                "limitations": r.limitations,
            }
            for r in self._rules.values()
        ]
