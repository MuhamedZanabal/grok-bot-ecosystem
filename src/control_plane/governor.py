"""Bounded-loop governor: iteration, wall-clock and cost budgets with sticky terminal states.

Admission is checked *before* each unit of work, and cost is reserved up front, so a
budget can never be overspent by the action that would exceed it.
"""

from dataclasses import dataclass
from enum import Enum
import math
import threading
import time
from typing import Callable

from .killswitch import KillSwitch


class LoopState(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CYCLE_ABORTED = "cycle_aborted"
    KILLED = "killed"


@dataclass(frozen=True)
class Budget:
    max_iterations: int
    max_wall_seconds: float
    max_cost: float

    def __post_init__(self) -> None:
        for name in ("max_iterations", "max_wall_seconds", "max_cost"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ValueError(f"{name} must be a finite non-negative number")


class LoopGovernor:
    def __init__(self, budget: Budget, kill_switch: KillSwitch | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.budget = budget
        self._kill = kill_switch
        self._clock = clock
        self._start = clock()
        self.iterations = 0
        self.spent = 0.0
        self.state = LoopState.RUNNING
        self.reason = ""
        self._lock = threading.Lock()

    def _terminate(self, state: LoopState, reason: str) -> LoopState:
        if self.state is LoopState.RUNNING:
            self.state, self.reason = state, reason
        return self.state

    def admit(self, cost: float = 0.0) -> LoopState:
        """Reserve one iteration and `cost`. Returns RUNNING if admitted, else terminal state."""
        with self._lock:
            return self._admit(cost)

    def _admit(self, cost: float) -> LoopState:
        if self.state is not LoopState.RUNNING:
            return self.state
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise ValueError("cost must be a finite non-negative number")
        if self._kill is not None and self._kill.engaged():
            return self._terminate(LoopState.KILLED, "kill_switch_engaged")
        if self.iterations >= self.budget.max_iterations:
            return self._terminate(LoopState.CYCLE_ABORTED, "iteration_budget_exhausted")
        if self._clock() - self._start >= self.budget.max_wall_seconds:
            return self._terminate(LoopState.CYCLE_ABORTED, "wall_clock_budget_exhausted")
        if self.spent + cost > self.budget.max_cost:
            return self._terminate(LoopState.CYCLE_ABORTED, "cost_budget_exhausted")
        self.iterations += 1
        self.spent += cost
        return LoopState.RUNNING

    def complete(self) -> LoopState:
        return self._terminate(LoopState.COMPLETED, "completed")
