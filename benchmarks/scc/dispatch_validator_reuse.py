#!/usr/bin/env python3
"""Verify the user-authorized accounting-only continuation, without mutation.

Deploy the helper commit with deploy.sh to its own unused deployment run. Never
build it or substitute its numerical code for the original accepted bootstrap.
"""
import ast
from pathlib import Path
import sys
import tarfile

from dispatch_campaign import PROJECT, identity, require
from run_fused_task import sha256

REUSABLE_RUN = "20260905T151045Z-becd4ac-b2v1-dispatch"
REUSABLE_SOURCE = "becd4ac569c93aa26c6b07030cad0c08352cd4a4"
REUSABLE_ARCHIVE = "e989929028c434e9e7f14a17285a7de827fce4ace28031d0b00db57193a4b033"
REUSABLE_BINARY = "4f311d6e6ec11c045b2bb28fe7ad52897183fe01ff256bae0910b85f8eaa7a17"
SERIAL_RETRY_RUN = REUSABLE_RUN + "-serial1"
APPLICATION_RETRY_RUN = REUSABLE_RUN + "-app1"
ALLOWED_CHANGES = {
    "AGENTS.md", "benchmarks/scc/README.md", "benchmarks/scc/submit_dispatch.sh",
    "benchmarks/scc/dispatch_campaign.py", "benchmarks/scc/dispatch_validator_reuse.py",
    "benchmarks/scc/tests/test_dispatch_campaign.py",
    "benchmarks/scc/dispatch_serial_retry.py", "benchmarks/scc/dispatch_serial_launcher.py",
    "benchmarks/scc/run_dispatch_serial.sh",
}


def archive_files(source):
    archive = PROJECT / "source-archives" / f"{source}.tar"
    code = PROJECT / "code-b2" / source
    files = {}
    with tarfile.open(archive) as handle:
        require(handle.pax_headers.get("comment") == source, "archive Git identity mismatch")
        for member in handle:
            path = Path(member.name)
            require(not path.is_absolute() and ".." not in path.parts, "unsafe archive path")
            if member.isdir():
                continue
            require(member.isfile(), "unsupported archive entry")
            data = handle.extractfile(member).read()
            require(member.name not in files, "duplicate archive member")
            require((code / path).read_bytes() == data, f"deployed source differs: {path}")
            files[member.name] = data
    return files, sha256(archive)


def verify_delta(original, helper):
    changed = {p for p in original.keys() | helper.keys() if original.get(p) != helper.get(p)}
    require(changed <= ALLOWED_CHANGES, f"not an accounting-only continuation: {sorted(changed)}")
    # Everything executable in the campaign module except the accounting reader
    # must remain identical, including scientific checks and promotion thresholds.
    def without_parser(data):
        tree = ast.parse(data)
        tree.body = [node for node in tree.body if not
                     (isinstance(node, ast.FunctionDef) and node.name == "parse_qacct")]
        return ast.dump(tree, include_attributes=False)
    name = "benchmarks/scc/dispatch_campaign.py"
    require(without_parser(original[name]) == without_parser(helper[name]),
            "campaign changed beyond accounting parser")


def verify(run, validator):
    run, validator = run.resolve(), validator.resolve()
    source, archive, _, binary = identity(run)
    helper_source = validator.parents[1].name
    require(validator == PROJECT / "code-b2" / helper_source / "benchmarks/scc",
            "validator is not an immutable deployment")
    helper_files, helper_archive = archive_files(helper_source)
    if helper_source != source:
        require(run in (PROJECT / "runs" / REUSABLE_RUN, PROJECT / "runs" / APPLICATION_RETRY_RUN) and
                (source, archive, binary) == (REUSABLE_SOURCE, REUSABLE_ARCHIVE, REUSABLE_BINARY),
                "reuse is authorized only for the recorded successful bootstrap")
        original_files, original_archive = archive_files(source)
        require(original_archive == archive, "original archive changed")
        verify_delta(original_files, helper_files)
        if run.name == APPLICATION_RETRY_RUN:
            from dispatch_serial_retry import verify_references
            verify_references(run, helper_source)
    else:
        require(helper_archive == archive, "validator archive mismatch")
    return dict(validator_source_commit=helper_source,
                validator_archive_sha256=helper_archive,
                numerical_source_commit=source, numerical_binary_sha256=binary)


if __name__ == "__main__":
    for key, value in verify(Path(sys.argv[1]), Path(sys.argv[2])).items():
        print(f"{key}={value}")
