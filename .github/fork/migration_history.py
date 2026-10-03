"""Preserve published Alembic history and merge independent revision branches."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Optional

VERSIONS = Path("gpustack/migrations/versions")
APPROVALS = Path(".github/fork/migration_repairs.json")


def _identifiers(value):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ValueError(f"Invalid revision identifiers: {value!r}")


class _WithoutDocstrings(ast.NodeTransformer):
    def generic_visit(self, node):
        node = super().generic_visit(node)
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    node.body = body[1:]
        return node


def _semantic_tree(value):
    # Empty optional AST fields vary between supported Python versions.
    if isinstance(value, ast.AST):
        result = {"node": type(value).__name__}
        for name, field in ast.iter_fields(value):
            if field is not None and field != []:
                result[name] = _semantic_tree(field)
        return result
    if isinstance(value, list):
        return [_semantic_tree(item) for item in value]
    return value


def read_revisions(directory: Path) -> dict:
    """Read identifiers and semantic fingerprints without importing migration code."""
    revisions = {}
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in (
                    "revision",
                    "down_revision",
                    "depends_on",
                ):
                    values[target.id] = ast.literal_eval(node.value)
        if "revision" not in values or "down_revision" not in values:
            raise ValueError(f"Missing revision identifiers in {path}")
        revision = values["revision"]
        if not isinstance(revision, str) or revision in revisions:
            raise ValueError(f"Invalid or duplicate revision in {path}: {revision!r}")
        semantic = json.dumps(
            _semantic_tree(_WithoutDocstrings().visit(tree)), sort_keys=True
        )
        revisions[revision] = {
            "path": path,
            "parents": _identifiers(values["down_revision"]),
            "dependencies": _identifiers(values.get("depends_on")),
            "fingerprint": hashlib.sha256(semantic.encode()).hexdigest(),
        }
    if not revisions:
        raise ValueError(f"No migration revisions found in {directory}")
    for revision in revisions:
        ancestors(revisions, revision)
    return revisions


def ancestors(revisions: dict, revision: str, visiting=()) -> set:
    """Return ancestry including dependencies, rejecting missing nodes and cycles."""
    if revision in visiting:
        raise ValueError(f"Cycle in migration history at {revision}")
    if revision not in revisions:
        raise ValueError(f"Missing migration revision {revision}")
    result = {revision}
    node = revisions[revision]
    for parent in node["parents"] + node["dependencies"]:
        result.update(ancestors(revisions, parent, visiting + (revision,)))
    return result


def heads(revisions: dict) -> list:
    """Return unreferenced revision heads in deterministic order."""
    referenced = {
        parent
        for node in revisions.values()
        for parent in node["parents"] + node["dependencies"]
    }
    return sorted(set(revisions) - referenced)


def check_history(previous: dict, current: dict, repairs: dict) -> None:
    """Reject rewritten published history unless a pending repair covers the change."""
    for revision, old in previous.items():
        if revision not in current:
            raise ValueError(f"Published migration {revision} was removed")
        new = current[revision]
        if (old["parents"], old["dependencies"]) != (
            new["parents"],
            new["dependencies"],
        ):
            raise ValueError(f"Published migration {revision} changed ancestry")
        if old["fingerprint"] == new["fingerprint"]:
            continue
        repair = repairs.get(revision, {})
        repair_id = repair.get("repair_revision")
        if repair.get("fingerprint") != new["fingerprint"]:
            raise ValueError(
                f"Published migration {revision} changed. Add a new repair revision "
                "and record its exact fingerprint in migration_repairs.json: "
                f"{new['fingerprint']}"
            )
        if repair_id in previous or repair_id not in current:
            raise ValueError(f"Repair for {revision} must be a new, pending revision")
        covered = ancestors(current, repair_id)
        if not {revision, *heads(previous)} <= covered:
            raise ValueError(f"Repair {repair_id} does not follow published heads")
        if not repair.get("reason"):
            raise ValueError(f"Repair for {revision} needs a review reason")


def merge_heads(directory: Path) -> Optional[Path]:
    """Add a merge revision without editing any existing revision."""
    revisions = read_revisions(directory)
    parents = heads(revisions)
    if len(parents) == 1:
        return None
    revision = hashlib.sha256("\n".join(parents).encode()).hexdigest()[:12]
    if revision in revisions:
        raise ValueError(f"Merge revision identifier collision: {revision}")
    path = directory / f"{revision}_fork_merge.py"
    path.write_text(
        '"""Merge upstream and fork migration branches.\n\n'
        f"Revision ID: {revision}\nRevises: {', '.join(parents)}\n"
        '"""\n\n'
        f"revision = {revision!r}\n"
        f"down_revision = {tuple(parents)!r}\n"
        "branch_labels = None\ndepends_on = None\n\n\n"
        "def upgrade() -> None:\n    pass\n\n\n"
        "def downgrade() -> None:\n    pass\n"
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("merge", "check"))
    parser.add_argument("--current-dir", type=Path, default=VERSIONS)
    parser.add_argument("--previous-dir", type=Path)
    parser.add_argument("--repairs", type=Path, default=APPROVALS)
    args = parser.parse_args()
    if args.action == "merge":
        path = merge_heads(args.current_dir)
        if path:
            print(path)
    else:
        if args.previous_dir is None:
            parser.error("check requires --previous-dir")
        repairs = json.loads(args.repairs.read_text()) if args.repairs.exists() else {}
        check_history(
            read_revisions(args.previous_dir), read_revisions(args.current_dir), repairs
        )


if __name__ == "__main__":
    main()
