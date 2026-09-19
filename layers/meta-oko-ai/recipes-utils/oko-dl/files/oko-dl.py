#!/usr/bin/env python3
# Copyright 2026 Satisfanly Ltd
#
# OKO OS is a product of Satisfanly Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Download Hugging Face GGUFs manually or from an OKO model-runner config."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

import yaml

__version__ = "0.5"
HF_HOST = "huggingface.co"
USER_AGENT = f"oko-dl/{__version__}"
SPLIT_GGUF_RE = re.compile(r"^(?P<prefix>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)


class OkoDlError(RuntimeError):
    pass


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def human_size(value: int | None) -> str:
    if value is None:
        return "unknown size"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"


def auth_headers() -> dict[str, str]:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_hf_url(url: str) -> tuple[str, str, str | None]:
    """Parse a Hugging Face model URL.

    Returns (repo_id, revision, target).  target is None for repository/tree
    URLs, which means the model GGUF must be discovered automatically.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise OkoDlError("expected an http(s) Hugging Face URL")
    if parsed.hostname not in (HF_HOST, f"www.{HF_HOST}"):
        raise OkoDlError(f"unsupported host: {parsed.hostname!r}; expected {HF_HOST}")

    parts = [unquote(p) for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise OkoDlError(
            "URL must point to a Hugging Face model repository or GGUF file, e.g. "
            "https://huggingface.co/OWNER/REPO"
        )

    owner, repo = parts[0], parts[1]
    repo_id = f"{owner}/{repo}"

    # Repository root: https://huggingface.co/OWNER/REPO
    if len(parts) == 2:
        return repo_id, "main", None

    route = parts[2]

    # Repository tree: https://huggingface.co/OWNER/REPO/tree/main
    if route == "tree":
        if len(parts) < 4 or not parts[3]:
            raise OkoDlError("Hugging Face tree URL does not contain a revision")
        if len(parts) > 4:
            raise OkoDlError(
                "subdirectory tree URLs are not supported; pass the repository root or /tree/REVISION"
            )
        return repo_id, parts[3], None

    # Exact file URL: /blob/REV/file.gguf, /resolve/REV/file.gguf, /raw/REV/file.gguf
    if route not in ("blob", "resolve", "raw"):
        raise OkoDlError(
            f"unsupported Hugging Face route {route!r}; expected repository root, tree, blob, resolve, or raw"
        )
    if len(parts) < 5:
        raise OkoDlError("Hugging Face file URL does not contain a revision and file path")

    revision = parts[3]
    path_in_repo = "/".join(parts[4:])
    if not path_in_repo:
        raise OkoDlError("URL does not contain a file path")
    return repo_id, revision, path_in_repo


def api_model_info(repo_id: str, revision: str) -> dict[str, Any]:
    api_url = (
        f"https://{HF_HOST}/api/models/{quote(repo_id, safe='/')}"
        f"?revision={quote(revision, safe='')}&files_metadata=true"
    )
    req = Request(api_url, headers=auth_headers())
    try:
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise OkoDlError(
                f"Hugging Face denied access to {repo_id!r}; export HF_TOKEN for gated/private repos"
            ) from exc
        if exc.code == 404:
            raise OkoDlError(f"repository or revision not found: {repo_id}@{revision}") from exc
        raise OkoDlError(f"Hugging Face API returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise OkoDlError(f"cannot reach Hugging Face API: {exc.reason}") from exc


def sibling_files(info: dict[str, Any]) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for sibling in info.get("siblings", []):
        name = sibling.get("rfilename")
        if not isinstance(name, str):
            continue
        size = sibling.get("size")
        if not isinstance(size, int):
            lfs = sibling.get("lfs")
            size = lfs.get("size") if isinstance(lfs, dict) and isinstance(lfs.get("size"), int) else None
        result[name] = size
    return result


def split_group(target: str, files: dict[str, int | None]) -> list[str]:
    base = target.rsplit("/", 1)[-1]
    match = SPLIT_GGUF_RE.match(base)
    if not match:
        return [target]

    prefix = match.group("prefix")
    total = int(match.group("total"))
    parent = target.rsplit("/", 1)[0] + "/" if "/" in target else ""
    expected = [f"{parent}{prefix}-{i:05d}-of-{total:05d}.gguf" for i in range(1, total + 1)]
    missing = [name for name in expected if name not in files]
    if missing:
        raise OkoDlError("split GGUF detected but shard(s) are missing: " + ", ".join(missing))
    return expected


def is_mmproj(name: str) -> bool:
    return name.lower().endswith(".gguf") and "mmproj" in name.rsplit("/", 1)[-1].lower()


def has_name_token(name: str, token: str) -> bool:
    """Return True when TOKEN appears as a dash/dot/underscore-delimited filename token."""
    base = name.rsplit("/", 1)[-1]
    return re.search(
        rf"(^|[-_.]){re.escape(token)}(?:[-_.]|$)",
        base,
        flags=re.I,
    ) is not None


def has_mtp(name: str) -> bool:
    """Whether a GGUF filename advertises embedded MTP/FastMTP support."""
    return (
        name.lower().endswith(".gguf")
        and re.search(
            r"(^|[-_.])(?:fast)?mtp(?:[-_.]|$)",
            name.rsplit("/", 1)[-1],
            flags=re.I,
        ) is not None
    )


def is_mtp_sidecar(name: str) -> bool:
    """Best-effort identification of a *standalone* MTP draft GGUF.

    Embedded-MTP target models commonly contain ``-MTP-`` or ``-mtp`` in
    their filename, so that marker alone is not enough to call a file a
    sidecar.  Standalone llama.cpp MTP companions normally start with
    ``mtp-``/``fastmtp-``.  HauhauCS-style FastMTP files also use FastMTP
    as a dedicated sidecar marker anywhere in the basename.
    """
    if not name.lower().endswith(".gguf") or is_mmproj(name):
        return False

    base = name.rsplit("/", 1)[-1]
    if re.match(r"^(?:fast)?mtp[-_.]", base, flags=re.I):
        return True
    if has_name_token(name, "FASTMTP"):
        return True
    return False


def has_tools_variant(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return re.search(r"(^|[-_.])TOOLS?(?:[-_.]|$)", base, flags=re.I) is not None


def has_max_variant(name: str) -> bool:
    return has_name_token(name, "MAX")


def logical_gguf_representatives(names: list[str]) -> list[str]:
    """Return one representative path for every logical GGUF (including sharded GGUFs)."""
    representatives: dict[str, str] = {}
    for name in sorted(names):
        base = name.rsplit("/", 1)[-1]
        match = SPLIT_GGUF_RE.match(base)
        if match:
            parent = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
            key = f"{parent}{match.group('prefix')}-of-{match.group('total')}"
            # Prefer shard 1 as the representative when it exists.
            if key not in representatives or int(match.group("part")) == 1:
                representatives[key] = name
        else:
            representatives[name] = name
    return list(representatives.values())


def complete_split_group(target: str, files: dict[str, int | None]) -> list[str] | None:
    """Return the complete logical GGUF group, or None for an incomplete split GGUF."""
    try:
        return split_group(target, files)
    except OkoDlError:
        return None


def logical_size(target: str, files: dict[str, int | None]) -> int:
    """Total known size of a GGUF/all shards; -1 if sizes are unknown or shards are incomplete."""
    group = complete_split_group(target, files)
    if group is None:
        return -1
    sizes = [files.get(name) for name in group]
    known = [size for size in sizes if isinstance(size, int)]
    return sum(known) if known else -1


def precision_score(name: str) -> int:
    """Best-effort precision/quant score from a filename; larger means higher precision."""
    base = name.rsplit("/", 1)[-1].upper()

    # Float formats are higher precision than integer quants.
    if re.search(r"(^|[-_.])F32(?:[-_.]|$)", base):
        return 3200
    if re.search(r"(^|[-_.])(?:BF16|F16|FP16)(?:[-_.]|$)", base):
        return 1600
    if re.search(r"(^|[-_.])(?:BF8|F8|FP8)(?:[-_.]|$)", base):
        return 800

    # Standard GGUF quant names: Q8_0, Q8_K, Q8_K_P, IQ4_XS, etc.
    match = re.search(r"(^|[-_.])I?Q(?P<bits>\d+)(?:[-_.]|$)", base)
    if match:
        return int(match.group("bits")) * 100
    return -1


def select_best(candidates: list[str], files: dict[str, int | None]) -> str | None:
    """Pick the highest-precision candidate, then the largest file at that precision."""
    if not candidates:
        return None
    reps = [
        name for name in logical_gguf_representatives(candidates)
        if complete_split_group(name, files) is not None
    ]
    if not reps:
        return None
    return max(reps, key=lambda name: (precision_score(name), logical_size(name, files), name.lower()))


def q8_variant_preference(name: str, files: dict[str, int | None]) -> tuple[int, int, int, int, int, str]:
    """Rank Q8 models *within* either the TOOL or non-TOOL family.

    TOOL-ness is handled by the caller.  Within a family, prefer models that
    combine MAX + embedded MTP, then MAX, then MTP, then the largest logical
    file.  This avoids depending on one exact vendor-specific filename while
    still preferring the feature-rich variant when those markers exist.
    """
    max_variant = int(has_max_variant(name))
    mtp = int(has_mtp(name) and not is_mtp_sidecar(name))
    features = max_variant + mtp
    all_requested = int(features == 2)
    return (
        all_requested,
        features,
        max_variant,
        mtp,
        logical_size(name, files),
        name.lower(),
    )


def q8_model_candidates(files: dict[str, int | None]) -> list[str]:
    model_candidates = [
        name for name in files
        if (
            name.lower().endswith('.gguf')
            and not is_mmproj(name)
            and not is_mtp_sidecar(name)
        )
    ]
    return [
        name for name in logical_gguf_representatives(model_candidates)
        if (
            complete_split_group(name, files) is not None
            and re.search(
                r'(^|[-_.])Q8(?:[-_.]|$)',
                name.rsplit('/', 1)[-1],
                flags=re.I,
            )
        )
    ]


def select_q8_models(files: dict[str, int | None]) -> list[str]:
    """Select the best Q8 TOOL and best Q8 non-TOOL variants.

    Repository/tree URLs intentionally download both families when both are
    available.  This is deliberately heuristic: TOOL/TOOLS, MAX and MTP are
    treated as independent filename markers rather than requiring one exact
    hard-coded vendor filename.
    """
    q8_candidates = q8_model_candidates(files)
    if not q8_candidates:
        model_candidates = [
            name for name in files
            if name.lower().endswith('.gguf') and not is_mmproj(name) and not is_mtp_sidecar(name)
        ]
        available = ', '.join(logical_gguf_representatives(model_candidates)[:12]) or 'none'
        raise OkoDlError(
            'could not auto-discover a Q8 GGUF in the repository; '
            f'available model GGUFs include: {available}'
        )

    tools = [name for name in q8_candidates if has_tools_variant(name)]
    normal = [name for name in q8_candidates if not has_tools_variant(name)]

    selected: list[str] = []
    if tools:
        selected.append(max(tools, key=lambda name: q8_variant_preference(name, files)))
    if normal:
        selected.append(max(normal, key=lambda name: q8_variant_preference(name, files)))

    # Defensive fallback: q8_candidates is non-empty, so this should only be
    # reachable if classification changes in the future.
    if not selected:
        selected.append(max(q8_candidates, key=lambda name: q8_variant_preference(name, files)))
    return selected


def discover_files(
    target: str | None,
    files: dict[str, int | None],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Resolve model GGUFs plus shared mmproj/MTP companions.

    Exact file URLs select exactly that model.  Repository/tree URLs select up
    to two Q8 models: best TOOL and best non-TOOL.
    """
    if target is None:
        targets = select_q8_models(files)
    else:
        if target not in files:
            raise OkoDlError(f'{target!r} was not found in the repository at the requested revision')
        targets = [target]

    model_files: list[str] = []
    for selected_target in targets:
        model_files.extend(split_group(selected_target, files))
    model_files = list(dict.fromkeys(model_files))
    model_set = set(model_files)

    mmproj_candidates = [
        name for name in files
        if is_mmproj(name) and name not in model_set
    ]

    # Only fetch a standalone MTP sidecar when at least one selected target
    # does not already contain embedded MTP tensors.
    needs_mtp_sidecar = any(
        not (has_mtp(selected_target) and not is_mtp_sidecar(selected_target))
        for selected_target in targets
    )
    mtp_candidates = [
        name for name in files
        if is_mtp_sidecar(name) and name not in model_set
    ]

    mmproj_target = select_best(mmproj_candidates, files)
    mtp_target = select_best(mtp_candidates, files) if needs_mtp_sidecar else None
    mmproj = split_group(mmproj_target, files) if mmproj_target is not None else []
    mtp = split_group(mtp_target, files) if mtp_target is not None else []

    return targets, model_files, mmproj, mtp

def resolve_url(repo_id: str, revision: str, path_in_repo: str) -> str:
    return (
        f"https://{HF_HOST}/{quote(repo_id, safe='/')}/resolve/"
        f"{quote(revision, safe='')}/{quote(path_in_repo, safe='/')}"
    )


def progress_line(name: str, done: int, total: int | None, started: float, base: int = 0) -> str:
    elapsed = max(time.monotonic() - started, 0.001)
    transferred = max(done - base, 0)
    speed = transferred / elapsed
    if total and total > 0:
        percent = min(done * 100.0 / total, 100.0)
        return f"\r{name}: {percent:6.2f}%  {human_size(done)} / {human_size(total)}  {human_size(int(speed))}/s"
    return f"\r{name}: {human_size(done)}  {human_size(int(speed))}/s"


def download_file(
    repo_id: str,
    revision: str,
    remote_path: str,
    destination_root: Path,
    expected_size: int | None,
    force: bool,
) -> None:
    destination = destination_root / remote_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")

    if destination.exists() and not force:
        if expected_size is None or destination.stat().st_size == expected_size:
            print(f"Already present: {destination}")
            return
        eprint(f"Existing file has unexpected size; re-downloading: {destination}")
        destination.unlink()

    if force:
        destination.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)

    resume_from = partial.stat().st_size if partial.exists() else 0
    headers = auth_headers()
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
        print(f"Resuming {remote_path} from {human_size(resume_from)} of {human_size(expected_size)}")
    else:
        print(f"Downloading {remote_path} ({human_size(expected_size)})")

    req = Request(resolve_url(repo_id, revision, remote_path), headers=headers)
    try:
        response = urlopen(req, timeout=60)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise OkoDlError(f"download denied for {remote_path!r}; set HF_TOKEN if required") from exc
        raise OkoDlError(f"download failed for {remote_path!r}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise OkoDlError(f"download failed for {remote_path!r}: {exc.reason}") from exc

    with response:
        status = getattr(response, "status", response.getcode())
        if resume_from and status != 206:
            eprint(f"Server did not honor Range for {remote_path}; restarting")
            resume_from = 0
            mode = "wb"
        else:
            mode = "ab" if resume_from else "wb"

        content_length = response.headers.get("Content-Length")
        response_length = int(content_length) if content_length and content_length.isdigit() else None
        total = expected_size if expected_size is not None else (
            resume_from + response_length if response_length is not None else None
        )

        started = time.monotonic()
        done = resume_from
        last_update = 0.0
        with open(partial, mode) as output:
            while True:
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last_update >= 0.2:
                    print(progress_line(remote_path.rsplit("/", 1)[-1], done, total, started, resume_from), end="", flush=True)
                    last_update = now
            output.flush()
            os.fsync(output.fileno())

    final_size = partial.stat().st_size
    print(progress_line(remote_path.rsplit("/", 1)[-1], final_size, total, started, resume_from))

    if expected_size is not None and final_size != expected_size:
        raise OkoDlError(
            f"size mismatch for {remote_path!r}: got {final_size} bytes, expected {expected_size}; "
            f"partial file kept at {partial}"
        )

    os.replace(partial, destination)
    print(f"Saved: {destination}")


def _gguf_path_from_token(token: str) -> str | None:
    """Return a GGUF path carried by a shell token, including --flag=PATH form."""
    candidate = token
    if token.startswith("-") and "=" in token:
        candidate = token.split("=", 1)[1]
    return candidate if candidate.lower().endswith(".gguf") else None


def configured_gguf_files(
    config_path: Path,
    models_root: Path,
) -> list[tuple[str, Path, str, str]]:
    """Extract GGUFs referenced by runner ``cmd`` fields.

    Returns tuples of (model_name, local_path, repo_id, remote_path). OKO's
    model directory convention is ``OWNER___REPO`` under ``models_root``, which
    makes the corresponding Hugging Face repository unambiguous.
    """
    try:
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as exc:
        raise OkoDlError(f"cannot read OKO config {str(config_path)!r}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise OkoDlError(f"cannot parse OKO config {str(config_path)!r}: {exc}") from exc

    if not isinstance(raw, dict) or not raw:
        raise OkoDlError("OKO config must be a non-empty YAML mapping")

    result: list[tuple[str, Path, str, str]] = []
    seen: set[Path] = set()

    for model_name, table in raw.items():
        if not isinstance(model_name, str) or not isinstance(table, dict):
            continue
        cmd = table.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            continue

        try:
            argv = shlex.split(cmd, posix=True)
        except ValueError as exc:
            raise OkoDlError(f"model {model_name!r}: cannot parse cmd: {exc}") from exc

        for token in argv:
            configured_path = _gguf_path_from_token(token)
            if configured_path is None:
                continue

            configured_path = os.path.expandvars(configured_path)
            if re.search(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[^}]+\})", configured_path):
                raise OkoDlError(
                    f"model {model_name!r}: unresolved environment variable in GGUF path "
                    f"{configured_path!r}"
                )

            local_path = Path(configured_path).expanduser()
            if not local_path.is_absolute():
                local_path = config_path.parent / local_path
            local_path = local_path.resolve()

            if local_path in seen:
                continue

            try:
                relative = local_path.relative_to(models_root)
            except ValueError as exc:
                raise OkoDlError(
                    f"model {model_name!r}: GGUF path {str(local_path)!r} is outside "
                    f"models root {str(models_root)!r}; cannot infer Hugging Face repository"
                ) from exc

            if len(relative.parts) < 2:
                raise OkoDlError(
                    f"model {model_name!r}: GGUF path {str(local_path)!r} must be under "
                    f"{str(models_root)!r}/OWNER___REPO/"
                )

            repo_dir = relative.parts[0]
            if "___" not in repo_dir:
                raise OkoDlError(
                    f"model {model_name!r}: model directory {repo_dir!r} does not use "
                    "the OWNER___REPO naming convention"
                )
            owner, repo = repo_dir.split("___", 1)
            if not owner or not repo:
                raise OkoDlError(
                    f"model {model_name!r}: invalid model directory {repo_dir!r}; "
                    "expected OWNER___REPO"
                )

            repo_id = f"{owner}/{repo}"
            remote_path = Path(*relative.parts[1:]).as_posix()
            seen.add(local_path)
            result.append((model_name, local_path, repo_id, remote_path))

    return result


