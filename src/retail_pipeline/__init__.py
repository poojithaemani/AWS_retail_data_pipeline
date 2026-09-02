"""Retail data pipeline - the transformation logic that runs inside Glue.

Deliberately one module. `transforms.py` holds the seven functions the brief
names, and nothing else lives here yet: sub-packages for extract, quality and
common were removed rather than left empty, because a package that exists in
anticipation of code is just a directory to explain.

The Glue entry point is NOT in this package - it is
`scripts/glue_jobs/curated_sales.py`. That file imports `awsglue`, which cannot
be installed locally, so keeping it out means everything in here is importable
and testable without AWS.
"""

__all__ = ["transforms"]
