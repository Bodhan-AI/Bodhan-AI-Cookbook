"""Child process for test_research_regressions.test_resume_after_a_hard_process_kill.

    python _kill_resume_child.py first|resume <tmp_path>

`first` translates a file, then blocks inside the next planner call until the parent kills
the process. `resume` continues the run with --resume and prints the fake API call counts.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from fakes import FakeBodhan, ScriptedChat
from test_agent_e2e import args, last_ok

from bodhan_agent.cli import run
from bodhan_agent.llm import PlannerLLM


def main() -> None:
    phase, tmp = sys.argv[1], Path(sys.argv[2])
    txt = tmp / "p.txt"
    fake = FakeBodhan()
    if phase == "first":
        plan = [("record_plan", {"steps": ["translate", "speak"], "deliverables": [{"kind": "audio", "language": "hi"}]})]

        def hang(messages):
            time.sleep(600)  # the parent kills the process here: no finally blocks, no clean shutdown

        chat = ScriptedChat([plan, [("translate_text", {"path": str(txt), "target_language": "hi"})], hang])
        asyncio.run(run(args(tmp, "Read this aloud in Hindi", [txt]), transport=fake.transport, llm=PlannerLLM(client=chat, model="s")))
    else:
        chat = ScriptedChat([
            lambda m: [("synthesize_speech", {"path": last_ok(m, "path"), "language": "hi", "format": "wav"})],
            lambda m: [("finish", {"status": "completed", "final_answer": "Done.", "deliverables": [last_ok(m, "path")]})],
        ])
        a = args(tmp, None, [])
        a.resume = True
        asyncio.run(run(a, transport=fake.transport, llm=PlannerLLM(client=chat, model="s")))
    print(json.dumps(dict(fake.calls)))


if __name__ == "__main__":
    main()
