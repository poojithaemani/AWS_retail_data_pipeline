# Cost log

Appended automatically by `./scripts/de.sh verify` at the end of every session.

Cost Explorer lags roughly 8 hours, so the figure recorded on any given row is
usually the *previous* session's spend settling. Read the trend, not the row.

Target: under $50 for the whole project. Idle cost after a verified teardown
should be a few cents a month (S3 storage + KMS key).

**The `3 survivor(s)` row on 2026-08-27 is kept deliberately.** `verify` ran
while the training layer was still standing and correctly refused to report
clean. That row is the proof the check fails when it should — a teardown
verifier that has only ever returned "clean" has not been tested.

Rows from 2026-08-22 and 2026-08-24 were removed: they predate `bootstrap`, so
they recorded an empty account rather than a real session.

> **The table must stay at the end of this file.** `verify_teardown.sh` appends
> each new row with `>>`, so anything written below the table lands outside it
> and breaks the rendering. That already happened once.

| Date (UTC) | Phase | Month-to-date | Teardown |
| --- | --- | ---: | --- |
| 2026-08-27 | 00 | $-0.0000014582 | clean |
| 2026-08-27 | 00 | $-0.0000014582 | 3 survivor(s) |
| 2026-08-27 | 00 | $-0.0000014582 | clean |
| 2026-08-31 | 01 | $-0.0000016052 | clean |
| 2026-08-31 | 01 | $-0.0000016003 | clean |
| 2026-09-01 | 02 | $-0.0000000266 | clean |
| 2026-09-02 | 03 | $-0.0000000266 | clean |
| 2026-09-03 | 05 | $-0.000000115 | clean |
| 2026-09-08 | n/a | $-0.0000004456 | clean |
| 2026-09-09 | 07 | $-0.0000005088 | clean |
| 2026-09-09 | 08 | $-0.0000005355 | clean |
| 2026-09-10 | 09 | $0.009996845 | clean |
| 2026-09-10 | n/a | $-0.0000060783 | clean |
| 2026-09-14 | n/a | $-0.0000294641 | clean |
| 2026-09-14 | n/a | $-0.0000349858 | clean |
| 2026-09-15 | n/a | $-0.0000427808 | clean |
