#!/usr/bin/env python3
"""Verify the standalone Apache-2.0 investigation-core distributions.

The source boundary check prevents product imports before packaging. This verifier
also compares the built wheel and sdist with the current source tree byte-for-byte,
so a previously built (and therefore stale) artifact cannot satisfy the release
gate after source or licensing changes.
"""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
import importlib
import io
import re
import stat
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Mapping
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipInfo

PACKAGE_NAME = "instadescribe-investigation-core"
IMPORT_NAME = "instadescribe_investigation_core"
PROHIBITED_IMPORT_ROOTS = frozenset(
    {
        "app",
        "instadescribe_contracts",
        "instadescribe_worker",
        "modular_pipeline",
        "services",
    }
)
SOURCE_ROOT_FILES = ("LICENSE", "README.md", "pyproject.toml")
SDIST_LOCK_FILE = "uv.lock"
_SIMPLE_REQUIREMENT = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?P<constraint>(?:[<>=!~].*)?)"
)


def _safe_archive_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path == PurePosixPath(".") or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive path: {name}")
    if name != path.as_posix():
        raise ValueError(f"archive path must use canonical POSIX spelling: {name!r}")
    return path


def _single_artifact(dist: Path, pattern: str, label: str) -> Path:
    matches = sorted(dist.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {label} in {dist}, found {len(matches)}")
    artifact = matches[0]
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError(f"{label} must be a regular file: {artifact}")
    return artifact


def _assert_open_python(source: bytes, member: str) -> None:
    tree = ast.parse(source, filename=member)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.partition(".")[0]
                if root in PROHIBITED_IMPORT_ROOTS:
                    raise ValueError(f"{member} imports BUSL product module {root!r}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            root = node.module.partition(".")[0]
            if root in PROHIBITED_IMPORT_ROOTS:
                raise ValueError(f"{member} imports BUSL product module {root!r}")


def _read_source_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source {label} must be a regular file: {path}")
    return path.read_bytes()


def _read_source(
    source: Path,
) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes]]:
    package = source / "src" / IMPORT_NAME
    if not package.is_dir():
        raise ValueError(f"source package directory does not exist: {package}")

    package_files: dict[str, bytes] = {}
    python_paths = sorted(package.rglob("*.py"))
    if not python_paths or package / "__init__.py" not in python_paths:
        raise ValueError(f"source package is missing Python package files: {package}")
    for path in python_paths:
        relative = path.relative_to(package).as_posix()
        data = _read_source_file(path, f"package file {relative}")
        _assert_open_python(data, f"source:{relative}")
        package_files[relative] = data

    marker = package / "py.typed"
    package_files["py.typed"] = _read_source_file(marker, "py.typed marker")
    root_files = {name: _read_source_file(source / name, name) for name in SOURCE_ROOT_FILES}

    sdist_source = dict(root_files)
    sdist_source[".gitignore"] = _read_source_file(source / ".gitignore", "package .gitignore")
    sdist_source[SDIST_LOCK_FILE] = _read_source_file(source / SDIST_LOCK_FILE, SDIST_LOCK_FILE)
    for name, data in package_files.items():
        sdist_source[f"src/{IMPORT_NAME}/{name}"] = data

    tests = source / "tests"
    test_paths = sorted(tests.rglob("*.py")) if tests.is_dir() else []
    if not test_paths or tests / "conftest.py" not in test_paths:
        raise ValueError(f"source package is missing its shipped test suite: {tests}")
    for path in test_paths:
        relative = path.relative_to(tests).as_posix()
        data = _read_source_file(path, f"test file {relative}")
        _assert_open_python(data, f"source:tests/{relative}")
        sdist_source[f"tests/{relative}"] = data

    return package_files, root_files, sdist_source


def _assert_exact_files(
    actual: Mapping[str, bytes], expected: Mapping[str, bytes], *, label: str
) -> None:
    actual_names = set(actual)
    expected_names = set(expected)
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    changed = sorted(
        name for name in actual_names & expected_names if actual[name] != expected[name]
    )
    if missing or extra or changed:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        if changed:
            details.append(f"changed={changed}")
        raise ValueError(f"{label} does not exactly match current source: {'; '.join(details)}")


