"""bundle.json / bundle.js emission from a generic, wave-grouped Op list plus a
caller-supplied human-row-renderer and page-side allowlist. Zero domain knowledge lives
here -- the renderer and allowlist are the only place consumer-specific shape enters, and
both are plain data/functions the consumer supplies, not something this module interprets.

A "wave" is the consumer's own grouping of ops into a signable-and-verifiable-together unit
(for `fides_land`: UNSTAKE / STAKE-NOW / STAKE-DEFERRED; a generic single-shot consumer
just supplies one wave). Within a wave, ops are further chunked at `op.chunk_ops`'s
op-count/byte ceiling -- one `requestBroadcast` call per chunk. Each chunk is its own
ledger `unit_id`: `<wave>` when the wave fit in one chunk, `<wave>.<chunk_index>` when it
needed more than one.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from . import op as op_config
from .op import DEFAULT_MAX_BYTES_PER_TX, Op, chunk_ops, op_key_tier, required_account, required_key_tier

RowRenderer = Callable[[Op], dict[str, Any]]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def unit_id(wave_name: str, chunk_index: int, total_chunks: int) -> str:
    return wave_name if total_chunks <= 1 else f"{wave_name}.{chunk_index}"


def compute_batch_id(bundle_body: dict[str, Any]) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    digest = hashlib.sha256(_canonical(bundle_body).encode("utf-8")).hexdigest()[:8]
    return f"{stamp}-{digest}"


class BundleError(Exception):
    pass


def build_bundle(
    waves: list[tuple[str, list[Op]]],
    *,
    consumer: str,
    renderer: RowRenderer,
    allowlist: dict[str, Any],
    observed_at: str | None = None,
    source_hash: str | None = None,
    max_ops_per_tx: int | None = None,
    max_bytes_per_tx: int = DEFAULT_MAX_BYTES_PER_TX,
    assumptions: list[str] | None = None,
    wave_summarizer: Callable[[str, list[Op]], dict[str, Any]] | None = None,
    confirm_command_template: str | None = None,
) -> dict[str, Any]:
    """Builds the generic `{header, waves[]}` bundle body. `renderer(op) -> row dict`,
    `allowlist`, and `wave_summarizer(wave_name, ops) -> dict` are consumer-supplied data;
    this function never interprets their contents, only carries them through to the
    artifact. `wave_summarizer` is how a consumer's "N cards, M deeds, expected net delta"
    style per-wave header (plan §8) reaches the page without the core knowing what those
    words mean."""
    if max_ops_per_tx is None:
        max_ops_per_tx = op_config.DEFAULT_MAX_OPS_PER_TX
    if not waves:
        raise BundleError("cannot build a bundle with no waves")
    all_ops: list[Op] = [op for _name, ops in waves for op in ops]
    if not all_ops:
        raise BundleError("cannot build a bundle from an empty op list")
    account = required_account(all_ops)
    key_tier = required_key_tier(all_ops)

    wave_bodies = []
    for wave_name, ops in waves:
        chunks = chunk_ops(ops, max_ops_per_tx=max_ops_per_tx, max_bytes_per_tx=max_bytes_per_tx) if ops else []
        chunk_bodies = []
        for index, chunk in enumerate(chunks):
            # `chunk_ops` never mixes tiers within a chunk (fides/op.py), so ONE call to
            # `required_key_tier` here is exact for the whole chunk, not just the majority of
            # it -- this is the tier the signer page actually signs and broadcasts THIS unit
            # at (/Aletheia finding B), never `header.key_tier`, which is only the
            # bundle-wide informational figure (see `header`'s own comment below).
            chunk_tier = required_key_tier(chunk)
            chunk_bodies.append(
                {
                    "unit_id": unit_id(wave_name, index, len(chunks)),
                    "chunk_index": index,
                    "key_tier": chunk_tier,
                    "ops": [op.as_custom_json() for op in chunk],
                    # Each row also carries the OP's own tier (== `chunk_tier`, since a chunk
                    # is tier-homogeneous by construction) -- redundant with the chunk-level
                    # field today, but it is what lets a row render its own tier without the
                    # page having to thread `chunk_tier` through `renderRow`, and it stays
                    # correct if a future consumer's renderer groups differently.
                    "rows": [{**renderer(op), "tier": op_key_tier(op)} for op in chunk],
                    "keychain_operations": [op.as_keychain_tuple() for op in chunk],
                }
            )
        wave_bodies.append({
            "wave": wave_name,
            "op_count": len(ops),
            "chunks": chunk_bodies,
            "summary": wave_summarizer(wave_name, ops) if wave_summarizer else {},
        })

    header = {
        "consumer": consumer,
        "account": account,
        "key_tier": key_tier,
        "observed_at": observed_at,
        "source_hash": source_hash,
        "generated_at": _now_iso(),
        "max_ops_per_tx": max_ops_per_tx,
        "max_bytes_per_tx": max_bytes_per_tx,
        "allowlist": allowlist,
        "assumptions": assumptions or [],
        "confirm_command_template": confirm_command_template or "python3 -m fides confirm {batch_id} --unit {unit_id} --tx {tx_id}",
    }
    body = {"header": header, "waves": wave_bodies}
    body["header"]["batch_id"] = compute_batch_id(body)
    return body


def all_unit_ids(body: dict[str, Any]) -> list[str]:
    return [chunk["unit_id"] for wave in body["waves"] for chunk in wave["chunks"]]


def ops_for_unit(body: dict[str, Any], target_unit_id: str) -> list[dict[str, Any]]:
    for wave in body["waves"]:
        for chunk in wave["chunks"]:
            if chunk["unit_id"] == target_unit_id:
                return chunk["ops"]
    raise BundleError(f"no such unit_id in bundle: {target_unit_id!r}")


def write_bundle(body: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Writes `bundle.json` (canonical artifact) and `bundle.js` (byte-identical payload
    wrapped as `window.FIDES_BUNDLE = {...};`). Immutable once written: refuses to overwrite
    either file with different content. Each file is written to a temp path in the same
    directory and atomically renamed into place, so a process killed mid-write leaves either
    no file or a complete one -- never a half-written bundle a later read could misparse.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bundle.json"
    js_path = out_dir / "bundle.js"
    canonical = json.dumps(body, indent=2, sort_keys=True)
    js_content = f"window.FIDES_BUNDLE = {json.dumps(body, sort_keys=True, separators=(',', ':'))};\n"
    for path, content in ((json_path, canonical), (js_path, js_content)):
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != content:
                raise FileExistsError(f"{path} already exists with different content -- bundles are immutable once written")
            continue
        tmp_path = path.with_suffix(path.suffix + f".tmp{time.time_ns()}")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
    return json_path, js_path


def load_bundle(out_dir: Path) -> dict[str, Any]:
    return json.loads((out_dir / "bundle.json").read_text(encoding="utf-8"))
