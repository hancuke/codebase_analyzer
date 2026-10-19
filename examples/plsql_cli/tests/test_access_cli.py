import builtins
import sys
from types import ModuleType

import pytest

from access_doc_cli import cli
from plsql_doc_cli.validation import ConfigError


@pytest.fixture
def access_config(tmp_path):
    for name in ("source", "tables", "prompts/ui", "prompts/vba"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "lookup.csv").write_text("form,table\n")
    path = tmp_path / "access-docs.toml"
    path.write_text(
        '[variables]\nprompt_root = "prompts"\n'
        '[project]\nsystem_id = "rms"\nenv_id = "prod"\n'
        '[paths]\nsource_root = "source"\ndocument_root = "docs"\n'
        'log_file = "logs/form.log"\nlookup_ref_csv_path = "lookup.csv"\n'
        'table_file_folder_path = "tables"\n'
        '[prompts]\nsys_prompt_directory = "${prompt_root}"\n'
        'ui_user_prompt_directory = "${prompt_root}/ui"\n'
        'vba_user_prompt_directory = "${prompt_root}/vba"\n',
    )
    return path


@pytest.fixture
def host(monkeypatch):
    events, calls = [], {}
    mapping = {"order_form": ("orders",)}
    controls = {"ui": (mapping, False), "entries": False}

    def stage(name, value=None):
        events.append(name)
        calls[name] = value
        outcome = controls.get(name)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    class Service:
        def __init__(self, config):
            stage("service", config)

        def analyze_form_ui(self, **kwargs):
            return stage("ui", kwargs)

        def analyze_entrys(self, **kwargs):
            return stage("entries", kwargs)

    class Workbench:
        @classmethod
        def open(cls, root, **kwargs):
            stage("open", {"root": root, **kwargs})
            return cls()

        def publish(self, root):
            stage("publish", root)

    for name, attributes in {
        "infra": {"setup_logging": lambda path: stage("logging", path)},
        "infra.observability": {},
        "infra.observability.phoenix_tracing": {
            "init_phoenix_tracing": lambda: stage("init"),
            "shutdown_phoenix_tracing": lambda: stage("shutdown"),
        },
        "processor": {"AccessConfig": lambda **kwargs: kwargs, "AccessService": Service},
        "processor.core": {},
        "processor.core.workbench": {"Workbench": Workbench},
    }.items():
        module = ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    return events, calls, controls


def test_success_forwards_config_and_ui_mapping(access_config, host, monkeypatch):
    events, calls, controls = host
    monkeypatch.chdir(access_config.parent)
    options = cli.load_options(access_config)
    assert cli.main(["run"]) == 0
    assert events == ["logging", "open", "service", "init", "ui", "entries", "publish", "shutdown"]
    assert calls["service"] == {
        "source_root": options.source_root, "document_root": options.document_root,
        "system_id": "rms", "env_id": "prod",
    }
    assert calls["open"] == {"root": options.workbench_root, "protected_roots": (options.source_root,)}
    workbench = calls["ui"]["workbench"]
    assert calls["ui"] == {
        "workbench": workbench,
        "lookup_ref_csv_path": options.lookup_ref_csv_path,
        "table_file_folder_path": options.table_file_folder_path,
        "sys_prompt_directory": options.sys_prompt_directory,
        "user_prompt_directory": access_config.parent / "prompts/ui",
    }
    assert calls["entries"] == {
        "workbench": workbench,
        "form_lookup_mapping": controls["ui"][0],
        "sys_prompt_directory": options.sys_prompt_directory,
        "user_prompt_directory": access_config.parent / "prompts/vba",
    }
    assert calls["entries"]["form_lookup_mapping"] is controls["ui"][0]
    assert calls["publish"] == options.document_root
    assert calls["logging"] == str(options.log_file)


@pytest.mark.parametrize(("ui_error", "entries_error"), [(True, False), (False, True), (True, True)])
def test_errors_accumulate_without_publishing(access_config, host, capsys, ui_error, entries_error):
    events, calls, controls = host
    controls["ui"] = (controls["ui"][0], ui_error)
    controls["entries"] = entries_error
    assert cli.main(["run", "--config", str(access_config)]) == 1
    assert events[-3:] == ["ui", "entries", "shutdown"]
    assert "publish" not in calls
    message = capsys.readouterr().err
    assert "not published" in message
    if ui_error:
        assert "form_ui" in message
    if entries_error:
        assert "entries" in message


@pytest.mark.parametrize("phase", ["init", "ui", "entries", "publish", "shutdown"])
def test_unexpected_errors_propagate(access_config, host, phase):
    events, _, controls = host
    controls[phase] = RuntimeError(f"{phase} failed")
    with pytest.raises(RuntimeError, match=f"{phase} failed"):
        cli.run(cli.load_options(access_config))
    assert events[-1] == ("init" if phase == "init" else "shutdown")
    if phase in ("init", "ui", "entries"):
        assert "publish" not in events
    if phase == "ui":
        assert "entries" not in events


@pytest.mark.parametrize("phase", ["ui", "entries"])
def test_invalid_service_error_flag_is_not_success(access_config, host, phase):
    events, _, controls = host
    controls[phase] = (controls["ui"][0], None) if phase == "ui" else None
    with pytest.raises(TypeError, match="bool error flag"):
        cli.run(cli.load_options(access_config))
    assert "publish" not in events
    assert events[-1] == "shutdown"


@pytest.mark.parametrize("field", [
    "source_root", "lookup_ref_csv_path", "table_file_folder_path",
    "sys_prompt_directory", "ui_user_prompt_directory", "vba_user_prompt_directory",
])
def test_missing_required_resource_is_input_error(access_config, host, field, capsys):
    events, _, _ = host
    options = cli.load_options(access_config)
    resource = getattr(options, field)
    lines = access_config.read_text().splitlines()
    access_config.write_text("\n".join(
        f'{field} = "missing-resource"' if line.startswith(f"{field} = ") else line
        for line in lines
    ))
    with pytest.raises(SystemExit) as error:
        cli.main(["run", "--config", str(access_config)])
    assert error.value.code == 2
    assert f"{field} must exist" in capsys.readouterr().err
    assert events == []
    assert resource.exists()


def test_configuration_is_independent_of_cwd_and_strict(access_config, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    options = cli.load_options(access_config)
    assert options.ui_user_prompt_directory == access_config.parent / "prompts/ui"
    assert not options.document_root.exists()
    options.env_id = "test"
    assert options.env_id == "test"
    access_config.write_text(access_config.read_text().replace("env_id", "env_ids"))
    with pytest.raises(ConfigError, match="unknown config field"):
        cli.load_options(access_config)


def test_help_and_missing_host_dependency(access_config, monkeypatch, capsys):
    original = builtins.__import__

    def no_host(name, *args, **kwargs):
        if name.split(".")[0] in ("infra", "processor"):
            raise ModuleNotFoundError(f"No module named '{name}'")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_host)
    with pytest.raises(SystemExit) as error:
        cli.main(["run", "--help"])
    assert error.value.code == 0
    assert "--config" in capsys.readouterr().out
    with pytest.raises(ModuleNotFoundError, match="infra"):
        cli.main(["run", "--config", str(access_config)])
