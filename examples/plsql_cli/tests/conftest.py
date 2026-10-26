from pathlib import Path

import pytest


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "source").mkdir()
    for name in ("simple_user_prompts", "complex_user_prompts", "special_user_prompts"):
        (root / "prompts" / name).mkdir(parents=True)
    path = root / "plsql-docs.toml"
    path.write_text(
        f'[variables]\nprompt_root = "{root.as_posix()}/prompts"\n'
        '[project]\nsystem_id = "rms"\nenv_id = "prod"\n'
        f'[paths]\nsource_root = "{root.as_posix()}/source"\n'
        f'document_root = "{root.as_posix()}/docs"\n'
        f'log_file = "{root.as_posix()}/logs/plsql.log"\n'
        '[prompts]\nsys_prompt_directory = "${prompt_root}"\n'
        'simple_user_prompt_directory = "${prompt_root}/simple_user_prompts"\n'
        'complex_user_prompt_directory = "${prompt_root}/complex_user_prompts"\n'
        'special_user_prompts = "${prompt_root}/special_user_prompts"\n'
        '[patterns]\nexclude = ["normal_*"]\ninclude_special = ["special_*"]\n',
        encoding="utf-8",
    )
    return path
