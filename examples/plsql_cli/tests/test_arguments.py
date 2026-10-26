import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from access_doc_cli import cli as access
from plsql_doc_cli import cli as plsql
from plsql_doc_cli.validation import ConfigError, load_arguments, load_config


@pytest.fixture(params=[access, plsql], ids=["access", "plsql"])
def case(request, tmp_path):
    cli = request.param
    root = tmp_path / "input"
    root.mkdir()
    lookup = root / "lookup.csv"
    lookup.touch()
    values = {
        "source_root": str(root),
        "document_root": str(tmp_path / "output"),
        "log_file": str(tmp_path / "logs" / "run.log"),
        "sys_prompt_directory": str(root),
        "system_id": "rms",
        "env_id": "prod",
    }
    if cli is access:
        values.update(
            lookup_ref_csv_path=str(lookup),
            table_file_folder_path=str(root),
            ui_user_prompt_directory=str(root),
            vba_user_prompt_directory=str(root),
        )
    else:
        values.update(
            simple_user_prompt_directory=str(root),
            complex_user_prompt_directory=str(root),
            special_user_prompts=str(root),
            exclude=["normal_*", "NORMAL_*"],
            include_special=["special_*", "SPECIAL_*"],
        )
    return cli, values


def argv(values):
    result = ["run"]
    for name, value in values.items():
        result.append("--" + name.replace("_", "-"))
        result.extend(value if isinstance(value, list) else [value])
    return result


def write_config(path, cli, values):
    import json
    from dataclasses import fields

    sections = {}
    for item in fields(cli.RunOptions):
        if item.name in values:
            sections.setdefault(item.metadata["section"], []).append(
                f"{item.name} = {json.dumps(values[item.name])}"
            )
    path.write_text("\n".join(
        f"[{section}]\n" + "\n".join(lines) for section, lines in sections.items()
    ))
    return path


def test_cli_and_toml_produce_equivalent_options(case, tmp_path, monkeypatch):
    cli, values = case
    config = write_config(tmp_path / "project.toml", cli, values)
    expected = cli.load_options(config)
    received = []
    monkeypatch.setattr(cli, "run", lambda options: received.append(options) or 0)
    assert cli.main(argv(values)) == 0
    assert received == [expected]
    if cli is plsql:
        assert received[0].exclude == ("normal_*", "NORMAL_*")
        assert received[0].include_special == ("special_*", "SPECIAL_*")
        assert received[0].detail is plsql.Detail.SUMMARY
    assert not expected.document_root.exists()
    assert not expected.log_file.parent.exists()


def test_no_implicit_default_config(case, tmp_path, monkeypatch, capsys):
    cli, values = case
    name = "access-docs.toml" if cli is access else "plsql-docs.toml"
    write_config(tmp_path / name, cli, values)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        cli.main(["run"])
    assert error.value.code == 2
    assert "missing required field" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["source_root", "system_id"])
def test_config_rejects_business_arguments_before_reading_file(case, tmp_path, flag, capsys):
    cli, values = case
    with pytest.raises(SystemExit) as error:
        cli.main(["run", "--config", str(tmp_path / "missing.toml"), *argv({flag: values[flag]})[1:]])
    assert error.value.code == 2
    assert "--config cannot be combined" in capsys.readouterr().err
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("source", ["cli", "toml"])
def test_every_business_path_requires_absolute_input(case, tmp_path, source):
    cli, values = case
    for name, value in values.items():
        if not isinstance(value, str) or not Path(value).is_absolute():
            continue
        invalid = {**values, name: "relative/path"}
        with pytest.raises(ConfigError, match=f"{name} must be an absolute path"):
            if source == "cli":
                load_arguments(cli.build_parser().parse_args(argv(invalid)), cli.RunOptions)
            else:
                cli.load_options(write_config(tmp_path / "relative.toml", cli, invalid))


def test_cli_missing_resource_and_empty_string_are_input_errors(case, tmp_path, capsys):
    cli, values = case
    for name, value, message in [
        ("source_root", str(tmp_path / "missing"), "source_root must exist"),
        ("system_id", " ", "system_id must be a non-empty string"),
    ]:
        with pytest.raises(SystemExit) as error:
            cli.main(argv({**values, name: value}))
        assert error.value.code == 2
        assert message in capsys.readouterr().err
    assert not (tmp_path / "logs").exists()


