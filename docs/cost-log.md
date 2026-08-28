# Cost log

Appended automatically by `./scripts/de.sh verify` at the end of every session.

Cost Explorer lags roughly 8 hours, so the figure recorded on any given row is
usually the *previous* session's spend settling. Read the trend, not the row.

Target: under $50 for the whole project. Idle cost after a verified teardown
should be a few cents a month (S3 storage + KMS key).

| Date (UTC) | Phase | Month-to-date | Teardown |
| --- | --- | ---: | --- |
| 2026-08-27 | 00 | $-0.0000014582 | clean |
| 2026-08-27 | 00 | $-0.0000014582 | 3 survivor(s) |
| 2026-08-27 | 00 | $-0.0000014582 | clean |

The middle row is kept deliberately. `verify` ran while the training layer was
still standing and reported three survivors, which is the proof that the check
actually fails when it should. A teardown verifier that has only ever returned
"clean" has not been tested.

Rows from 2026-08-22 and 2026-08-24 were removed: they predate `bootstrap`, so
they recorded an empty account rather than a real session.
