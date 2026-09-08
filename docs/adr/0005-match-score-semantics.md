# Match score semantics stay deliberately uncalibrated

The Match score is a 0–100 heuristic blended from title similarity, duration closeness, and a flat channel bonus; the exact constants live in `_score_candidate` in `engine.py`. This record pins the four semantics below as deliberate, so that future changes recalibrate this ADR explicitly instead of drifting. None of the numbers behind them were derived from measurement — the score is a working heuristic, not a calibrated ranking guarantee, and that is the accepted state.

- **Organic matches peak at 98, not 100.** The weighted blend maxes at 55 + 35 + 8; the scorer never outputs 100. A 100 is assigned only outside the scorer — a manual override, or a Track taken directly from a YouTube playlist (which is the chosen item itself, so nothing is scored). Override and organic scores are therefore knowingly comparable: 100 always means "not scorer-ranked", never "the scorer was certain".
- **Unknown duration scores as a perfect duration Match.** When either the Track or the candidate carries no duration, the duration component takes its perfect score rather than a penalty. Absence of evidence is not evidence of mismatch; title similarity carries the discrimination in that case.
- **Two different string-similarity functions, on purpose.** Candidate scoring uses `fuzz.token_set_ratio` on the raw query against the candidate title; duplicate detection (`_is_same_song`) uses `fuzz.token_sort_ratio` on normalized title and artist. They answer different questions — ranking fuzzy YouTube titles versus deciding "is this the same song" — and are tuned independently; unifying them would silently change both behaviors.
- **The default confidence threshold is an uncalibrated heuristic.** The default of 75 was chosen by inspection, not by measuring precision and recall on a corpus, and it is exposed as a user setting. Moving it is the intended knob for perceived Match score accuracy, not a bug fix.

## Consequences

- Any change to the ceiling, the unknown-duration handling, the similarity-function split, or the default threshold must amend this ADR in the same change.
- The score's shape (a weighted blend with a hard ceiling) is stable across scorer refactors; retuning the component weights is allowed and does not need a new decision.
