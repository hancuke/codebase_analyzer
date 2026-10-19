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
        '[variables]\nprompt_root = "prompts"\n'
        '[project]\nsystem_id = "rms"\nenv_id = "prod"\n'
        '[paths]\nsource_root = "source"\ndocument_root = "docs"\n'
        'log_file = "logs/plsql.log"\n'
        '[prompts]\nsys_prompt_directory = "${prompt_root}"\n'
        'simple_user_prompt_directory = "${prompt_root}/simple_user_prompts"\n'
        'complex_user_prompt_directory = "${prompt_root}/complex_user_prompts"\n'
        'special_user_prompts = "${prompt_root}/special_user_prompts"\n'
        '[patterns]\nexclude = ["normal_*"]\ninclude_special = ["special_*"]\n',
        encoding="utf-8",
    )
    return path
