#!/usr/bin/env python3
"""Checks the launchd plist templates and restart-bridge.sh (launchctl stubbed)."""
import os
import plistlib
import re
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4} kickstart exit=(\d+)$")
FAKE_LAUNCHCTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_CALLS"
[ -z "$STUB_DROP" ] || : > "$STUB_DROP"
exit "$STUB_EXIT"
"""


def render(name: str, home: str) -> dict:
    template = (HERE / name).read_text(encoding="utf-8")
    assert "__HOME__" in template, name
    assert "/Users/" not in template, name
    return plistlib.loads(template.replace("__HOME__", home).encode())


def check_templates() -> None:
    home = "/home/someone"
    base = f"{home}/t3-muse-bridge"

    bridge = render("com.muse.t3bridge.plist", home)
    assert bridge["Label"] == "com.muse.t3bridge", bridge
    assert bridge["ProgramArguments"] == [
        "/usr/bin/python3", f"{base}/bridge.py"], bridge

    restarter = render("com.muse.t3bridge-restarter.plist", home)
    assert restarter["Label"] == "com.muse.t3bridge-restarter", restarter
    assert restarter["ProgramArguments"] == [
        "/bin/sh", f"{base}/restart-bridge.sh"], restarter
    assert restarter["QueueDirectories"] == [f"{base}/restart-trigger"], restarter
    assert restarter["StandardOutPath"] == f"{base}/restart.log", restarter
    assert restarter["StandardErrorPath"] == f"{base}/restart.log", restarter
    assert "KeepAlive" not in restarter, restarter
    assert "RunAtLoad" not in restarter, restarter

    # both templates point at a file this repo ships
    for plist in (bridge, restarter):
        program = Path(plist["ProgramArguments"][1]).name
        assert (HERE / program).is_file(), program


def run_script(stub_exit: int = 0, seed=None, drop: str = "") -> dict:
    """Run the script as the plist does; return what it left behind."""
    with tempfile.TemporaryDirectory(prefix="restarter-test-") as tmp:
        root = Path(tmp)
        base = root / "home" / "t3-muse-bridge"
        trigger = base / "restart-trigger"
        trigger.mkdir(parents=True)
        if seed:
            seed(trigger)
        stubs = root / "stubs"
        stubs.mkdir()
        fake = stubs / "launchctl"
        fake.write_text(FAKE_LAUNCHCTL)
        fake.chmod(0o755)
        calls = root / "calls"
        env = dict(
            os.environ,
            HOME=str(root / "home"),
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            STUB_CALLS=str(calls),
            STUB_EXIT=str(stub_exit),
            STUB_DROP=str(trigger / drop) if drop else "",
        )
        proc = subprocess.run(
            ["/bin/sh", str(HERE / "restart-bridge.sh")],
            env=env, capture_output=True, text=True)
        assert proc.stderr == "", proc.stderr
        log = base / "restart.log"
        return {
            "exit": proc.returncode,
            "calls": calls.read_text().splitlines() if calls.exists() else [],
            "trigger": sorted(p.name for p in trigger.iterdir()),
            "log": log.read_text().splitlines() if log.exists() else [],
        }


def check_log(result: dict, code: int) -> None:
    assert len(result["log"]) == 1, result
    match = LOG_LINE.match(result["log"][0])
    assert match and int(match.group(1)) == code, result


def seed_everything(trigger: Path) -> None:
    # launchd treats any entry as "non-empty", so a leftover of any of these
    # kinds would re-run the job, and bounce the bridge, every few seconds
    for name in ("go", ".DS_Store", "..odd"):
        (trigger / name).write_text("ignored")
    (trigger / "folder").mkdir()
    (trigger / "folder" / "nested").write_text("ignored")


def check_script() -> None:
    expected = f"kickstart -k gui/{os.getuid()}/com.muse.t3bridge"

    result = run_script(seed=seed_everything)
    assert result["calls"] == [expected], result
    assert result["trigger"] == [], result
    assert result["exit"] == 0, result
    check_log(result, 0)

    # a failed kickstart still clears the trigger, so launchd cannot loop on it
    result = run_script(stub_exit=113, seed=seed_everything)
    assert result["trigger"] == [], result
    assert result["exit"] == 113, result
    check_log(result, 113)

    # a trigger dropped during the kickstart survives and gets its own run
    result = run_script(seed=seed_everything, drop="late")
    assert result["calls"] == [expected], result
    assert result["trigger"] == ["late"], result
    check_log(result, 0)

    # launchd can re-run the job with the folder already empty: no second bounce
    result = run_script()
    assert result["calls"] == [], result
    assert result["log"] == [], result
    assert result["exit"] == 0, result


def main() -> None:
    check_templates()
    check_script()
    print("restarter tests: OK")


if __name__ == "__main__":
    main()