def configured_reference_missing(local_path: Path, remote_path: str) -> bool:
    """Check whether a configured GGUF (including all named split shards) is missing."""
    match = SPLIT_GGUF_RE.match(remote_path.rsplit("/", 1)[-1])
    if not match:
        return not local_path.exists()

    prefix = match.group("prefix")
    total = int(match.group("total"))
    return any(
        not (local_path.parent / f"{prefix}-{part:05d}-of-{total:05d}.gguf").exists()
        for part in range(1, total + 1)
    )


def auto_download_from_config(
    config_path: Path,
    models_root: Path,
    force: bool,
) -> None:
    configured = configured_gguf_files(config_path, models_root)
    if not configured:
        print(f"No GGUF files referenced by: {config_path}")
        return

    wanted = configured if force else [
        item for item in configured
        if configured_reference_missing(item[1], item[3])
    ]
    present = len(configured) - len(wanted)

    print(f"Config      : {config_path}")
    print(f"Models root : {models_root}")
    print(f"Referenced  : {len(configured)} GGUF file(s)")
    if not force:
        print(f"Present     : {present}")
        print(f"Missing     : {len(wanted)}")

    if not wanted:
        print("All configured GGUF files are already present.")
        return

    by_repo: dict[str, list[tuple[str, Path, str]]] = {}
    for model_name, local_path, repo_id, remote_path in wanted:
        by_repo.setdefault(repo_id, []).append((model_name, local_path, remote_path))

    for repo_id, entries in by_repo.items():
        revision = "main"
        model_dir = models_root / repo_id.replace("/", "___")
        print()
        print(f"Repository : {repo_id}")
        print("Revision   : main")
        print("Inspecting repository...")

        info = api_model_info(repo_id, revision)
        files = sibling_files(info)

        remote_files: list[str] = []
        for model_name, _local_path, remote_path in entries:
            if remote_path not in files:
                raise OkoDlError(
                    f"model {model_name!r}: {remote_path!r} was not found in "
                    f"{repo_id}@{revision}"
                )
            remote_files.extend(split_group(remote_path, files))

        remote_files = list(dict.fromkeys(remote_files))
        print(f"Files      : {len(remote_files)}")
        for remote_path in remote_files:
            download_file(
                repo_id,
                revision,
                remote_path,
                model_dir,
                files.get(remote_path),
                force,
            )

    print()
    print("Done: all configured GGUF files are present.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oko-dl",
        description=(
            "Download Hugging Face GGUFs; repository/tree URLs auto-select both the best "
            "Q8 TOOL and best Q8 non-TOOL variants, preferring MAX+MTP within each family, "
            "plus highest-precision mmproj/standalone-MTP companions when needed."
        ),
    )
    parser.add_argument(
        "url",
        nargs="?",
        help=(
            "Hugging Face repository/tree URL (auto-select best Q8 TOOL + non-TOOL; "
            "prefer MAX+MTP) or blob/resolve/raw GGUF URL; omit with --auto"
        ),
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="parse oko-ai.yaml and download every referenced GGUF that is missing",
    )
    parser.add_argument(
        "--config",
        default="~/oko-ai.yaml",
        metavar="FILE",
        help="OKO runner YAML used by --auto (default: ~/oko-ai.yaml)",
    )
    parser.add_argument("--models-root", default="~/models", metavar="DIR", help="models directory (default: ~/models)")
    parser.add_argument("--force", action="store_true", help="download again even if destination exists")
    parser.add_argument("--no-mmproj", action="store_true", help="do not auto-download mmproj companion files")
    parser.add_argument("--no-mtp", action="store_true", help="do not auto-download MTP/FastMTP companion files")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        models_root = Path(args.models_root).expanduser().resolve()

        if args.auto:
            if args.url is not None:
                parser.error("URL cannot be used together with --auto")
            if args.no_mmproj or args.no_mtp:
                parser.error("--no-mmproj and --no-mtp are only valid in URL mode")
            config_path = Path(args.config).expanduser().resolve()
            auto_download_from_config(config_path, models_root, args.force)
            return 0

        if args.url is None:
            parser.error("URL is required unless --auto is used")

        repo_id, revision, target = parse_hf_url(args.url)
        if target is not None and not target.lower().endswith(".gguf"):
            raise OkoDlError("the supplied file URL must point to a .gguf file")

        model_dir = models_root / repo_id.replace("/", "___")
        model_dir.mkdir(parents=True, exist_ok=True)

        print(f"Repository : {repo_id}")
        print(f"Revision   : {revision}")
        print(f"Model dir  : {model_dir}")
        print("Inspecting repository...")

        info = api_model_info(repo_id, revision)
        files = sibling_files(info)
        discovered_targets, model_files, mmproj_files, mtp_files = discover_files(target, files)
        if target is None:
            for selected_target in discovered_targets:
                kind = "TOOLS" if has_tools_variant(selected_target) else "noTOOL"
                print(f"Auto Q8 {kind:6}: {selected_target}")
        else:
            print(f"Model      : {discovered_targets[0]}")

        selected = list(model_files)
        if not args.no_mmproj:
            print("mmproj     : " + (", ".join(mmproj_files) if mmproj_files else "not found"))
            selected.extend(mmproj_files)
        if not args.no_mtp:
            print("MTP        : " + (", ".join(mtp_files) if mtp_files else "not found"))
            selected.extend(mtp_files)

        selected = list(dict.fromkeys(selected))
        print(f"Files      : {len(selected)}")
        for name in selected:
            download_file(repo_id, revision, name, model_dir, files.get(name), args.force)

        print(f"Done: {model_dir}")
        return 0
    except KeyboardInterrupt:
        eprint("\nInterrupted; partial download was kept for resume.")
        return 130
    except OkoDlError as exc:
        eprint(f"oko-dl: error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

