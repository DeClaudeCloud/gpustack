"""Keep this fork's own Alembic revisions at the head of upstream's chain.

After a rebase onto upstream, an upstream revision added since the last sync
shares a parent with a fork revision, leaving two heads -- and the server
refuses to migrate. When exactly one fork revision and one upstream revision
are heads, the base of the fork's chain is re-parented onto the upstream one.

usage: repoint_migration.py <fork-revision-id> [<fork-revision-id> ...]
(all of the fork's revisions; they must form one chain)
Prints the rewritten file path, or nothing when the chain already has one head.
Exits non-zero when the situation needs a human.
"""

import re
import sys
from pathlib import Path

VERSIONS = Path("gpustack/migrations/versions")
REV = re.compile(r"^revision(?:: str)? = ['\"]([\w-]+)['\"]", re.M)
DOWN = re.compile(r"^down_revision(?:: [^=]+)? = (.+)$", re.M)


def parse():
    revisions = {}
    for path in VERSIONS.glob("*.py"):
        text = path.read_text()
        rev = REV.search(text)
        down = DOWN.search(text)
        if not rev or not down:
            continue
        parents = set(re.findall(r"['\"]([\w-]+)['\"]", down.group(1)))
        revisions[rev.group(1)] = (path, parents)
    return revisions


def main(fork_ids):
    revisions = parse()
    referenced = set().union(*(parents for _, parents in revisions.values()))
    heads = sorted(r for r in revisions if r not in referenced)
    if len(heads) == 1:
        return 0
    fork_heads = [h for h in heads if h in fork_ids]
    upstream_heads = [h for h in heads if h not in fork_ids]
    # The fork's revisions form one chain on top of upstream's; its base is
    # the one revision whose parent is upstream's, and that is what moves.
    bases = [r for r in fork_ids if r in revisions and not revisions[r][1] & fork_ids]
    if len(fork_heads) != 1 or len(upstream_heads) != 1 or len(bases) != 1:
        print(f"cannot resolve migration heads automatically: {heads}", file=sys.stderr)
        return 1
    base, theirs = bases[0], upstream_heads[0]
    path, parents = revisions[base]
    (old,) = parents
    text = path.read_text()
    text = re.sub(
        r"^(down_revision(?:: [^=]+)? = )['\"]" + old + r"['\"]",
        lambda m: f"{m.group(1)}'{theirs}'",
        text,
        count=1,
        flags=re.M,
    )
    text = re.sub(
        r"^Revises: " + old + r"$", f"Revises: {theirs}", text, count=1, flags=re.M
    )
    path.write_text(text)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(set(sys.argv[1:])))
