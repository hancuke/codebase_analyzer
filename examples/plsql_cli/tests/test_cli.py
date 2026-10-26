import builtins
import sys
from types import ModuleType

import pytest

from plsql_doc_cli.cli import Detail, load_options, main, run
import plsql_doc_cli.cli as cli


@pytest.fixture
def host(monkeypatch):
    events = []
    calls = {}
    controls = {"normal": False, "special": False}

    def stage(name, kwargs=None):
        events.append(name)
        calls[name] = kwargs
        outcome = controls.get(name)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    class Service:
        def __init__(self, **kwargs):
            stage("service", kwargs)

        def generate(self, **kwargs):
            return stage("normal", kwargs)

        def generate_special_package(self, **kwargs):
            return stage("special", kwargs)

    class Workbench:
        @classmethod
        def open(cls, root, **kwargs):
            stage("open", {"root": root, **kwargs})
            return cls()

        def publish(self, root):
            stage("publish", root)

    attributes = {
        "infra": {"setup_logging": lambda path: stage("logging", path)},
        "infra.observability": {},
        "infra.observability.phoenix_tracing": {
            "init_phoenix_tracing": lambda: stage("init"),
            "shutdown_phoenix_tracing": lambda: stage("shutdown"),
        },
        "processor": {
            "PLSQLConfig": lambda **kwargs: kwargs,
            "PLSQLService": Service,
        },
        "processor.core": {},
        "processor.core.workbench": {"Workbench": Workbench},
    }
    for name, fields in attributes.items():
        module = ModuleType(name)
        for key, value in fields.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    return events, calls, controls


def options(path):
    return load_options(path)


def test_success_forwards_parameters_and_publishes(config_path, host):
    events, calls, _ = host
    config = options(config_path)
    assert main(["run", "--config", str(config_path)]) == 0
    assert events == ["logging", "open", "service", "init", "normal", "special", "publish", "shutdown"]
    assert calls["service"]["config"] == {
        "source_root": config.source_root, "document_root": config.document_root,
        "system_id": "rms", "env_id": "prod",
    }
    assert calls["open"] == {"root": config.workbench_root, "protected_roots": (config.source_root,)}
    assert calls["normal"]["workbench"] is calls["special"]["workbench"]
    assert calls["normal"]["exclude_pattern"] == ["normal_*"]
    assert calls["special"]["include_pattern"] == ["special_*"]
    assert calls["normal"]["sys_prompt_directory"] == config.sys_prompt_directory
    assert calls["special"]["sys_prompt_directory"] == config.sys_prompt_directory
    assert calls["normal"]["simple_user_prompt_directory"] == config.simple_user_prompt_directory
    assert calls["normal"]["complex_user_prompt_directory"] == config.complex_user_prompt_directory
    assert calls["special"]["user_prompt_directory"] == config.special_user_prompts
    assert calls["publish"] == config.document_root
    assert calls["logging"] == str(config.log_file)


def test_prompt_paths_are_independent_config_values(config_path, host):
    _, calls, _ = host
    text = config_path.read_text()
    replacements = {
        "${prompt_root}": "system-templates",
        "${prompt_root}/simple_user_prompts": "single-pass",
        "${prompt_root}/complex_user_prompts": "multi-pass",
        "${prompt_root}/special_user_prompts": "special-package",
    }
    for original, replacement in replacements.items():
        (config_path.parent / replacement).mkdir()
        text = text.replace(f'"{original}"', f'"{config_path.parent.as_posix()}/{replacement}"')
    config_path.write_text(text)
    assert main(["run", "--config", str(config_path)]) == 0
    assert calls["normal"]["sys_prompt_directory"] == config_path.parent / "system-templates"
    assert calls["special"]["sys_prompt_directory"] == config_path.parent / "system-templates"
    assert calls["normal"]["simple_user_prompt_directory"] == config_path.parent / "single-pass"
    assert calls["normal"]["complex_user_prompt_directory"] == config_path.parent / "multi-pass"
    assert calls["special"]["user_prompt_directory"] == config_path.parent / "special-package"


