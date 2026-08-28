#!/usr/bin/env python3
import argparse
import hashlib
import os
from pathlib import Path


DEFAULT_BYTES = 1_153_433_600
CHUNK_BYTES = 1024 * 1024
SEED = b"atakit-baby-container-tester-v0.2.0"


def parse_args():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Generate the deterministic measured workload payload."
    )
    parser.add_argument(
        "--bytes",
        type=int,
        default=int(os.environ.get("WORKLOAD_PAYLOAD_BYTES", DEFAULT_BYTES)),
        help=f"payload size in bytes (default: {DEFAULT_BYTES})",
    )
    parser.add_argument("--output", type=Path, default=root / "payload.bin")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing payload when its size differs",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.bytes <= 0:
        raise SystemExit("--bytes must be positive")

    output = args.output.resolve()
    if output.exists():
        current = output.stat().st_size
        if current == args.bytes:
            print(f"{output} already has {current} bytes")
            return
        if not args.force:
            raise SystemExit(
                f"{output} has {current} bytes; pass --force to replace it"
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    remaining = args.bytes
    index = 0
    with temporary.open("wb") as payload:
        while remaining:
            size = min(CHUNK_BYTES, remaining)
            material = SEED + index.to_bytes(8, "big")
            payload.write(hashlib.shake_256(material).digest(size))
            remaining -= size
            index += 1
    temporary.replace(output)
    print(f"generated {output}: {args.bytes} bytes")


if __name__ == "__main__":
    main()
