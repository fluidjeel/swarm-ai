from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalCaseResult:
    fixture_id: str
    agent: str
    passed: bool
    schema_valid: bool
    behavior_valid: bool
    details: str = ""


@dataclass
class EvalReport:
    results: list[EvalCaseResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for item in self.results if item.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.results if not item.passed)

    def print_summary(self) -> None:
        print("A2A Eval Suite Report")
        print(f"  Total: {len(self.results)}")
        print(f"  Pass:  {self.passed_count}")
        print(f"  Fail:  {self.failed_count}")
        print()
        for item in self.results:
            status = "PASS" if item.passed else "FAIL"
            print(f"[{status}] {item.fixture_id} ({item.agent}) - {item.details}")

    def exit_code(self) -> int:
        return 0 if self.failed_count == 0 else 1
