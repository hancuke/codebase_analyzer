"""Load TOML or CLI options using dataclass fields, annotations and metadata."""

from __future__ import annotations

import os
import sys
from argparse import SUPPRESS, ArgumentParser, Namespace
from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path, PureWindowsPath
from string import Template
from typing import TypeVar, get_type_hints

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


T = TypeVar("T")


class ConfigError(ValueError):
    """Invalid configuration supplied by the caller."""


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _parse(annotation: object, value: object, name: str, base: Path, absolute: bool = False):
    if annotation == tuple[str, ...]:
        if not isinstance(value, list):
            raise ConfigError(f"{name} must be a list of strings")
        return tuple(_text(name, item) for item in value)
    text = _text(name, value)
    if annotation is str:
        return text
    path = Path(text)
    if os.name != "nt" and (PureWindowsPath(text).drive or text.startswith("\\")):
        raise ConfigError(f"{name}: Windows path cannot be used on this platform: {text}")
    if os.name == "nt" and path.anchor and not path.is_absolute():
        raise ConfigError(f"{name}: expected a relative or fully absolute path")
    if absolute and not path.is_absolute():
        raise ConfigError(f"{name} must be an absolute path: {text}")
    return (path if path.is_absolute() else base / path).resolve()


def _schema(model: type[T]):
    if not is_dataclass(model):
        raise TypeError("configuration model must be a dataclass")
    annotations = get_type_hints(model)
    schema = {}
    for item in fields(model):
        if item.metadata.get("cli"):
            if not item.init or (item.default is MISSING and item.default_factory is MISSING):
                raise TypeError(f"CLI field {item.name} must declare a default and allow initialization")
            continue
        section = item.metadata.get("section")
        if not item.init or not isinstance(section, str) or not section:
            raise TypeError(f"{model.__name__}.{item.name} must declare section metadata")
        if annotations[item.name] not in (str, Path, tuple[str, ...]):
            raise TypeError(f"unsupported configuration type: {annotations[item.name]}")
        schema.setdefault(section, {})[item.name] = item
    if "variables" in schema:
        raise TypeError("variables is reserved for configuration path prefixes")
    return schema, annotations


def _parse_values(raw_values: Mapping[str, object], model: type[T], base: Path) -> dict[str, object]:
    schema, annotations = _schema(model)
    values = {}
    for section, items in schema.items():
        for name, item in items.items():
            label = f"{section}.{name}"
            if name in raw_values:
                value = _parse(
                    annotations[name], raw_values[name], label, base,
                    absolute=item.metadata.get("absolute", False),
                )
            elif item.default is not MISSING:
                value = item.default
            elif item.default_factory is not MISSING:
                value = item.default_factory()
            else:
                raise ConfigError(f"missing required field: {label}")
            if item.metadata.get("nonempty") and not value:
                raise ConfigError(f"{label} must not be empty")
            if isinstance(value, Path) and name not in raw_values:
                value = _parse(
                    Path, str(value), label, base,
                    absolute=item.metadata.get("absolute", False),
                )
            if isinstance(value, Path) and item.metadata.get("must_exist") and not value.exists():
                raise ConfigError(f"{label} must exist: {value}")
            values[name] = value
    return values


def add_arguments(parser: ArgumentParser, model: type[T]) -> None:
    """Register business fields without validating their values or requiredness."""
    schema, annotations = _schema(model)
    for section, items in schema.items():
        for name in items:
            flag = "--" + name.replace("_", "-")
            help_text = f"{section}.{name} (cannot be combined with --config)."
            if annotations[name] == tuple[str, ...]:
                parser.add_argument(flag, nargs="*", default=SUPPRESS, help=help_text)
            else:
                parser.add_argument(flag, default=SUPPRESS, help=help_text)


def load_arguments(args: Namespace, model: type[T]) -> T:
    """Choose exactly one business input source, then apply the model's rules."""
    schema, _ = _schema(model)
    values = {
        name: getattr(args, name)
        for items in schema.values() for name in items if hasattr(args, name)
    }
    if args.config is not None:
        if values:
            flags = ", ".join("--" + name.replace("_", "-") for name in values)
            raise ConfigError(f"--config cannot be combined with business arguments: {flags}")
        return load_config(args.config, model)
    try:
        parsed = _parse_values(values, model, Path.cwd())
    except ConfigError:
        raise
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot load command-line options: {error}") from error
    return model(**parsed)


def load_config(config_path: Path, model: type[T]) -> T:
    """Required fields have no default; metadata declares section and constraints."""
    schema, annotations = _schema(model)
    try:
        config_path = config_path.resolve()
        with config_path.open("rb") as stream:
            document = tomllib.load(stream)
        variables = document.pop("variables", {})
        if not isinstance(variables, dict):
            raise ConfigError("variables must be a table")
        for name, value in variables.items():
            _text(f"variables.{name}", value)
        for section, table in document.items():
            if section not in schema:
                raise ConfigError(f"unknown config section: {section}")
            if not isinstance(table, dict):
                raise ConfigError(f"{section} must be a table")
            for key in table:
                if key not in schema[section]:
                    raise ConfigError(f"unknown config field: {section}.{key}")
        values = {}
        for section, items in schema.items():
            table = document.get(section, {})
            for name in items:
                label = f"{section}.{name}"
                if name in table:
                    raw = table[name]
                    if annotations[name] is Path and isinstance(raw, str):
                        try:
                            raw = Template(raw).substitute(variables)
                        except (KeyError, ValueError) as error:
                            raise ConfigError(f"{label}: invalid path variable: {error}") from error
                    values[name] = raw
        values = _parse_values(values, model, config_path.parent)
    except ConfigError:
        raise
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot load configuration {config_path}: {error}") from error
    return model(**values)
