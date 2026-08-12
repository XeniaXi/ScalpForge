# Gap-semantics audit

Candidate A currently rejects a one-hour path when any five-minute bar inherits a
one-second `is_gap_start` flag. That flag can represent a brief quote interruption
greater than five seconds; it does not necessarily mean that a five-minute bar or
an executable entry/exit quote is absent.

`scalpforge-audit-gap-semantics` audits this distinction on development data only.
It reports fixed duration buckets (5–30 seconds, 30–300 seconds, and over 300
seconds), actual five-minute discontinuities, and whether valid bid/ask quotes
bracket each interruption. It never evaluates P&L, changes the frozen candidate,
rebuilds labels, or reads the sealed holdout.

The report's recommendation is a research decision only. Any revised continuity
rule must be frozen and tested on new development or prospective data rather than
chosen from Candidate A returns.
