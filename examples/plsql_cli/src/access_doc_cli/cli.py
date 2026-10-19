"""Analyze Access UI and entries, then publish documentation on success."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from plsql_doc_cli.validation import ConfigError, load_config


@dataclass
class RunOptions:
    source_root: Path = field(metadata={"section": "paths", "must_exist": True})
    document_root: Path = field(metadata={"section": "paths"})
    log_file: Path = field(metadata={"section": "paths"})
    lookup_ref_csv_path: Path = field(metadata={"section": "paths", "must_exist": True})
    table_file_folder_path: Path = field(metadata={"section": "paths", "must_exist": True})
    sys_prompt_directory: Path = field(metadata={"section": "prompts", "must_exist": True})
    ui_user_prompt_directory: Path = field(metadata={"section": "prompts", "must_exist": True})
    vba_user_prompt_directory: Path = field(metadata={"section": "prompts", "must_exist": True})
    system_id: str = field(metadata={"section": "project"})
    env_id: str = field(metadata={"section": "project"})

    @property
    def workbench_root(self) -> Path:
        return self.document_root / ".workbench"


def load_options(config_path: Path) -> RunOptions:
    return load_config(config_path, RunOptions)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="access-docs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Analyze UI and entries, then publish on success.")
    run_parser.add_argument(
        "--config", type=Path, default=Path("access-docs.toml"),
        help="Project TOML (default: access-docs.toml in the current directory).",
    )
    return parser


def run(options: RunOptions) -> int:
    from infra import setup_logging
    from infra.observability.phoenix_tracing import (
        init_phoenix_tracing,
        shutdown_phoenix_tracing,
    )
    from processor import AccessConfig, AccessService
    from processor.core.workbench import Workbench

    options.log_file.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(str(options.log_file))
    logger = logging.getLogger(__name__)
    config = AccessConfig(
        source_root=options.source_root, document_root=options.document_root,
        system_id=options.system_id, env_id=options.env_id,
    )
    workbench = Workbench.open(
        options.workbench_root, protected_roots=(options.source_root,),
    )
    service = AccessService(config)
    init_phoenix_tracing()
    try:
        form_lookup_mapping, ui_error = service.analyze_form_ui(
            workbench=workbench,
            lookup_ref_csv_path=options.lookup_ref_csv_path,
            table_file_folder_path=options.table_file_folder_path,
            sys_prompt_directory=options.sys_prompt_directory,
            user_prompt_directory=options.ui_user_prompt_directory,
        )
        if type(ui_error) is not bool:
            raise TypeError("AccessService.analyze_form_ui must return a bool error flag")
        entries_error = service.analyze_entrys(
            workbench=workbench,
            form_lookup_mapping=form_lookup_mapping,
            sys_prompt_directory=options.sys_prompt_directory,
            user_prompt_directory=options.vba_user_prompt_directory,
        )
        if type(entries_error) is not bool:
            raise TypeError("AccessService.analyze_entrys must return a bool error flag")
        failed = [
            name for name, error in (("form_ui", ui_error), ("entries", entries_error))
            if error
        ]
        if failed:
            print(
                f"Analysis failed ({', '.join(failed)}); not published. "
                f"Log: {options.log_file}. Checkpoints retained: {options.workbench_root}",
                file=sys.stderr,
            )
            return 1
        workbench.publish(options.document_root)
        logger.info("Access analysis completed and published")
        return 0
    finally:
        shutdown_phoenix_tracing()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        options = load_options(args.config)
    except ConfigError as error:
        parser.error(str(error))
    return run(options)


if __name__ == "__main__":
    sys.exit(main())
