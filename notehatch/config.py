import os
import sys
import tomllib
from pathlib import Path

from .errors import EXIT_USAGE, NHError

SETTINGS = {
    "export.front_matter": (("yaml", "none"), "yaml"),
    "export.images": (("extract", "keep"), "extract"),
    "export.edited_files": (("keep", "overwrite"), "keep"),
    "export.mtime": (("updated", "export_time"), "updated"),
    "export.empty_days": (("skip", "include"), "skip"),
    "export.unknown_folder": (("refuse_on_overlap", "refuse_any_files"), "refuse_any_files"),
    "export.unchanged_exit": (("success", "warn"), "success"),
    "verify.scope": (("all_source_days", "manifest_days"), "all_source_days"),
    "export.missing_files": (("rewrite", "conflict"), "rewrite"),
    "lock.stale_requires_age": (("true", "false"), "false"),
    "source.env_precedence": (("notehatch_first", "noterepo_first"), "notehatch_first"),
    "export.mtime_change_rewrites": (("true", "false"), "true"),
}
ORIGINAL = ("export.front_matter", "export.images", "export.edited_files", "export.mtime",
            "export.empty_days")  # the settings that shape exported files; recorded in the manifest
# Settings whose environment variable has no NOTEHATCH_ prefix and wins over every other source.
OVERRIDE_ENV = {"export.mtime_change_rewrites": "EXPORT_MTIME_CHANGE_REWRITES", "source.env_precedence": "SOURCE_ENV_PRECEDENCE", "lock.stale_requires_age": "LOCK_STALE_REQUIRES_AGE", "export.missing_files": "EXPORT_MISSING_FILES", "verify.scope": "VERIFY_SCOPE", "export.unchanged_exit": "EXPORT_UNCHANGED_EXIT", "export.unknown_folder": "EXPORT_UNKNOWN_FOLDER"}


def env_name(key):
    return "NOTEHATCH_" + key.replace(".", "_").upper()


def default_config_path(env=os.environ):
    if sys.platform == "win32":
        base = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "NoteHatch" / "config.toml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NoteHatch" / "config.toml"
    base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "notehatch" / "config.toml"


def check_value(key, value, origin):
    if key not in SETTINGS:
        raise NHError(EXIT_USAGE, f"unknown setting {key!r} ({origin}); valid keys: {', '.join(SETTINGS)}")
    allowed = SETTINGS[key][0]
    if value not in allowed:
        raise NHError(EXIT_USAGE, f"invalid value {value!r} for {key} ({origin}); allowed values: {', '.join(allowed)}")
    return value


def config_path(flag, env=os.environ):
    """(path, must_exist) for the config file; only the --config flag must name an existing file."""
    if flag:
        return Path(flag), True
    if env.get("NOTEHATCH_CONFIG"):
        return Path(env["NOTEHATCH_CONFIG"]), False
    return default_config_path(env), False


def read_file(path, explicit):
    """Flat {key: value} from the config file."""
    if not path.exists():
        if explicit:
            raise NHError(EXIT_USAGE, f"config file {path} does not exist")
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise NHError(EXIT_USAGE, f"config file {path} is malformed: {e}")
    except (OSError, UnicodeDecodeError) as e:
        raise NHError(EXIT_USAGE, f"config file {path} is unreadable: {getattr(e, 'strerror', None) or e}")
    flat = {}
    for section, body in data.items():
        if not isinstance(body, dict):
            raise NHError(EXIT_USAGE, f"config file {path}: unknown key {section!r}; valid keys: {', '.join(SETTINGS)}")
        for name, value in body.items():
            flat[f"{section}.{name}"] = value
    for key, value in list(flat.items()):
        if isinstance(value, bool):
            value = flat[key] = "true" if value else "false"
        check_value(key, value, f"config file {path}")
    return flat


def resolve(flag_config=None, set_pairs=(), env=os.environ):
    """{key: (value, source label)} following flag > env > file > default."""
    path, explicit = config_path(flag_config, env)
    from_file = read_file(path, explicit)
    flags = {}
    for pair in set_pairs or ():
        key, sep, value = pair.partition("=")
        if not sep:
            raise NHError(EXIT_USAGE, f"invalid --set {pair!r}: expected key=value")
        flags[key.strip()] = check_value(key.strip(), value.strip(), "--set")
    out = {}
    for key, (_, default) in SETTINGS.items():
        forced = OVERRIDE_ENV.get(key)
        if forced and forced in env:
            out[key] = (check_value(key, env[forced], f"env {forced}"), f"env {forced}")
        elif key in flags:
            out[key] = (flags[key], "flag --set")
        elif env_name(key) in env:
            out[key] = (check_value(key, env[env_name(key)], f"env {env_name(key)}"), f"env {env_name(key)}")
        elif key in from_file:
            out[key] = (from_file[key], f"config file {path}")
        else:
            out[key] = (default, "default")
    return out


def values(resolved):
    return {k: v[0] for k, v in resolved.items()}


def set_value(key, value, flag_config=None, env=os.environ):
    check_value(key, value, "config set")
    path, explicit = config_path(flag_config, env)
    current = read_file(path, False)
    current[key] = value
    sections = {}
    for k, v in sorted(current.items()):
        sec, _, name = k.partition(".")
        sections.setdefault(sec, {})[name] = v
    text = "".join(
        f"[{sec}]\n" + "".join(f'{n} = "{v}"\n' for n, v in body.items()) + "\n"
        for sec, body in sections.items())
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        raise NHError(EXIT_USAGE, f"cannot write config file {path}: {e.strerror or e}")
    return path
