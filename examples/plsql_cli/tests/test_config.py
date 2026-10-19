import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from plsql_doc_cli.cli import build_parser, load_options, main
from plsql_doc_cli.validation import ConfigError
import plsql_doc_cli.cli as cli
import plsql_doc_cli.validation as validation


def load(path: Path):
    return load_options(path)


def test_config_paths_are_relative_to_file_not_cwd(config_path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    options = load(config_path)
    assert options.source_root == config_path.parent / "source"
    assert options.document_root == config_path.parent / "docs"
    assert options.sys_prompt_directory == config_path.parent / "prompts"
    assert options.simple_user_prompt_directory == config_path.parent / "prompts/simple_user_prompts"
    assert options.complex_user_prompt_directory == config_path.parent / "prompts/complex_user_prompts"
    assert options.special_user_prompts == config_path.parent / "prompts/special_user_prompts"
    assert options.log_file == config_path.parent / "logs/plsql.log"
    assert (options.system_id, options.env_id) == ("rms", "prod")
    assert options.exclude == ("normal_*",)
    assert options.include_special == ("special_*",)
    assert not options.document_root.exists()
    assert not options.log_file.parent.exists()


def test_run_options_can_be_updated_after_loading(config_path):
    options = load(config_path)
    options.detail = cli.Detail.FULL
    assert options.detail is cli.Detail.FULL
    assert load(config_path).detail is cli.Detail.SUMMARY


def test_config_defaults_and_default_filename(config_path, monkeypatch):
    monkeypatch.chdir(config_path.parent)
    options = load_options(build_parser().parse_args(["run"]).config)
    assert options.exclude == ("normal_*",)
    assert options.include_special == ("special_*",)
    text = config_path.read_text().split("[patterns]")[0]
    config_path.write_text(text)
    with pytest.raises(ConfigError, match="patterns.exclude"):
        load(config_path)


def test_empty_exclusions_in_config(config_path):
    config_path.write_text(config_path.read_text().replace('exclude = ["normal_*"]', "exclude = []"))
    options = load(config_path)
    assert options.exclude == ()


@pytest.mark.parametrize(("old", "new", "message"), [
    ("system_id = \"rms\"", 'system_id = ""', "non-empty"),
    ("env_id = \"prod\"", "env_id = 12", "non-empty"),
    ("system_id", "system_ids", "unknown config field"),
    ("[project]", "[projects]", "unknown config section"),
    ('source_root = "source"\n', "", "paths.source_root"),
    ('source_root = "source"', 'source_root = "missing"', "must exist"),
    ('exclude = ["normal_*"]', 'exclude = "normal_*"', "list of strings"),
    ('include_special = ["special_*"]', "include_special = []", "must not be empty"),
    ('include_special = ["special_*"]', 'include_special = [""]', "non-empty"),
])
def test_invalid_configuration(config_path, old, new, message):
    config_path.write_text(config_path.read_text().replace(old, new))
    with pytest.raises(ConfigError, match=message):
        load(config_path)


def test_unknown_table_type_and_invalid_toml(config_path):
    config_path.write_text('project = "rms"\n')
    with pytest.raises(ConfigError, match="must be a table"):
        load(config_path)
    config_path.write_text("[project")
    with pytest.raises(ConfigError, match="cannot load configuration"):
        load(config_path)


def test_configured_prompt_paths_must_exist(config_path):
    (config_path.parent / "prompts/special_user_prompts").rmdir()
    with pytest.raises(ConfigError, match="prompts.special_user_prompts must exist"):
        load(config_path)


@pytest.mark.skipif(os.name == "nt", reason="Windows paths are native on Windows")
def test_foreign_windows_path(config_path):
    config_path.write_text(config_path.read_text().replace('source_root = "source"', 'source_root = "C:/workspace/src"'))
    with pytest.raises(ConfigError, match="Windows path"):
        load(config_path)


def test_existing_symlink_directory_is_accepted(config_path):
    (config_path.parent / "docs").symlink_to(config_path.parent / "source", target_is_directory=True)
    assert load(config_path).document_root == config_path.parent / "source"


@pytest.mark.parametrize("case", ["missing", "invalid"])
def test_input_error_exit_code_and_no_imports(config_path, case, capsys):
    if case == "missing":
        config_path.unlink()
    elif case == "invalid":
        config_path.write_text("[broken")
    args = ["run", "--config", str(config_path)]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert not (config_path.parent / "logs").exists()


@pytest.mark.parametrize("flag", [
    "--mode", "--exclude", "--include", "--source-root",
    "--document-root", "--prompt-root", "--log-file",
])
def test_removed_overrides_are_rejected(config_path, flag, capsys):
    with pytest.raises(SystemExit) as error:
        main(["run", "--config", str(config_path), flag, "value"])
    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_new_field_declaration_controls_loading_and_validation(config_path, monkeypatch):
    @dataclass
    class ExtendedOptions(cli.RunOptions):
        owner: str = field(metadata={"section": "project"})

    monkeypatch.setattr(cli, "RunOptions", ExtendedOptions)
    with pytest.raises(ConfigError, match="missing required field: project.owner"):
        load(config_path)
    original = config_path.read_text()
    config_path.write_text(original.replace("[project]", '[project]\nowner = "payments"'))
    assert load(config_path).owner == "payments"
    config_path.write_text(original.replace("[project]", "[project]\nowner = 123"))
    with pytest.raises(ConfigError, match="project.owner must be a non-empty string"):
        load(config_path)


def test_generic_loader_accepts_unrelated_caller_model(tmp_path):
    @dataclass(frozen=True)
    class ExportSettings:
        title: str = field(metadata={"section": "export"})
        labels: tuple[str, ...] = field(metadata={"section": "export"})
        destination: Path = field(metadata={"section": "storage"})

    path = tmp_path / "export.toml"
    path.write_text(
        '[export]\ntitle = "Report"\nlabels = ["daily", "DAILY"]\n'
        '[storage]\ndestination = "out"\n',
    )
    result = validation.load_config(path, ExportSettings)
    assert result == ExportSettings("Report", ("daily", "DAILY"), tmp_path / "out")
    assert not result.destination.exists()
    path.write_text(path.read_text().replace('labels = ["daily", "DAILY"]', "labels = 42"))
    with pytest.raises(ConfigError, match="export.labels must be a list of strings"):
        validation.load_config(path, ExportSettings)


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_path_existence_is_declared_per_field(tmp_path, kind):
    @dataclass(frozen=True)
    class Settings:
        required: Path = field(metadata={"section": "paths", "must_exist": True})
        optional: Path = field(metadata={"section": "paths"})

    config = tmp_path / "settings.toml"
    config.write_text('[paths]\nrequired = "required"\noptional = "optional"\n')
    with pytest.raises(ConfigError, match="paths.required must exist"):
        validation.load_config(config, Settings)
    required = tmp_path / "required"
    if kind == "directory":
        required.mkdir()
    else:
        required.touch()
    result = validation.load_config(config, Settings)
    assert result.required == required
    assert not result.optional.exists()


def test_invalid_model_is_programming_error_not_config_error(tmp_path):
    @dataclass(frozen=True)
    class InvalidSettings:
        title: str

    with pytest.raises(TypeError, match="must declare section metadata"):
        validation.load_config(tmp_path / "missing.toml", InvalidSettings)


def test_model_constructor_error_propagates(tmp_path):
    @dataclass(frozen=True)
    class BrokenSettings:
        title: str = field(metadata={"section": "export"})

        def __post_init__(self):
            raise ValueError("caller model failure")

    path = tmp_path / "export.toml"
    path.write_text('[export]\ntitle = "Report"\n')
    with pytest.raises(ValueError, match="caller model failure") as error:
        validation.load_config(path, BrokenSettings)
    assert not isinstance(error.value, ConfigError)


def test_dataclass_defaults_make_fields_optional(tmp_path):
    @dataclass(frozen=True)
    class Settings:
        title: str = field(metadata={"section": "export"})
        owner: str = field(default="team", metadata={"section": "export"})
        labels: tuple[str, ...] = field(default_factory=tuple, metadata={"section": "export"})
        destination: Path = field(default=Path("out"), metadata={"section": "paths"})

    config = tmp_path / "settings.toml"
    config.write_text('[export]\ntitle = "Report"\n')
    result = validation.load_config(config, Settings)
    assert result == Settings("Report", "team", (), tmp_path / "out")
    config.write_text('[export]\ntitle = "Report"\nowner = "other"\nlabels = ["daily"]\n')
    result = validation.load_config(config, Settings)
    assert (result.owner, result.labels) == ("other", ("daily",))


def test_default_path_obeys_existence_marker(tmp_path):
    @dataclass(frozen=True)
    class Settings:
        source: Path = field(
            default=Path("source"), metadata={"section": "paths", "must_exist": True},
        )

    config = tmp_path / "settings.toml"
    config.write_text("")
    with pytest.raises(ConfigError, match="paths.source must exist"):
        validation.load_config(config, Settings)
    (tmp_path / "source").mkdir()
    assert validation.load_config(config, Settings).source == tmp_path / "source"


@pytest.mark.parametrize(("variables", "path", "message"), [
    ('root = "out"', "${missing}/report", "invalid path variable"),
    ('root = "out"', "${root", "invalid path variable"),
    ("root = 42", "${root}", "variables.root must be a non-empty string"),
])
def test_invalid_path_variables(tmp_path, variables, path, message):
    @dataclass(frozen=True)
    class Settings:
        destination: Path = field(metadata={"section": "paths"})

    config = tmp_path / "settings.toml"
    config.write_text(f'[variables]\n{variables}\n[paths]\ndestination = "{path}"\n')
    with pytest.raises(ConfigError, match=message):
        validation.load_config(config, Settings)


def test_variables_must_be_a_table(tmp_path):
    @dataclass(frozen=True)
    class Settings:
        destination: Path = field(metadata={"section": "paths"})

    config = tmp_path / "settings.toml"
    config.write_text('variables = "out"\n[paths]\ndestination = "out"\n')
    with pytest.raises(ConfigError, match="variables must be a table"):
        validation.load_config(config, Settings)


def test_variables_only_expand_paths_once(tmp_path):
    @dataclass(frozen=True)
    class Settings:
        title: str = field(metadata={"section": "export"})
        destination: Path = field(metadata={"section": "paths"})

    config = tmp_path / "settings.toml"
    config.write_text(
        '[variables]\nroot = "${other}"\nother = "expanded"\n'
        '[export]\ntitle = "${root}"\n[paths]\ndestination = "${root}/$$report"\n',
    )
    result = validation.load_config(config, Settings)
    assert result.title == "${root}"
    assert result.destination == tmp_path / "${other}/$report"


def test_cli_field_cannot_be_set_by_toml(config_path):
    config_path.write_text(config_path.read_text().replace("[project]", '[project]\ndetail = "full"'))
    with pytest.raises(ConfigError, match="unknown config field: project.detail"):
        load(config_path)


def test_cli_field_requires_default(tmp_path):
    @dataclass(frozen=True)
    class Settings:
        detail: str = field(metadata={"cli": True})

    with pytest.raises(TypeError, match="CLI field detail must declare a default"):
        validation.load_config(tmp_path / "missing.toml", Settings)


def test_cli_field_default_factory_is_preserved(tmp_path):
    @dataclass(frozen=True)
    class Settings:
        labels: tuple[str, ...] = field(default_factory=tuple, metadata={"cli": True})

    config = tmp_path / "empty.toml"
    config.write_text("")
    assert validation.load_config(config, Settings).labels == ()
