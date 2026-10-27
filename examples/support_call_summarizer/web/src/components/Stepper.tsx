import type { Step } from "../lib/steps";

const STATE_TEXT: Record<Step["state"], string> = {
  complete: "completed",
  current: "in progress",
  upcoming: "not started",
  failed: "failed",
};

export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="stepper" aria-label="Processing steps">
      {steps.map((step, i) => (
        <li
          key={step.key}
          className={`stepper__step stepper__step--${step.state}`}
          aria-current={step.state === "current" ? "step" : undefined}
        >
          <span className="stepper__marker" aria-hidden="true">
            {step.state === "complete" ? "✓" : step.state === "failed" ? "!" : i + 1}
          </span>
          <span className="stepper__label">
            {step.label}
            <span className="visually-hidden"> ({STATE_TEXT[step.state]})</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
