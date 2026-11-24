class Budget:
    """Counts LLM turns and rupees. It is plain code, so the LLM cannot talk its way past it."""

    def __init__(self, max_steps: int, max_cost_inr: float, call_timeout: int):
        self.max_steps = max_steps
        self.max_cost = max_cost_inr
        self.call_timeout = call_timeout
        self.steps = 0
        self.cost = 0.0

    def exhausted(self) -> str | None:
        if self.steps >= self.max_steps:
            return f"step limit reached ({self.max_steps} steps)"
        if self.cost >= self.max_cost:
            return f"cost limit reached (Rs {self.max_cost})"
        return None

    def can_afford(self, estimate: float) -> bool:
        return self.cost + estimate <= self.max_cost

    def add_cost(self, amount: float) -> None:
        self.cost += amount