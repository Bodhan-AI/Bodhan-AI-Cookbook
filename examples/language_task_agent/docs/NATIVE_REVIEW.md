# Language quality and human review

The assignment's assessors perform native-language output review. Obtaining our
own fluent reviewer is optional additional assurance, not a stated prerequisite
for opening/submitting the PR. The unperformed-review status below remains honest;
prepared forms and automated checks cannot be represented as a human approval.

No fluent human review has been performed or arranged. No external reviewer was
contacted. Round-trip scores, script evidence and matching transcripts are screens,
not native-language correctness certificates.

The later supplement adds five distinct synthetic clips and a code-rendered
translation index. Development review caught a planner rewriting failure in an
intermediate completed run; see EVALUATION.md. The corrected index copies actual
Bodhan translations. Do not approve the older misleading list merely because its
report says completed. A review packet provides hashes, sources and blank review
forms; a prepared packet is not a completed human review.

The final evidence contains the exact inputs/outputs/hashes for review:

- Tamil: the original eight-page school-garden chapter, matching English/Tamil
  summaries and 115.456s MP3; all nine audio chunks have passed back-ASR checks.
- Marathi: 4x4 table, numeric values18/24/30 preserved, source image and translation.
- Telugu: the later five-clip batch has distinct hashes; clips1/4 are selected
  with script evidence and verbatim English translations. The older duplicate
  fixture is historical. Ambiguous shared scripts remain unknown.
- Hindi: a licensed continuous natural180s recording; 35readable SRT cues after processing the full180s; source anchors, caption-free
  intervals and estimated phrase timing are disclosed. The earlier synthetic loop remains historical.
- Bengali:8.96s Ishita WAV plus source/text, with the partial/corrected lineage.

A fluent reviewer should compare source/target meaning, names/numbers, omissions,
added facts, pronunciation, repetition and intelligibility. Record reviewer identity,
fluency, date, artifact SHA256 and timestamps/examples for issues. Do not silently
edit Indic text; corrections must still use Bodhan and repeat verification.

Candidate understanding and live-change rehearsal are also pending. WALKTHROUGH.md
is preparation, not a claim of participation. No automated artifact can substitute
for the candidate's own explanation of the code.
