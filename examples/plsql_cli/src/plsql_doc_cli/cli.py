"""Run both PL/SQL documentation stages using a project TOML file."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from plsql_doc_cli.validation import ConfigError, load_config


class Detail(str, Enum):
    SUMMARY = "summary"
    FULL = "full"


@dataclass(frozen=True)
class RunOptions:
    source_root: Path = field(metadata={"section": "paths", "must_exist": True})
    document_root: Path = field(metadata={"section": "paths"})
    sys_prompt_directory: Path = field(metadata={"section": "prompts", "must_exist": True})
    simple_user_prompt_directory: Path = field(metadata={"section": "prompts", "must_exist": True})
    complex_user_prompt_directory: Path = field(metadata={"section": "prompts", "must_exist": True})
    special_user_prompts: Path = field(metadata={"section": "prompts", "must_exist": True})
    log_file: Path = field(metadata={"section": "paths"})
    system_id: str = field(metadata={"section": "project"})
    env_id: str = field(metadata={"section": "project"})
    exclude: tuple[str, ...] = field(metadata={"section": "patterns"})
    include_special: tuple[str, ...] = field(metadata={"section": "patterns", "nonempty": True})
    detail: Detail = field(default=Detail.SUMMARY, metadata={"cli": True}, kw_only=True)

    @property
    def workbench_root(self) -> Path:
        return self.document_root / ".workbench"


def load_options(config_path: Path) -> RunOptions:
    return load_config(config_path, RunOptions)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plsql-docs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Generate documentation and publish on success.")
    run_parser.add_argument(
        "--config", type=Path, default=Path("plsql-docs.toml"),
        help="Project TOML (default: plsql-docs.toml in the current directory).",
    )
    run_parser.add_argument(
        "--detail", choices=[item.value for item in Detail], default=Detail.SUMMARY.value,
        help="Completion output detail; does not change generation (default: summary).",
    )
    return parser


def run(options: RunOptions) -> int:
    # These imports belong to the host application, not this workspace.
    from infra import setup_logging
    from infra.observability.phoenix_tracing import (
        init_phoenix_tracing,
        shutdown_phoenix_tracing,
    )
    from processor import PLSQLConfig, PLSQLService
    from processor.core.workbench import Workbench

    options.log_file.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(str(options.log_file))
    config = PLSQLConfig(
        source_root=options.source_root, document_root=options.document_root,
        system_id=options.system_id, env_id=options.env_id,
    )
    workbench = Workbench.open(
        options.workbench_root, protected_roots=(options.source_root,),
    )
    service = PLSQLService(config=config)
    init_phoenix_tracing()
    try:
        normal_error = service.generate(
            workbench=workbench,
            sys_prompt_directory=options.sys_prompt_directory,
            simple_user_prompt_directory=options.simple_user_prompt_directory,
            complex_user_prompt_directory=options.complex_user_prompt_directory,
            exclude_pattern=list(options.exclude),
        )
        if type(normal_error) is not bool:
            raise TypeError("PLSQLService.generate must return a bool error flag")
        special_error = service.generate_special_package(
            workbench=workbench,
            sys_prompt_directory=options.sys_prompt_directory,
            user_prompt_directory=options.special_user_prompts,
            include_pattern=list(options.include_special),
        )
        if type(special_error) is not bool:
            raise TypeError("PLSQLService.generate_special_package must return a bool error flag")
        failed = [
            name for name, error in (("normal", normal_error), ("special", special_error))
            if error
        ]
        if failed:
            print(
                f"Generation failed ({', '.join(failed)}); not published. "
                f"Log: {options.log_file}. Checkpoints retained: {options.workbench_root}",
                file=sys.stderr,
            )
            return 1
        workbench.publish(options.document_root)
        if options.detail is Detail.FULL:
            print(
                f"Published: {options.document_root}\n"
                f"Source: {options.source_root}\nLog: {options.log_file}"
            )
        return 0
    finally:
        shutdown_phoenix_tracing()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        options = replace(load_options(args.config), detail=Detail(args.detail))
    except ConfigError as error:
        parser.error(str(error))
    return run(options)


if __name__ == "__main__":
    sys.exit(main())
