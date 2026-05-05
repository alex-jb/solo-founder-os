"""Tests for solo_founder_os.cron — sfos-cron weekly job installer."""
from __future__ import annotations
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import solo_founder_os.cron as cron
from solo_founder_os.cron import (
    JOBS,
    _emit_crontab_block,
    _wrapper_path,
    install_one,
    is_loaded,
    main,
    render_wrapper,
    uninstall_one,
    write_job_files,
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect HOME so the installer never touches the real
    ~/Library/LaunchAgents or ~/.solo-founder-os."""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cron, "CRON_DIR",
                          tmp_path / ".solo-founder-os" / "cron")
    monkeypatch.setattr(cron, "LOG_DIR",
                          tmp_path / ".solo-founder-os" / "cron-logs")


def _stub_launchctl(monkeypatch, *, returncode: int = 0,
                     output: str = "", track: list | None = None):
    def fake(*args):
        if track is not None:
            track.append(args)
        return (returncode, output)
    monkeypatch.setattr(cron, "_launchctl", fake)


# ───────────────── shape ─────────────────


def test_jobs_contain_canonical_entries():
    """Sunday cron schedule must include all 4 layers in the loop."""
    labels = {j.label for j in JOBS}
    assert "com.alexji.sfos.eval" in labels
    assert "com.alexji.sfos.council" in labels
    assert "com.alexji.sfos.evolver" in labels
    assert "com.alexji.sfos.retro" in labels
    assert all(j.weekday == 0 for j in JOBS), "all jobs must run Sunday"


def test_jobs_run_in_dependency_order():
    """eval (L6) → council (L5) → evolver (L4) → retro: each downstream
    job needs the upstream's data to be fresh that morning. eval writes
    drift signals; council reads them; evolver reads BOTH drift +
    council notes; retro digests everything."""
    by_label = {j.label: (j.hour, j.minute) for j in JOBS}
    eval_t = by_label["com.alexji.sfos.eval"]
    council_t = by_label["com.alexji.sfos.council"]
    evol_t = by_label["com.alexji.sfos.evolver"]
    retro_t = by_label["com.alexji.sfos.retro"]
    assert eval_t < council_t < evol_t < retro_t


def test_council_job_passes_auto_from_drift_flag():
    """The L5 council cron job must invoke --auto-from-drift; without
    that flag the council does nothing (just a usage error from the CLI)."""
    council = next(j for j in JOBS if j.label == "com.alexji.sfos.council")
    assert "--auto-from-drift" in council.extra_args


# ───────────────── render_wrapper ─────────────────


def test_render_wrapper_sources_zshrc_and_zshenv():
    job = JOBS[0]
    body = render_wrapper(job)
    assert "$HOME/.zshenv" in body
    assert "$HOME/.zshrc" in body


def test_render_wrapper_uses_python_module_invocation():
    job = JOBS[0]
    body = render_wrapper(job)
    assert f"-m {job.module}" in body


def test_render_wrapper_creates_log_dir():
    job = JOBS[0]
    body = render_wrapper(job)
    assert "mkdir -p" in body


def test_render_wrapper_marked_auto_generated():
    body = render_wrapper(JOBS[0])
    assert "DO NOT EDIT" in body


def test_render_wrapper_suppresses_runpy_warning():
    """The -W ignore::RuntimeWarning:runpy flag was added in v0.25.1
    after the harmless runpy warning kept showing up in cron-logs.
    Scoped to runpy specifically so real RuntimeWarnings still surface."""
    body = render_wrapper(JOBS[0])
    assert "-W ignore::RuntimeWarning:runpy" in body


def test_preflight_sterile_import_ok_when_installed(monkeypatch):
    """When the package is importable from cwd=/, preflight returns ok."""
    from solo_founder_os.cron import _preflight_sterile_import
    fake_calls = []
    def fake_run(*args, **kwargs):
        fake_calls.append((args, kwargs))
        class R:
            returncode = 0
            stdout = "0.26.4\n"
            stderr = ""
        return R()
    monkeypatch.setattr("subprocess.run", fake_run)
    ok, hint = _preflight_sterile_import()
    assert ok is True
    assert hint == ""
    # Must run with cwd=/
    assert fake_calls[0][1].get("cwd") == "/"


def test_preflight_sterile_import_fail_returns_install_hint(monkeypatch):
    """When the package isn't importable, hint must include the
    pip install -e fix verbatim so the operator can copy-paste."""
    from solo_founder_os.cron import _preflight_sterile_import
    def fake_run(*args, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "ModuleNotFoundError: No module named 'solo_founder_os'"
        return R()
    monkeypatch.setattr("subprocess.run", fake_run)
    ok, hint = _preflight_sterile_import()
    assert ok is False
    assert "pip install --user -e ." in hint
    assert "ModuleNotFoundError" in hint


def test_main_install_aborts_when_preflight_fails(monkeypatch, tmp_path,
                                                       capsys):
    """sfos-cron install should refuse to write plists if SFOS isn't
    pip-installed — the whole point is to prevent the v0.26.2 bug
    class on a fresh machine."""
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    import solo_founder_os.cron as cron
    monkeypatch.setattr(cron, "_preflight_sterile_import",
                          lambda: (False, "  fake install hint"))
    rc = main(["install", "--no-load"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "pre-flight FAILED" in err
    assert "fake install hint" in err
    # Refused → no plists written
    plist = pathlib.Path.home() / "Library" / "LaunchAgents" / \
        "com.alexji.sfos.eval.plist"
    # (using monkeypatched home, but we didn't set Library/LaunchAgents)
    assert not plist.exists()


def test_find_repo_root_locates_pyproject():
    """Walks up from the cron module until it finds pyproject.toml."""
    from solo_founder_os.cron import _find_repo_root
    root = _find_repo_root()
    assert root is not None
    assert (root / "pyproject.toml").exists()
    # Should be the SFOS repo root, not some random parent
    assert (root / "solo_founder_os").is_dir()


def test_main_install_ensure_pip_install_auto_fixes(monkeypatch, tmp_path,
                                                         capsys):
    """When pre-flight fails and --ensure-pip-install is passed, the
    CLI auto-runs `pip install -e <repo>` and re-checks."""
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    import solo_founder_os.cron as cron

    # First preflight fails, second passes (after pip install)
    state = {"calls": 0}
    def fake_preflight():
        state["calls"] += 1
        if state["calls"] == 1:
            return (False, "fake initial-fail hint")
        return (True, "")
    monkeypatch.setattr(cron, "_preflight_sterile_import", fake_preflight)

    pip_calls: list = []
    def fake_pip(path):
        pip_calls.append(path)
        return (0, "Successfully installed solo-founder-os-0.27.0")
    monkeypatch.setattr(cron, "_pip_install_editable", fake_pip)

    _stub_launchctl(monkeypatch)
    rc = main(["install", "--no-load", "--ensure-pip-install"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "auto-installing" in err
    assert "pip install ✓" in err
    assert len(pip_calls) == 1
    assert state["calls"] == 2  # pre-fail + post-install re-check


def test_main_install_ensure_pip_install_pip_failure_aborts(
    monkeypatch, tmp_path, capsys,
):
    """If pip install itself fails, the CLI must still abort with
    rc=2 and surface the pip stderr — never silently install broken
    plists."""
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    import solo_founder_os.cron as cron
    monkeypatch.setattr(cron, "_preflight_sterile_import",
                          lambda: (False, "preflight hint"))
    monkeypatch.setattr(cron, "_pip_install_editable",
                          lambda path: (1, "ERROR: pip exploded"))
    rc = main(["install", "--no-load", "--ensure-pip-install"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "pip install FAILED" in err
    assert "pip exploded" in err


def test_main_install_skip_preflight_bypasses_check(monkeypatch, tmp_path):
    """--skip-preflight is the escape hatch for testing the install
    path itself (or repairing a half-broken install)."""
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    import solo_founder_os.cron as cron
    # Make preflight fail but pass --skip-preflight
    monkeypatch.setattr(cron, "_preflight_sterile_import",
                          lambda: (False, "shouldn't be called"))
    _stub_launchctl(monkeypatch)
    rc = main(["install", "--no-load", "--skip-preflight"])
    assert rc == 0


def test_render_wrapper_cd_to_home_before_python():
    """Regression for the 2026-05-04 production bug: launchd inherited
    CWD from the directory where the plist was first loaded (the
    solo-founder-os repo). When Python ran `-m solo_founder_os.eval`,
    sys.path picked up the dev-tree's `solo_founder_os/` AHEAD of the
    pip-installed package, causing partial-import failures.
    Wrapper must `cd $HOME` first so CWD is always neutral."""
    body = render_wrapper(JOBS[0])
    cd_pos = body.find('cd "$HOME"')
    exec_pos = body.find("exec ")
    assert cd_pos >= 0, "wrapper must cd to $HOME"
    assert cd_pos < exec_pos, "cd must come BEFORE exec"


# ───────────────── write_job_files ─────────────────


def test_write_job_files_creates_plist_and_wrapper(tmp_path):
    job = JOBS[0]
    plist_path, wrapper_path = write_job_files(job)
    assert plist_path.exists()
    assert wrapper_path.exists()
    # Wrapper must be executable
    mode = wrapper_path.stat().st_mode & 0o777
    assert mode & 0o100, f"wrapper not executable: {oct(mode)}"
    # Plist must reference the wrapper
    plist_text = plist_path.read_text()
    assert str(wrapper_path) in plist_text
    assert "<key>Weekday</key>" in plist_text


def test_write_job_files_idempotent(tmp_path):
    job = JOBS[0]
    p1, w1 = write_job_files(job)
    p2, w2 = write_job_files(job)
    assert p1 == p2 and w1 == w2


# ───────────────── install_one / uninstall_one ─────────────────


def test_install_one_loads_via_launchctl(monkeypatch):
    calls: list = []
    _stub_launchctl(monkeypatch, returncode=0, output="ok", track=calls)
    info = install_one(JOBS[0], load=True)
    assert info["loaded"] is True
    # First call is unload (idempotent reload), second is `load -w`
    assert calls[0][0] == "unload"
    assert calls[1][:2] == ("load", "-w")


def test_install_one_no_load_skips_launchctl(monkeypatch):
    calls: list = []
    _stub_launchctl(monkeypatch, track=calls)
    info = install_one(JOBS[0], load=False)
    assert info["loaded"] is False
    assert calls == []


def test_install_one_load_failure_records_error(monkeypatch):
    _stub_launchctl(monkeypatch, returncode=1, output="oops")
    info = install_one(JOBS[0], load=True)
    assert info["loaded"] is False
    assert "oops" in info["load_error"]


def test_uninstall_one_removes_plist(monkeypatch, tmp_path):
    _stub_launchctl(monkeypatch)
    write_job_files(JOBS[0])  # so the plist exists
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{JOBS[0].label}.plist"
    assert plist_path.exists()
    info = uninstall_one(JOBS[0])
    assert info["removed"] is True
    assert not plist_path.exists()


def test_uninstall_one_missing_plist_is_noop(monkeypatch):
    _stub_launchctl(monkeypatch)
    info = uninstall_one(JOBS[0])
    assert info["removed"] is False


def test_is_loaded_reflects_launchctl_returncode(monkeypatch):
    _stub_launchctl(monkeypatch, returncode=0)
    assert is_loaded(JOBS[0]) is True
    _stub_launchctl(monkeypatch, returncode=1)
    assert is_loaded(JOBS[0]) is False


# ───────────────── crontab fallback ─────────────────


def test_crontab_block_renders_all_jobs():
    block = _emit_crontab_block(JOBS)
    for j in JOBS:
        assert f"-m {j.module}" in block
    # Sunday in cron is dow=0 (final field)
    for line in block.splitlines():
        if line and not line.startswith("#"):
            fields = line.split(maxsplit=5)
            if len(fields) >= 5:
                assert fields[4] == "0", f"non-Sunday line: {line}"


# ───────────────── CLI ─────────────────


def test_cli_install_plan_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    rc = main(["install", "--plan"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Plan:" in out
    assert not (tmp_path / "Library").exists()
    assert not (tmp_path / ".solo-founder-os" / "cron").exists()


def test_cli_install_writes_files(monkeypatch, tmp_path):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    _stub_launchctl(monkeypatch)
    rc = main(["install", "--no-load"])
    assert rc == 0
    for job in JOBS:
        assert (tmp_path / "Library" / "LaunchAgents"
                  / f"{job.label}.plist").exists()
        assert _wrapper_path(job).exists()


def test_cli_install_linux_emits_crontab(monkeypatch, capsys):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    rc = main(["install"])  # no --linux needed when actually on Linux
    assert rc == 0
    out = capsys.readouterr().out
    assert "* * 0" in out  # cron Sunday
    # No plist should have been written even though we ran install
    home = pathlib.Path.home()
    assert not (home / "Library" / "LaunchAgents"
                  / "com.alexji.sfos.eval.plist").exists()


def test_cli_uninstall_calls_remove(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    _stub_launchctl(monkeypatch)
    # First install so there's something to remove
    main(["install", "--no-load"])
    # Then uninstall
    rc = main(["uninstall"])
    assert rc == 0
    for job in JOBS:
        assert not (pathlib.Path.home() / "Library" / "LaunchAgents"
                      / f"{job.label}.plist").exists()


def test_cli_status_reports_loaded_state(monkeypatch, capsys):
    _stub_launchctl(monkeypatch, returncode=0)
    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    for job in JOBS:
        assert job.label in out