def _metadata_requirement(requirement: object, *, extra: str) -> str:
    if not isinstance(requirement, str):
        raise ValueError(f"optional dependency in {extra!r} must be a string")
    match = _SIMPLE_REQUIREMENT.fullmatch(requirement)
    if match is None:
        raise ValueError(f"optional dependency {requirement!r} uses unsupported requirement syntax")
    normalized_name = re.sub(r"[-_.]+", "-", match.group("name")).lower()
    return f"{normalized_name}{match.group('constraint')}; extra == '{extra}'"


def _verify_metadata(raw: bytes, *, label: str, root_source: Mapping[str, bytes]) -> None:
    metadata = BytesParser().parsebytes(raw)
    try:
        project = tomllib.loads(root_source["pyproject.toml"].decode("utf-8"))["project"]
        source_fields = {
            "Name": project["name"],
            "Version": project["version"],
            "License-Expression": project["license"],
            "Requires-Python": project["requires-python"],
        }
    except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("source pyproject.toml has invalid project metadata") from error
    if source_fields["Name"] != PACKAGE_NAME:
        raise ValueError(
            f"source project name must be {PACKAGE_NAME!r}, got {source_fields['Name']!r}"
        )
    if project.get("dependencies", []) != []:
        raise ValueError("source project must have zero required runtime dependencies")

    optional_dependencies = project.get("optional-dependencies", {})
    if not isinstance(optional_dependencies, dict):
        raise ValueError("source project optional-dependencies must be a table")
    expected_extras: list[str] = []
    expected_requirements: list[str] = []
    for raw_extra, requirements in optional_dependencies.items():
        if not isinstance(raw_extra, str) or not isinstance(requirements, list):
            raise ValueError("source project optional-dependencies are malformed")
        extra = re.sub(r"[-_.]+", "-", raw_extra).lower()
        if not extra or extra != raw_extra:
            raise ValueError(f"optional dependency extra must be canonical: {raw_extra!r}")
        expected_extras.append(extra)
        expected_requirements.extend(
            _metadata_requirement(requirement, extra=extra) for requirement in requirements
        )

    expected_fields = {
        **source_fields,
        "License-Expression": "Apache-2.0",
        "Requires-Python": ">=3.12",
    }
    for field, expected in expected_fields.items():
        actual = metadata.get(field)
        if actual != expected:
            raise ValueError(f"{label} metadata {field} must be {expected!r}, got {actual!r}")

    actual_extras = metadata.get_all("Provides-Extra", [])
    if sorted(actual_extras) != sorted(expected_extras):
        raise ValueError(f"{label} optional dependency extras do not match current source")
    actual_requirements = metadata.get_all("Requires-Dist", [])
    if sorted(actual_requirements) != sorted(expected_requirements):
        raise ValueError(f"{label} optional requirements do not match current source")

    payload = metadata.get_payload()
    if not isinstance(payload, str) or payload.encode("utf-8") != root_source["README.md"]:
        raise ValueError(f"{label} embedded README does not exactly match current source")


def _assert_apache_license(data: bytes, *, label: str, source_license: bytes) -> None:
    if data != source_license:
        raise ValueError(f"{label} license does not exactly match current source LICENSE")
    license_text = data.decode("utf-8")
    for marker in (
        "Apache License",
        "Version 2.0, January 2004",
        "Copyright 2026 Andrii Artemenko",
    ):
        if marker not in license_text:
            raise ValueError(f"{label} license is missing {marker!r}")