@pytest.mark.parametrize(("normal", "special"), [(True, False), (False, True), (True, True)])
def test_generation_errors_accumulate_without_publication(config_path, host, capsys, normal, special):
    events, _, controls = host
    controls.update(normal=normal, special=special)
    assert run(options(config_path)) == 1
    assert events[-3:] == ["normal", "special", "shutdown"]
    assert "publish" not in events
    message = capsys.readouterr().err
    assert "not published" in message
    if normal:
        assert "normal" in message
    if special:
        assert "special" in message


@pytest.mark.parametrize(("phase", "tail"), [
    ("init", ["init"]),
    ("normal", ["normal", "shutdown"]),
    ("special", ["special", "shutdown"]),
    ("publish", ["publish", "shutdown"]),
    ("shutdown", ["publish", "shutdown"]),
])
def test_unexpected_exceptions_propagate_and_cleanup(config_path, host, phase, tail):
    events, _, controls = host
    controls[phase] = RuntimeError(f"{phase} failed")
    with pytest.raises(RuntimeError, match=f"{phase} failed"):
        run(options(config_path))
    assert events[-len(tail):] == tail
    if phase in ("init", "normal", "special"):
        assert "publish" not in events
    if phase == "normal":
        assert "special" not in events


def test_cleanup_error_preserves_original_exception_context(config_path, host):
    _, _, controls = host
    controls.update(normal=ValueError("generation"), shutdown=RuntimeError("cleanup"))
    with pytest.raises(RuntimeError, match="cleanup") as error:
        run(options(config_path))
    assert isinstance(error.value.__context__, ValueError)
    assert str(error.value.__context__) == "generation"


@pytest.mark.parametrize("phase", ["normal", "special"])
def test_invalid_service_return_is_not_success(config_path, host, phase):
    events, _, controls = host
    controls[phase] = None
    with pytest.raises(TypeError, match="bool error flag"):
        run(options(config_path))
    assert "publish" not in events
    assert events[-1] == "shutdown"


def test_help_without_host_and_explicit_missing_dependency(config_path, monkeypatch, capsys):
    original = builtins.__import__

    def no_host(name, *args, **kwargs):
        if name.split(".")[0] in ("processor", "infra"):
            raise ModuleNotFoundError(f"No module named '{name}'")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_host)
    with pytest.raises(SystemExit) as error:
        main(["run", "--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--config" in help_text
    assert "--mode" not in help_text
    with pytest.raises(ModuleNotFoundError, match="infra"):
        main(["run", "--config", str(config_path)])


@pytest.mark.parametrize("detail", list(Detail))
def test_cli_detail_is_converted_to_enum(config_path, monkeypatch, detail):
    received = []
    monkeypatch.setattr(cli, "run", lambda options: received.append(options) or 0)
    assert main(["run", "--config", str(config_path), "--detail", detail.value]) == 0
    assert received[0].detail is detail
    assert load_options(config_path).detail is Detail.SUMMARY


def test_invalid_cli_enum_rejected_before_config_loading(monkeypatch, capsys):
    def unexpected_load(path):
        pytest.fail("invalid enum must be rejected before loading config")

    monkeypatch.setattr(cli, "load_options", unexpected_load)
    with pytest.raises(SystemExit) as error:
        main(["run", "--detail", "invalid"])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "invalid choice" in message
    assert "summary" in message and "full" in message


def test_full_detail_reports_publication_without_changing_stages(config_path, host, capsys):
    events, _, _ = host
    assert main(["run", "--config", str(config_path), "--detail", "full"]) == 0
    message = capsys.readouterr().out
    assert f"Published: {config_path.parent / 'docs'}" in message
    assert f"Source: {config_path.parent / 'source'}" in message
    assert events[-4:] == ["normal", "special", "publish", "shutdown"]
