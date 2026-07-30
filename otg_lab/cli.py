"""Command-line entry point for E-series experiments."""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from .experiment import ExperimentResult, ExperimentSpec, run_experiment
from .publishing import publish_all_results, publish_run


def _project_root(value: str | Path | None = None) -> Path:
    return Path(value or Path.cwd()).resolve()


def _experiment_directories(project_root: Path) -> tuple[Path, ...]:
    experiments = project_root / "experiments"
    if not experiments.is_dir():
        return ()
    return tuple(
        sorted(
            directory
            for directory in experiments.iterdir()
            if directory.is_dir()
            and not directory.name.startswith("_")
            and (directory / "experiment.py").is_file()
        )
    )


def resolve_experiment_directory(project_root: Path, query: str) -> Path:
    directories = _experiment_directories(project_root)
    exact = [directory for directory in directories if directory.name == query]
    if exact:
        return exact[0]
    normalized = str(query).strip().upper()
    prefix = normalized.split("_", 1)[0]
    if re.fullmatch(r"E[0-9]{2,}", prefix):
        matches = [
            directory
            for directory in directories
            if directory.name.upper().startswith(prefix + "_")
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"{query!r} is ambiguous: "
                + ", ".join(directory.name for directory in matches)
            )
    choices = ", ".join(directory.name for directory in directories) or "(none)"
    raise FileNotFoundError(
        f"experiment {query!r} was not found; available experiments: {choices}"
    )


def _load_module(path: Path) -> ModuleType:
    module_name = f"_otg_lab_experiment_{path.parent.name}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot import experiment module {path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def load_experiment_spec(project_root: Path, query: str) -> ExperimentSpec:
    directory = resolve_experiment_directory(project_root, query)
    module = _load_module(directory / "experiment.py")
    builder = getattr(module, "build_experiment", None)
    if callable(builder):
        signature = inspect.signature(builder)
        if len(signature.parameters) == 0:
            spec = builder()
        else:
            spec = builder(project_root)
    else:
        spec = getattr(module, "EXPERIMENT_SPEC", None)
    if not isinstance(spec, ExperimentSpec):
        raise TypeError(
            f"{directory / 'experiment.py'} must expose an ExperimentSpec via "
            "build_experiment(project_root) or EXPERIMENT_SPEC"
        )
    if directory.name != spec.directory_name:
        raise ValueError(
            f"experiment directory {directory.name!r} does not match resolved "
            f"spec name {spec.directory_name!r}"
        )
    return spec


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").lower()
    if not normalized or not normalized[0].isalpha():
        raise ValueError("experiment slug must begin with a letter")
    return normalized


def create_experiment(project_root: Path, experiment_id: str, slug: str) -> Path:
    identifier = str(experiment_id).strip().upper()
    if not re.fullmatch(r"E[0-9]{2,}", identifier):
        raise ValueError("experiment ID must look like E02")
    normalized_slug = _slug(slug)
    target = project_root / "experiments" / f"{identifier}_{normalized_slug}"
    if target.exists():
        raise FileExistsError(f"experiment directory already exists: {target}")
    template = project_root / "experiments" / "_template"
    experiment_template = template / "experiment.py"
    readme_template = template / "README.md"
    if not experiment_template.is_file() or not readme_template.is_file():
        raise FileNotFoundError("experiments/_template is incomplete")
    target.mkdir(parents=True)
    replacements = {
        "__EXPERIMENT_ID__": identifier,
        "__EXPERIMENT_SLUG__": normalized_slug,
        "__EXPERIMENT_TITLE__": f"{identifier} {normalized_slug.replace('_', ' ')}",
    }
    for source, destination_name in (
        (experiment_template, "experiment.py"),
        (readme_template, "README.md"),
    ):
        content = source.read_text(encoding="utf-8")
        for old, new in replacements.items():
            content = content.replace(old, new)
        (target / destination_name).write_text(content, encoding="utf-8")
    (target / "results.md").write_text(f"# {target.name}\n\n", encoding="utf-8")
    results_directory = target / "results"
    results_directory.mkdir()
    (results_directory / "index.csv").write_text(
        "experiment_id,run_id,spec_hash,git_commit,release_tag,release_url,"
        "release_state,result_directory,result_archive_sha256,published_at\n",
        encoding="utf-8",
    )
    return target


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="otg-lab",
        description="CSV-first single-axis OTG experiment runner",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="repository root (default: current directory)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="run one E-series experiment")
    run_parser.add_argument("experiment")
    run_parser.add_argument(
        "--runs-root",
        default=None,
        help=(
            "directory containing run instances "
            "(default: experiments/<experiment>/runs)"
        ),
    )
    run_parser.add_argument(
        "--no-figures",
        action="store_true",
        help="create the figures directory without rendering PNGs",
    )

    new_parser = commands.add_parser(
        "new-experiment", help="create a declared experiment from the template"
    )
    new_parser.add_argument("experiment_id")
    new_parser.add_argument("slug")

    publish_parser = commands.add_parser(
        "publish-run",
        help="package one manually selected result and publish it",
    )
    publish_parser.add_argument(
        "result_directory",
        help="experiments/<experiment>/results/<run-id>",
    )
    publish_parser.add_argument(
        "--repo",
        default=None,
        help="GitHub repository as [HOST/]OWNER/REPO (default: current repo)",
    )
    publish_parser.add_argument(
        "--tag",
        default=None,
        help="release tag (default: derived from experiment, run, and spec)",
    )
    publish_parser.add_argument(
        "--title",
        default=None,
        help="release title",
    )
    publish_parser.add_argument(
        "--draft",
        action="store_true",
        help="create a draft GitHub Release",
    )
    publish_parser.add_argument(
        "--package-only",
        action="store_true",
        help="package the result without creating a Release",
    )
    publish_parser.add_argument(
        "--output-dir",
        default=None,
        help=("retain release assets in this directory; required with --package-only"),
    )

    publish_all_parser = commands.add_parser(
        "publish-results",
        help="publish every unpublished experiments/E*/results/<run-id>",
    )
    publish_all_parser.add_argument(
        "--repo",
        default=None,
        help="GitHub repository as [HOST/]OWNER/REPO (default: current repo)",
    )
    publish_all_parser.add_argument(
        "--draft",
        action="store_true",
        help="create draft GitHub Releases",
    )

    commands.add_parser("list", help="list available experiments")
    return parser


