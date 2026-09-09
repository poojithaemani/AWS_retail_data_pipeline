"""Repository hygiene checks.

This repo is authored on Windows but ships shell scripts that run under bash,
and Terraform that must stay formatted. Both are easy to break silently — a
stray carriage return produces the famously unhelpful `\\r: command not found`
at the worst possible moment, halfway through a teardown.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {".sh", ".py", ".tf", ".hcl", ".json", ".md", ".yaml", ".yml", ".env", ".sql", ".txt", ".toml"}


def tracked_files() -> list[Path]:
    """Every file Git would care about: tracked plus untracked-but-not-ignored.

    Deliberately not just `git ls-files`. While work is in progress nothing may
    be committed yet, and a checker that silently scans zero files is worse than
    no checker at all -- it reports green regardless. `--others
    --exclude-standard` picks up new work while still honouring .gitignore.
    """
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip("not a git working tree")
    files = [REPO_ROOT / line for line in result.stdout.split("\n") if line.strip()]
    assert files, "no files to check -- the hygiene suite would pass vacuously"
    return files


def test_no_crlf_in_tracked_text_files() -> None:
    """Windows editors and naive Python writes both introduce CRLF."""
    offenders = [
        path.relative_to(REPO_ROOT)
        for path in tracked_files()
        if path.suffix in TEXT_SUFFIXES and path.exists() and b"\r\n" in path.read_bytes()
    ]
    assert not offenders, f"CRLF line endings found in: {offenders}"


def test_text_files_are_valid_utf8() -> None:
    offenders = []
    for path in tracked_files():
        if path.suffix not in TEXT_SUFFIXES or not path.exists():
            continue
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            offenders.append(path.relative_to(REPO_ROOT))
    assert not offenders, f"not valid UTF-8: {offenders}"


def test_no_mojibake() -> None:
    """Catch double-encoded UTF-8.

    Python's `read_text()` on Windows defaults to cp1252, not UTF-8. Reading a
    UTF-8 file that way and writing it back mangles every non-ASCII character
    into sequences like `â€”` — and the damage is lossy, so it cannot be
    reliably reversed afterwards. Always pass `encoding="utf-8"`, or read and
    write bytes.
    """
    markers = ("â€", "â†", "â”", "Ã¢", "Â ", "�")
    offenders = []
    for path in tracked_files():
        if path.suffix not in TEXT_SUFFIXES or not path.exists():
            continue
        # This file necessarily contains the markers it searches for.
        if path.name == Path(__file__).name:
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        found = [m for m in markers if m in text]
        if found:
            offenders.append(f"{path.relative_to(REPO_ROOT)} {found}")
    assert not offenders, f"double-encoded text found in: {offenders}"


def test_shell_scripts_have_a_shebang() -> None:
    for path in (REPO_ROOT / "scripts").glob("*.sh"):
        first = path.read_bytes().split(b"\n", 1)[0]
        assert first.startswith(b"#!"), f"{path.name} has no shebang"


def test_no_aws_account_id_hardcoded_in_terraform() -> None:
    """Account ids belong in data.aws_caller_identity, not in source.

    Hardcoding one makes the configuration silently wrong in any other
    account, which defeats the rebuild-from-empty-account requirement.
    """
    pattern = re.compile(r"\b\d{12}\b")
    offenders = []
    for path in (REPO_ROOT / "infrastructure").rglob("*.tf"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line) and "account_id" not in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"hardcoded account id in: {offenders}"


def _project_env_exports() -> set[str]:
    text = (REPO_ROOT / "config" / "project.env").read_bytes().decode("utf-8")
    return set(re.findall(r"^export ([A-Za-z_][A-Za-z0-9_]*)=", text, re.M))


def _terraform_variables() -> dict[str, dict[str, bool]]:
    """Every declared variable, and whether it carries a default."""
    found: dict[str, dict[str, bool]] = {}
    for path in (REPO_ROOT / "infrastructure").rglob("variables.tf"):
        layer = path.parent.name
        text = path.read_text(encoding="utf-8")
        for block in re.finditer(r'variable "(\w+)" \{(.*?)\n\}', text, re.S):
            name, body = block.group(1), block.group(2)
            has_default = re.search(r"^\s*default\s*=", body, re.M) is not None
            found[f"{layer}.{name}"] = {"has_default": has_default}
    return found


# Variables that are legitimately supplied at runtime rather than by
# config/project.env: de.sh computes two of them per invocation, and the
# budget subscriber is personal so it lives in the gitignored tfvars.
RUNTIME_SUPPLIED = {"state_bucket", "phase", "budget_notification_email"}


def test_no_terraform_default_shadows_project_env() -> None:
    """One value, one home.

    A Terraform `default` alongside a `TF_VAR_` export in project.env means the
    same value is defined twice, and the copies drift silently: Terraform run
    through de.sh picks up the export, Terraform run bare picks up the default,
    and the two disagree without anything failing.
    """
    exports = _project_env_exports()
    shadowed = [
        name
        for name, meta in _terraform_variables().items()
        if meta["has_default"] and f"TF_VAR_{name.split('.', 1)[1]}" in exports
    ]
    assert not shadowed, (
        "these Terraform variables have a default AND a TF_VAR_ export in "
        f"config/project.env - remove one: {shadowed}"
    )


def test_every_required_variable_has_a_source() -> None:
    """The other half: a variable with no default and no export fails at apply."""
    exports = _project_env_exports()
    orphaned = [
        name
        for name, meta in _terraform_variables().items()
        if not meta["has_default"]
        and name.split(".", 1)[1] not in RUNTIME_SUPPLIED
        and f"TF_VAR_{name.split('.', 1)[1]}" not in exports
    ]
    assert not orphaned, (
        "no default and nothing exports them, so apply will fail: " f"{orphaned}"
    )


def test_config_json_does_not_restate_project_env() -> None:
    """dev.json and pipeline.json must not duplicate identity values."""
    import json

    banned = {"region", "project", "domain", "database", "workgroup"}
    offenders = []
    for name in ("dev.json", "pipeline.json"):
        data = json.loads((REPO_ROOT / "config" / name).read_text(encoding="utf-8"))

        def walk(node: object, path: str = "") -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in banned:
                        offenders.append(f"{name}:{path}{key}")
                    walk(value, f"{path}{key}.")

        walk(data)
    assert not offenders, (
        "identity belongs in config/project.env, not restated here: " f"{offenders}"
    )


def test_iam_tag_values_are_valid() -> None:
    r"""IAM tag values are more restrictive than S3 or KMS tag values.

    IAM permits only ``[\p{L}\p{Z}\p{N}_.:/=+\-@]`` -- notably **no commas**.
    S3 and KMS accept them, so a comma in a shared tag block fails only on the
    IAM resources, and only at apply time. That is an expensive place to find
    out: it cost a partially-applied persistent layer, 22 resources in and 3
    short.
    """
    allowed = re.compile(r"^[\w\s_.:/=+\-@]*$", re.UNICODE)
    offenders = []
    for path in (REPO_ROOT / "infrastructure").rglob("*.tf"):
        text = path.read_text(encoding="utf-8")
        for block in re.finditer(r"tags\s*=\s*\{(.*?)\n\s*\}", text, re.S):
            for line in block.group(1).splitlines():
                match = re.match(r'\s*(\w+)\s*=\s*"([^"]*)"', line)
                if not match:
                    continue
                key, value = match.groups()
                # Interpolations resolve to project names and ids, all safe.
                probe = re.sub(r"\$\{[^}]*\}", "X", value)
                if not allowed.match(probe):
                    bad = sorted({c for c in probe if not allowed.match(c)})
                    offenders.append(f"{path.name}: {key}={value!r} contains {bad}")
    assert not offenders, "invalid IAM tag values: " + "; ".join(offenders)


def test_cost_log_table_is_last() -> None:
    """`verify` appends rows with >>, so the table must end the file.

    Prose written below the table means the next appended row lands outside it
    and the table stops rendering. That happened once already: an explanatory
    paragraph was added after the rows, and the following session's entry
    appeared as a stray line of pipes.
    """
    lines = [
        line
        for line in (REPO_ROOT / "docs" / "cost-log.md")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert lines[-1].startswith("|"), (
        "docs/cost-log.md must end with the table; the last non-blank line is: "
        f"{lines[-1]!r}"
    )


def test_terraform_is_formatted() -> None:
    result = subprocess.run(
        ["terraform", "fmt", "-check", "-recursive", str(REPO_ROOT / "infrastructure")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"run './scripts/de.sh fmt':\n{result.stdout}"


def test_no_state_files_are_tracked() -> None:
    tracked = {path.name for path in tracked_files()}
    leaked = {name for name in tracked if name.endswith(".tfstate") or name.endswith(".tfvars")}
    assert not leaked, f"Terraform state or vars committed: {leaked}"


def test_glue_job_zip_preserves_the_package_directory() -> None:
    """The zip Glue receives must contain retail_pipeline/, not bare modules.

    `archive_file` zips the *contents* of `source_dir`. Pointing it at
    `src/retail_pipeline` therefore produces an archive whose root is
    `transforms.py`, with no enclosing package -- and since Glue puts the zip
    itself on `sys.path`, the entry script's `from retail_pipeline import
    transforms` fails with ModuleNotFoundError at startup, after the run has
    been billed. The distinction is invisible in a plan and costs a job run to
    discover, so it is asserted here instead.
    """
    body = (REPO_ROOT / "infrastructure" / "training" / "glue_job.tf").read_text(
        encoding="utf-8"
    )
    match = re.search(r'source_dir\s*=\s*"\$\{path\.module\}/\.\./\.\./([^"]+)"', body)
    assert match, "archive_file.retail_pipeline has no recognisable source_dir"
    assert match.group(1) == "src", (
        "source_dir must be src/ so the archive keeps retail_pipeline/ inside it; "
        f"found src-relative path {match.group(1)!r}"
    )

    package_dir = REPO_ROOT / "src" / "retail_pipeline"
    assert (package_dir / "__init__.py").is_file(), "retail_pipeline must be a package"

    # Anything else added under src/ must be excluded explicitly, or it ends up
    # shipped to Glue -- generate/ drags in pandas for no runtime reason.
    excludes = re.search(r"excludes\s*=\s*\[(.*?)\]", body, re.S)
    assert excludes, "archive_file.retail_pipeline must declare excludes"
    excluded = set(re.findall(r'"([^"]+)"', excludes.group(1)))
    shipped = {
        entry.name
        for entry in (REPO_ROOT / "src").iterdir()
        if entry.is_dir() and entry.name != "retail_pipeline"
    }
    assert shipped <= excluded, (
        f"these src/ directories would be shipped to Glue unexcluded: {shipped - excluded}"
    )


def test_no_lifecycle_expiry_can_reach_the_raw_layer() -> None:
    """An expiration rule must name a derived prefix, never the whole bucket.

    This is a regression test for a real data loss. `lake.tf` carried a single
    lifecycle rule with `filter {}` -- every object in the bucket -- and a seven
    day expiry. It deleted the raw layer: forty-two order partitions plus the
    customer and product files, leaving only the two objects young enough to
    survive the window.

    The bucket policy did not help and could not. DenyRawObjectDeletion refuses
    s3:DeleteObject by *principals*; lifecycle expiry is carried out by S3
    against no principal at all, so the policy never sees it. raw/ was protected
    from deletion by callers and entirely exposed to deletion by configuration.

    An `abort_incomplete_multipart_upload` rule is exempt: it removes only the
    fragments of uploads that never finished and cannot remove a whole object.
    """
    body = (REPO_ROOT / "infrastructure" / "persistent" / "lake.tf").read_text(encoding="utf-8")

    start = body.find('resource "aws_s3_bucket_lifecycle_configuration"')
    assert start != -1, "no lifecycle configuration found in lake.tf"
    block = body[start:]

    # Split into rule bodies, keeping only those that actually expire objects.
    rules = re.split(r"\n\s*(?:dynamic\s+)?\"?rule\"?\s*\{", block)[1:]
    expiring = [r for r in rules if re.search(r"^\s*expiration\s*\{", r, re.M)]
    assert expiring, "expected at least one expiration rule to guard"

    offenders = []
    for rule in expiring:
        head = rule[: rule.find("expiration")]
        # A prefix may be a literal or, in a dynamic block, the iterator value.
        has_prefix = re.search(r"prefix\s*=\s*(\"[^\"]+\"|rule\.value)", head)
        if not has_prefix or re.search(r"filter\s*\{\s*\}", head):
            offenders.append(" ".join(rule.split())[:90])

    assert not offenders, (
        "these lifecycle rules expire objects without naming a prefix, so they "
        f"would delete raw/ as well: {offenders}"
    )

    assert '"raw/"' not in block, "raw/ must never appear in a lifecycle expiry rule"
