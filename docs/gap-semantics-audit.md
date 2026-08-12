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

Revision 2 also writes `continuity_intervals.parquet`. Each row preserves the
last observed pre-gap quote, first observed post-gap quote, duration, spread
change, midpoint jump, quote validity and an explicit prohibition on synthetic
fills. Interruptions up to 60 seconds are classified as short for operational
research, with 30/60/120-second thresholds retained only as return-blind
diagnostics.

The source data currently lacks an effective-dated broker XAU session calendar,
separate bid/ask update flags, receive timestamps and source sequences. Therefore
the artifact honestly labels market state as unknown; it does not infer that a
long silence was a scheduled closure.

The report's recommendation is a research decision only. Any revised continuity
rule must be frozen and tested on new development or prospective data rather than
chosen from Candidate A returns.

An optional `--session-calendar` accepts a verified, effective-dated JSON schedule.
The loader refuses example or unverified schedules, requires authoritative-source
provenance and hashes the exact configuration. With a calendar, interruptions are
classified as `scheduled_closed`, `unexpected_open_time_interruption`, or
`calendar_out_of_effective_range`. The included example is intentionally invalid
until its placeholder schedule and provenance are replaced and independently
verified against the exact broker/server instrument.