def _run_command(args: argparse.Namespace, project_root: Path) -> int:
    spec = load_experiment_spec(project_root, args.experiment)
    result: ExperimentResult = run_experiment(
        spec,
        project_root=project_root,
        runs_root=args.runs_root,
        create_figures=not args.no_figures,
    )
    state = "completed" if result.success else "failed"
    print(f"{result.experiment_id} {state}: {result.run_directory}")
    if result.failure_count:
        print(
            f"failures={result.failure_count}, "
            f"required_failures={result.required_failure_count}"
        )
    return 0 if result.success else 1


def _publish_run_command(
    args: argparse.Namespace,
    project_root: Path,
) -> int:
    result = publish_run(
        project_root,
        args.result_directory,
        repository=args.repo,
        release_tag=args.tag,
        release_title=args.title,
        draft=args.draft,
        package_only=args.package_only,
        output_directory=args.output_dir,
    )
    print(f"selected {result.plan.experiment_id}: {result.plan.result_directory}")
    print(
        f"result archive={result.assets.result_archive} "
        f"sha256={result.assets.result_archive_sha256}"
    )
    if result.release is not None:
        print(f"release {result.release.state}: {result.release.url}")
        run_id = result.release.runbuoy_run_id
        print(f"RunBuoy upload run: {run_id}")
        print(f"  runbuoy status {run_id}")
        print(f"  runbuoy logs {run_id}")
        print(f"  runbuoy attach {run_id}")
    print(
        "results index updated: "
        f"{result.plan.experiment_directory / 'results/index.csv'}"
    )
    return 0


def _publish_results_command(
    args: argparse.Namespace,
    project_root: Path,
) -> int:
    result = publish_all_results(
        project_root,
        repository=args.repo,
        draft=args.draft,
    )
    run_id = result.runbuoy_run_id
    print(
        f"RunBuoy batch: {run_id} "
        f"results={result.result_count} exit_code={result.exit_code}"
    )
    print(f"  runbuoy status {run_id}")
    print(f"  runbuoy logs {run_id}")
    print(f"  runbuoy attach {run_id}")
    return 0 if result.exit_code == 0 else 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    project_root = _project_root(args.project_root)
    try:
        if args.command == "run":
            return _run_command(args, project_root)
        if args.command == "new-experiment":
            path = create_experiment(project_root, args.experiment_id, args.slug)
            print(path)
            return 0
        if args.command == "publish-run":
            return _publish_run_command(args, project_root)
        if args.command == "publish-results":
            return _publish_results_command(args, project_root)
        if args.command == "list":
            for directory in _experiment_directories(project_root):
                print(directory.name)
            return 0
    except Exception as error:
        print(f"otg-lab: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    parser.error(f"unknown command {args.command!r}")
    return 2


__all__ = [
    "create_experiment",
    "load_experiment_spec",
    "main",
    "resolve_experiment_directory",
]


if __name__ == "__main__":
    raise SystemExit(main())