def _zip_member_is_link(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o177777
    return stat.S_ISLNK(mode)


def _purge_import_modules() -> None:
    for name in tuple(sys.modules):
        if name == IMPORT_NAME or name.startswith(f"{IMPORT_NAME}."):
            del sys.modules[name]


def _verify_wheel_record(
    archive: zipfile.ZipFile,
    *,
    record_name: str,
    expected_names: set[str],
) -> None:
    try:
        rows = tuple(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("wheel RECORD must be valid UTF-8 CSV") from error
    if any(len(row) != 3 for row in rows):
        raise ValueError("wheel RECORD rows must contain path, digest and size")
    recorded_names = [row[0] for row in rows]
    if len(recorded_names) != len(set(recorded_names)):
        raise ValueError("wheel RECORD contains duplicate paths")
    if set(recorded_names) != expected_names:
        raise ValueError("wheel RECORD does not enumerate the exact wheel member set")

    for name, encoded_digest, encoded_size in rows:
        if name == record_name:
            if encoded_digest or encoded_size:
                raise ValueError("wheel RECORD must not hash itself")
            continue
        data = archive.read(name)
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if encoded_digest != f"sha256={digest}":
            raise ValueError(f"wheel RECORD digest mismatch for {name}")
        if encoded_size != str(len(data)):
            raise ValueError(f"wheel RECORD size mismatch for {name}")


def _verify_wheel(
    wheel: Path, package_source: Mapping[str, bytes], root_source: Mapping[str, bytes]
) -> None:
    with zipfile.ZipFile(wheel) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("wheel contains duplicate archive paths")

        paths: dict[str, PurePosixPath] = {}
        for info in infos:
            path = _safe_archive_name(info.filename)
            paths[info.filename] = path
            if _zip_member_is_link(info):
                raise ValueError(f"wheel must not contain links: {info.filename}")
            if path.parts and path.parts[0].casefold() in PROHIBITED_IMPORT_ROOTS:
                raise ValueError(f"wheel contains BUSL product path: {info.filename}")

        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("wheel must contain exactly one dist-info/METADATA")
        dist_info = PurePosixPath(metadata_names[0]).parent
        if len(dist_info.parts) != 1 or not dist_info.name.endswith(".dist-info"):
            raise ValueError("wheel metadata must use one top-level dist-info directory")
        license_name = (dist_info / "licenses" / "LICENSE").as_posix()
        wheel_name = (dist_info / "WHEEL").as_posix()
        record_name = (dist_info / "RECORD").as_posix()
        expected_names = {
            *(f"{IMPORT_NAME}/{name}" for name in package_source),
            metadata_names[0],
            wheel_name,
            license_name,
            record_name,
        }
        actual_names = set(names)
        if actual_names != expected_names:
            extra = sorted(actual_names - expected_names)
            missing = sorted(expected_names - actual_names)
            raise ValueError(f"wheel member set is not exact: missing={missing}; extra={extra}")

        _verify_metadata(archive.read(metadata_names[0]), label="wheel", root_source=root_source)
        _assert_apache_license(
            archive.read(license_name),
            label="wheel",
            source_license=root_source["LICENSE"],
        )
        wheel_metadata = BytesParser().parsebytes(archive.read(wheel_name))
        if (
            wheel_metadata.get("Wheel-Version") != "1.0"
            or wheel_metadata.get("Root-Is-Purelib") != "true"
            or wheel_metadata.get_all("Tag", []) != ["py3-none-any"]
        ):
            raise ValueError("wheel compatibility metadata is not the expected pure-Python tag")
        _verify_wheel_record(
            archive,
            record_name=record_name,
            expected_names=expected_names,
        )

        packaged: dict[str, bytes] = {}
        prefix = (IMPORT_NAME,)
        for name, path in paths.items():
            if path.parts[:1] != prefix or len(path.parts) < 2:
                continue
            relative = PurePosixPath(*path.parts[1:])
            if relative.suffix == ".py" or relative.name == "py.typed":
                packaged[relative.as_posix()] = archive.read(name)
        _assert_exact_files(packaged, package_source, label="wheel package")
        for name, data in packaged.items():
            if name.endswith(".py"):
                _assert_open_python(data, f"wheel:{IMPORT_NAME}/{name}")

    _purge_import_modules()
    sys.path.insert(0, str(wheel))
    try:
        module = importlib.import_module(IMPORT_NAME)
        if str(wheel) not in str(module.__file__):
            raise ValueError(f"import smoke did not load from the built wheel: {module.__file__}")
        project = tomllib.loads(root_source["pyproject.toml"].decode("utf-8"))["project"]
        if getattr(module, "__version__", None) != project["version"]:
            raise ValueError("wheel package __version__ does not match pyproject.toml")
    finally:
        sys.path.remove(str(wheel))
        _purge_import_modules()


def _read_tar_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"sdist member is not a readable regular file: {member.name}")
    return stream.read()


def _allowed_sdist_directories(expected_files: set[str]) -> set[PurePosixPath]:
    allowed = {PurePosixPath(".")}
    for name in expected_files:
        parent = PurePosixPath(name).parent
        while parent != PurePosixPath("."):
            allowed.add(parent)
            parent = parent.parent
    return allowed


def _verify_sdist(
    sdist: Path, sdist_source: Mapping[str, bytes], root_source: Mapping[str, bytes]
) -> None:
    with tarfile.open(sdist, mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("sdist contains duplicate archive paths")

        by_name = {member.name: member for member in members}
        paths: dict[str, PurePosixPath] = {}
        for member in members:
            path = _safe_archive_name(member.name)
            paths[member.name] = path
            if member.issym() or member.islnk():
                raise ValueError(f"sdist must not contain links: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"sdist contains unsupported member type: {member.name}")

        roots = {path.parts[0] for path in paths.values() if path.parts}
        if len(roots) != 1:
            raise ValueError(f"sdist must contain exactly one top-level directory, found {roots}")
        root = roots.pop()

        actual_source: dict[str, bytes] = {}
        directory_paths: set[PurePosixPath] = set()
        metadata_bytes: bytes | None = None
        for name, path in paths.items():
            if path.parts[0] != root:
                raise ValueError(f"sdist member is outside the top-level directory: {name}")
            relative = PurePosixPath(*path.parts[1:])
            if any(part.casefold() in PROHIBITED_IMPORT_ROOTS for part in relative.parts):
                raise ValueError(f"sdist contains BUSL product path: {name}")
            member = by_name[name]
            if member.isdir():
                directory_paths.add(relative)
                continue
            data = _read_tar_member(archive, member)
            if relative == PurePosixPath("PKG-INFO"):
                metadata_bytes = data
                continue
            relative_name = relative.as_posix()
            actual_source[relative_name] = data
            if relative.suffix == ".py":
                _assert_open_python(data, f"sdist:{name}")

        _assert_exact_files(actual_source, sdist_source, label="sdist source manifest")
        allowed_directories = _allowed_sdist_directories(set(sdist_source) | {"PKG-INFO"})
        unexpected_directories = sorted(
            path.as_posix() for path in directory_paths if path not in allowed_directories
        )
        if unexpected_directories:
            raise ValueError(f"sdist contains unexpected directories: {unexpected_directories}")

        _assert_apache_license(
            actual_source["LICENSE"], label="sdist", source_license=root_source["LICENSE"]
        )
        if metadata_bytes is None:
            raise ValueError("sdist must contain exactly one top-level PKG-INFO")
        _verify_metadata(
            metadata_bytes,
            label="sdist",
            root_source=root_source,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="current packages/investigation-core source directory",
    )
    args = parser.parse_args()

    if not args.dist.is_dir():
        parser.error(f"distribution directory does not exist: {args.dist}")
    if not args.source.is_dir():
        parser.error(f"source directory does not exist: {args.source}")

    package_source, root_source, sdist_source = _read_source(args.source.resolve())
    wheel = _single_artifact(args.dist, "*.whl", "wheel")
    sdist = _single_artifact(args.dist, "*.tar.gz", "sdist")
    _verify_wheel(wheel, package_source, root_source)
    _verify_sdist(sdist, sdist_source, root_source)
    print("investigation-core-distribution-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
