import type { CallStatus, Stage } from "../api";

export type StepKey = "uploading" | "converting" | "diarizing" | "transcribing" | "translating" | "analyzing" | "done";
export type StepState = "complete" | "current" | "upcoming" | "failed";

export interface Step {
  key: StepKey;
  label: string;
  state: StepState;
}

const LABELS: Record<StepKey, string> = {
  uploading: "Uploading",
  converting: "Converting",
  diarizing: "Identifying speakers",
  transcribing: "Transcribing",
  translating: "Translating",
  analyzing: "Summarizing",
  done: "Done",
};

const STAGE_TO_STEP: Record<Stage, StepKey> = {
  queued: "converting",
  converting: "converting",
  diarizing: "diarizing",
  segmenting: "transcribing",
  transcribing: "transcribing",
  translating: "translating",
  analyzing: "analyzing",
  done: "done",
};

export interface StepInput {
  diarizationEnabled: boolean;
  analysisEnabled?: boolean;
  /** Null while the file is still uploading. */
  stage: Stage | null;
  status: CallStatus | null;
}

export function buildSteps({ diarizationEnabled, analysisEnabled = false, stage, status }: StepInput): Step[] {
  const keys: StepKey[] = ["uploading", "converting"];
  if (diarizationEnabled || stage === "diarizing") keys.push("diarizing");
  keys.push("transcribing", "translating");
  if (analysisEnabled || stage === "analyzing") keys.push("analyzing");
  keys.push("done");

  const finished = status === "completed" || status === "partial" || (stage === "done" && status !== "failed");
  const currentKey: StepKey = stage === null ? "uploading" : STAGE_TO_STEP[stage];
  const currentIndex = finished ? keys.length : keys.indexOf(currentKey);

  return keys.map((key, i) => {
    let state: StepState;
    if (i < currentIndex) state = "complete";
    else if (i === currentIndex) state = status === "failed" ? "failed" : "current";
    else state = "upcoming";
    return { key, label: LABELS[key], state };
  });
}