def test_variable_expansion_precedes_absolute_path_validation(case, tmp_path):
    cli, values = case
    config = write_config(tmp_path / "variables.toml", cli, {
        **values, "source_root": "${root}",
    })
    original = config.read_text()
    config.write_text('[variables]\nroot = "relative"\n' + original)
    with pytest.raises(ConfigError, match="source_root must be an absolute path"):
        cli.load_options(config)
    config.write_text(f"[variables]\nroot = '{values['source_root']}'\n" + original)
    assert cli.load_options(config).source_root == Path(values["source_root"])


def test_cli_does_not_expand_path_templates(case):
    cli, values = case
    args = cli.build_parser().parse_args(argv({**values, "source_root": "${root}"}))
    with pytest.raises(ConfigError, match="absolute path"):
        load_arguments(args, cli.RunOptions)


def test_new_field_automatically_registers_and_validates(case, monkeypatch):
    cli, values = case

    @dataclass
    class ExtendedOptions(cli.RunOptions):
        owner: str = field(metadata={"section": "project"})

    monkeypatch.setattr(cli, "RunOptions", ExtendedOptions)
    parser = cli.build_parser()
    args = parser.parse_args(argv(values))
    with pytest.raises(ConfigError, match="missing required field: project.owner"):
        load_arguments(args, ExtendedOptions)
    args = parser.parse_args(argv({**values, "owner": "payments"}))
    assert load_arguments(args, ExtendedOptions).owner == "payments"
    args = parser.parse_args(argv({**values, "owner": ""}))
    with pytest.raises(ConfigError, match="project.owner must be a non-empty string"):
        load_arguments(args, ExtendedOptions)


def test_cli_defaults_follow_dataclass_metadata(tmp_path):
    from argparse import ArgumentParser

    from plsql_doc_cli.validation import add_arguments

    @dataclass
    class Settings:
        source: Path = field(metadata={"section": "paths", "absolute": True})
        labels: tuple[str, ...] = field(default_factory=tuple, metadata={"section": "export"})
        owner: str = field(default="team", metadata={"section": "export"})

    parser = ArgumentParser()
    parser.add_argument("--config", type=Path)
    add_arguments(parser, Settings)
    result = load_arguments(parser.parse_args(["--source", str(tmp_path)]), Settings)
    assert result == Settings(tmp_path, (), "team")


def test_absolute_path_metadata_applies_to_defaults(tmp_path):
    @dataclass
    class Settings:
        destination: Path = field(
            default=Path("out"), metadata={"section": "paths", "absolute": True},
        )

    config = tmp_path / "default.toml"
    config.write_text("")
    with pytest.raises(ConfigError, match="destination must be an absolute path"):
        load_config(config, Settings)


@pytest.mark.skipif(os.name == "nt", reason="Windows paths are native on Windows")
def test_cli_rejects_foreign_platform_paths(case):
    cli, values = case
    args = cli.build_parser().parse_args(argv({**values, "source_root": "C:/source"}))
    with pytest.raises(ConfigError, match="Windows path"):
        load_arguments(args, cli.RunOptions)


def test_plsql_list_presence_and_nonempty_constraints(config_path, monkeypatch, capsys):
    options = plsql.load_options(config_path)
    values = {
        item: str(getattr(options, item)) for item in (
            "source_root", "document_root", "log_file", "sys_prompt_directory",
            "simple_user_prompt_directory", "complex_user_prompt_directory",
            "special_user_prompts", "system_id", "env_id",
        )
    }
    received = []
    monkeypatch.setattr(plsql, "run", lambda result: received.append(result) or 0)
    assert plsql.main(argv({
        **values, "exclude": [], "include_special": ["special_*"], "detail": "full",
    })) == 0
    assert received[0].exclude == ()
    assert received[0].detail is plsql.Detail.FULL
    for patterns, message in [
        ({"include_special": ["a"]}, "missing required field: patterns.exclude"),
        ({"exclude": []}, "missing required field: patterns.include_special"),
        ({"exclude": [], "include_special": []}, "include_special must not be empty"),
        ({"exclude": [""], "include_special": ["a"]}, "exclude must be a non-empty string"),
    ]:
        with pytest.raises(SystemExit) as error:
            plsql.main(argv({**values, **patterns}))
        assert error.value.code == 2
        assert message in capsys.readouterr().err
    with pytest.raises(SystemExit) as error:
        plsql.main(["run", "--config", str(config_path), "--exclude"])
    assert error.value.code == 2
    assert "--config cannot be combined" in capsys.readouterr().err
