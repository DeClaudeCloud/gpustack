# Fork migration and publication checks

Published Alembic revision identifiers and ancestry must remain stable. The
backend sync merges upstream commits and adds a merge revision when upstream
and the fork have independent migration heads. It never re-parents an existing
revision. Databases on either parent branch execute the missing branch before
crossing the merge point.

`migration_history.py` compares migration syntax trees, ignoring comments and
docstrings. Removing a published revision or changing its parents always fails.
Changing executable code in a published revision requires an explicit repair.
The sync checks against its starting commit; image publication also checks
against the actual migration files inside the previous published image.

## When upstream changes an applied bundle

1. Merge upstream into `feat/request-logs` locally and run
   `python3 .github/fork/migration_history.py merge` to join independent heads.
2. Add a new guarded repair revision after the resulting head. It must deliver
   the skipped schema or data changes on existing installations and preserve
   objects and values already present on fresh installations.
3. Run `python3 .github/fork/migration_history.py check --previous-dir PATH`,
   where `PATH` contains the baseline migration files. A changed revision's error
   includes its semantic fingerprint.
4. Record that fingerprint, the new `repair_revision`, and a review `reason` in
   `migration_repairs.json`, keyed by the changed upstream revision ID. The repair
   must be absent from the baseline and follow every baseline head. Updating a
   fingerprint alone cannot approve a repair that was already applied.
5. Add regression tests, run `make lint` and `make test`, then push normally.

The approval records a reviewed change; it does not generate or execute repair
SQL. Future edits to the same bundle produce another fingerprint and need
another pending repair revision.

## Image gate

The image workflow pins the previous `:dev` digest, builds and loads a local
candidate, and runs `verify_image_upgrade.py` before pushing any image tag.
It verifies both the previous image and a pinned September 29 canary digest,
also retained by `:dev-95a314d`, so databases from before the deployment-history
bundle changes remain covered. Keep the canary tag available in GHCR.

For each baseline, the gate creates a disposable PostgreSQL database, migrates
it with the baseline image, saves a deployment, upgrades it with the candidate,
and checks that the deployment survives. It also executes a read of every
application table and compares the upgraded schema with a fresh candidate
database, including columns, defaults, indexes, uniqueness and foreign keys.
Temporary containers, their volumes and the network are removed on failure
and success. No deployment database is accessed.

Run the gate manually with Docker available:

```sh
python3 .github/fork/verify_image_upgrade.py \
  --candidate local-candidate:dev \
  --previous ghcr.io/declaudecloud/gpustack@sha256:BASELINE_DIGEST \
  --previous ghcr.io/declaudecloud/gpustack:dev-95a314d
```

Publication fails if the registry's `:dev` baseline changes during verification.
Rerun the workflow to verify against that new baseline. Schema coverage uses
PostgreSQL; supported compatible databases still require portable migration
DDL and their existing unit checks. Arbitrary production data transformations
need focused regression tests in addition to the deployment canary.
