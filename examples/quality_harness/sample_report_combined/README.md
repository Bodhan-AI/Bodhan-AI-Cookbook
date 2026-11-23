# Combined demo report

This run scored 108 translations and selected 70 items for speech checks. It reused cached judge
verdicts and some cached Bodhan outputs. All 70 selected items reached transcription-based checks
without reported speech-check errors or budget skips. The run's Bodhan ledger recorded ₹3.440338
in new model charges; judge-provider charges were incurred separately during the earlier runs.

The 54 injected labels describe translation errors, not speech errors. Unperturbed items have not
been human-reviewed, so the report does not establish a false-alarm rate. See
`../docs/evaluation_evidence.md` for the speech-fault checks and limitations.
