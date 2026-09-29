"""Pin a requirements file's installed dependency closure, without importing apps.

Run with the Python from the environment to capture:
    python scripts/lock_installed.py requirements.txt requirements.lock
Only plain PEP 508 requirement lines are accepted. No installs or network access.
"""
import argparse
from collections import deque
from importlib.metadata import distribution
from pathlib import Path
import platform

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError:
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name


def snapshot(source):
    pending = deque()
    for line in source.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            pending.append(Requirement(line))
    selected, visited = {}, set()
    while pending:
        req = pending.popleft()
        if req.marker and not req.marker.evaluate():
            continue
        if req.url:
            raise ValueError(f'URL requirement cannot be captured as a version pin: {req.name}')
        name = canonicalize_name(req.name)
        dist = distribution(name)
        if req.specifier and not req.specifier.contains(dist.version, prereleases=True):
            raise ValueError(f'{name} {dist.version} does not satisfy {req.specifier}')
        selected[name] = dist.version
        key = (name, frozenset(req.extras))
        if key in visited:
            continue
        visited.add(key)
        for raw in dist.requires or []:
            dep = Requirement(raw)
            if dep.marker and not any(dep.marker.evaluate({'extra': extra}) for extra in {'', *req.extras}):
                continue
            dep.marker = None  # already evaluated with the parent's requested extras
            pending.append(dep)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    selected = snapshot(args.source)
    header = (f'# Installed dependency snapshot for {args.source.name}.\n'
              f'# Python {platform.python_version()}, {platform.system()} {platform.machine()}.\n'
              '# Exact versions including transitive dependencies; no artifact hashes.\n'
              '# Regenerate with scripts/lock_installed.py after validating environment changes.\n')
    args.output.write_text(header + ''.join(f'{name}=={version}\n' for name, version in sorted(selected.items())))
    print(f'Pinned {len(selected)} distributions in {args.output}')


if __name__ == '__main__':
    main()
