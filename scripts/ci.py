#!/usr/bin/env python3
"""Create local configuration and collect safe evidence for this lesson."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
OWNER_PATH = ROOT / ".ci-env-owner.json"
ARTIFACTS = ROOT / "artifacts"
REQUIRED = ("DB_USER", "DB_PASSWORD", "DB_NAME", "APP_PORT")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owns_env() -> bool:
    if not ENV_PATH.is_file() or ENV_PATH.is_symlink() or not OWNER_PATH.is_file():
        return False
    try:
        owner = json.loads(OWNER_PATH.read_text(encoding="utf-8"))
        return owner.get("sha256") == digest(ENV_PATH)
    except (OSError, ValueError):
        return False


def atomic_private_write(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".ci-write-", dir=ROOT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def dotenv_quote(value: str) -> str:
    # Compose understands JSON-style escapes in double-quoted dotenv values.
    # $$ passes a literal dollar through Compose interpolation.
    # `compose config` escapes dollars again when rendering its canonical model;
    # use `compose config --environment` to verify the parsed dotenv value.
    return json.dumps(value, ensure_ascii=False).replace("$", "$$")


def create_env() -> int:
    values = {name: os.environ.get(name, "") for name in REQUIRED}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError("Missing required environment variables: " + ", ".join(missing))
    for name, value in values.items():
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError(f"{name} must be a single line without control characters")
    if not values["APP_PORT"].isdigit() or not 1 <= int(values["APP_PORT"]) <= 65535:
        raise ValueError("APP_PORT must be a port number between 1 and 65535")
    if ENV_PATH.exists() and not owns_env():
        raise ValueError("An existing .env was not created by this script; it was left unchanged")
    content = "".join(f"{name}={dotenv_quote(values[name])}\n" for name in REQUIRED)
    atomic_private_write(ENV_PATH, content)
    atomic_private_write(
        OWNER_PATH,
        json.dumps({"sha256": digest(ENV_PATH), "createdAt": utc_now()}, indent=2) + "\n",
    )
    print("Created .env with mode 0600. Configuration values were not printed.")
    return 0


def read_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.is_file():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            try:
                value = json.loads(raw).replace("$$", "$")
            except ValueError:
                value = raw[1:-1]
        elif raw.startswith("'") and raw.endswith("'"):
            value = raw[1:-1].replace("\\'", "'")
        else:
            value = raw
        values[key.strip()] = value
    return values


def sensitive_values() -> list[str]:
    values = read_dotenv()
    values.update({key: value for key, value in os.environ.items()
                   if re.search(r"PASSWORD|TOKEN|SECRET|API_KEY|PRIVATE_KEY", key, re.I)})
    secrets = {value for key, value in values.items()
               if value and re.search(r"PASSWORD|TOKEN|SECRET|API_KEY|PRIVATE_KEY", key, re.I)}
    return sorted(secrets, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
        # Also cover JSON-escaped passwords in structured command output.
        escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
        if escaped != secret:
            text = text.replace(escaped, "[REDACTED]")
    return text


def write_artifact(name: str, text: str, secrets: list[str]) -> None:
    target = ARTIFACTS / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(redact(text, secrets), encoding="utf-8")


def capture(args: list[str], filename: str, secrets: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=45)
        text = result.stdout
        if result.stderr:
            text += "\nSTDERR:\n" + result.stderr
        if result.returncode:
            text += f"\nCommand exit status: {result.returncode}\n"
        write_artifact(filename, text, secrets)
        return {"exitCode": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as error:
        write_artifact(filename, f"Command could not complete: {error}\n", secrets)
        return {"exitCode": None, "error": redact(str(error), secrets)}


def parse_ps() -> list[dict[str, object]]:
    path = ARTIFACTS / "docker-ps.json"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8").split("\nSTDERR:", 1)[0].strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]
    except ValueError:
        result = []
        for line in text.splitlines():
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    result.append(entry)
            except ValueError:
                pass
        return result


def browser_results() -> dict[str, object]:
    outcome: dict[str, object] = {"executed": False, "passed": False}
    result_path = ARTIFACTS / "playwright-results.json"
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            stats = result.get("stats", {})
            outcome.update({"executed": True, "stats": stats})
            outcome["passed"] = (stats.get("expected", 0) > 0
                                 and stats.get("unexpected", 0) == 0
                                 and stats.get("flaky", 0) == 0
                                 and not result.get("errors"))
        except (OSError, ValueError):
            outcome["error"] = "Could not parse the Playwright JSON report"
    comparison_path = ARTIFACTS / "data-comparison.json"
    if comparison_path.is_file():
        try:
            comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
            outcome["dataComparison"] = comparison
            outcome["passed"] = bool(outcome["passed"] and comparison.get("equal") is True)
        except (OSError, ValueError):
            outcome["passed"] = False
            outcome["comparisonError"] = "Could not parse data comparison"
    else:
        outcome["passed"] = False
    junit = ARTIFACTS / "junit.xml"
    if junit.is_file():
        try:
            ET.parse(junit)
            outcome["junit"] = "junit.xml"
        except ET.ParseError:
            outcome["passed"] = False
            outcome["junitError"] = "JUnit report is invalid XML"
    return outcome


def build_summary() -> dict[str, object]:
    containers = parse_ps()
    services = {str(item.get("Service")): item for item in containers}
    healthy = all(services.get(name, {}).get("State") == "running"
                  and services.get(name, {}).get("Health") == "healthy"
                  for name in ("app", "db"))
    actions = os.environ.get("GITHUB_ACTIONS") == "true" and bool(os.environ.get("GITHUB_RUN_ID"))
    summary: dict[str, object] = {
        "generatedAt": utc_now(),
        "executionMode": "GitHub Actions" if actions else "local",
        "containersHealthy": healthy,
        "services": [{"service": name, "state": value.get("State"),
                      "health": value.get("Health")} for name, value in services.items()],
        "browserTests": browser_results(),
        "configurationArtifact": ".env (all values masked)",
    }
    if actions:
        summary["runUrl"] = (f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
                             f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")
        summary["runnerName"] = os.environ.get("RUNNER_NAME", "")
        summary["runnerOS"] = os.environ.get("RUNNER_OS", "")
        summary["runnerArch"] = os.environ.get("RUNNER_ARCH", "")
    return summary


def summary() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    secrets = sensitive_values()
    result = build_summary()
    write_artifact("test-report.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n", secrets)
    browser = result["browserTests"]
    assert isinstance(browser, dict)
    mode = result["executionMode"]
    text = ["# Deployment and browser test report", "",
            f"- Generated: {result['generatedAt']}",
            f"- Execution mode: {mode}",
            f"- Both services running and healthy: {result['containersHealthy']}",
            f"- Browser tests executed: {browser['executed']}",
            f"- Browser tests and data comparison passed: {browser['passed']}",
            "- The configuration artifact contains masked values only."]
    if mode == "local":
        text.extend(["", "This report records a local execution. It does not confirm registration",
                     "or online status of a GitHub self-hosted runner or a GitHub Actions run."])
    elif result.get("runUrl"):
        text.append(f"- GitHub run: {result['runUrl']}")
    text.extend(["", "## Checks", "",
                 "The browser test compares ordered records read directly from PostgreSQL,",
                 "the API response and the rendered table. See data-comparison.json,",
                 "playwright-results.json, junit.xml and the browser screenshot for evidence.", ""])
    write_artifact("test-report.md", "\n".join(text), secrets)
    print(f"Execution mode: {mode}; containers healthy: {result['containersHealthy']}; "
          f"browser tests passed: {browser['passed']}")
    return 0


def audit_artifacts(secrets: list[str]) -> None:
    for path in ARTIFACTS.rglob("*"):
        if path.is_symlink():
            raise ValueError("Artifacts must not contain symlinks")
        if not path.is_file():
            continue
        data = path.read_bytes()
        for secret in secrets:
            if secret.encode("utf-8") in data:
                raise ValueError(f"A secret value was found in artifact {path.relative_to(ARTIFACTS)}")


def collect() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    secrets = sensitive_values()
    values = read_dotenv()
    masked = "".join(f"{key}=***\n" for key in values)
    write_artifact(".env", masked or "# No runtime .env was available.\n", secrets)
    commands = [
        (["docker", "compose", "logs", "--no-color", "--timestamps"], "deployment-logs.txt"),
        (["docker", "compose", "ps", "--all", "--format", "json"], "docker-ps.json"),
        (["docker", "compose", "ps", "--all"], "docker-ps.txt"),
        (["docker", "compose", "images"], "docker-images.txt"),
        (["docker", "version", "--format", "Client={{.Client.Version}}; Server={{.Server.Version}}"], "docker-version.txt"),
    ]
    statuses = {}
    for args, filename in commands:
        statuses[filename] = capture(args, filename, secrets)
    # Format only non-secret image metadata, never container Config.Env.
    statuses["app-image.txt"] = capture(
        ["docker", "image", "inspect", "github-actions-lesson-app:1.0.0", "--format",
         "ID={{.Id}}\nSize={{.Size}}\nUser={{.Config.User}}\nArchitecture={{.Architecture}}\nOS={{.Os}}"],
        "app-image.txt", secrets,
    )
    compose = ROOT / "docker-compose.yml"
    if compose.is_file():
        write_artifact("docker-compose.yml", compose.read_text(encoding="utf-8"), secrets)
    write_artifact("collection-status.json", json.dumps(statuses, indent=2) + "\n", secrets)
    summary()
    # Check all output, including reports and screenshots generated by the tests.
    audit_artifacts(secrets)
    print("Artifacts collected. Password redaction and secret-value audit passed.")
    return 0


def clean_env() -> int:
    if not ENV_PATH.exists():
        OWNER_PATH.unlink(missing_ok=True)
        print("No runtime .env remains.")
        return 0
    if not owns_env():
        raise ValueError("The existing .env is not owned by this script; it was left unchanged")
    ENV_PATH.unlink()
    OWNER_PATH.unlink(missing_ok=True)
    print("Removed only the runtime .env created by this script.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create-env", "collect", "summary", "clean-env"))
    args = parser.parse_args()
    try:
        return {"create-env": create_env, "collect": collect,
                "summary": summary, "clean-env": clean_env}[args.command]()
    except (OSError, ValueError) as error:
        # Validation errors deliberately identify variable names, never values.
        print(f"CI helper: {redact(str(error), sensitive_values())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
