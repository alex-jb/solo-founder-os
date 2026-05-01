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


def test_jobs_contain_three_canonical_entries():
    labels = {j.label for j in JOBS}
    assert "com.alexji.sfos.eval" in labels
    assert "com.alexji.sfos.evolver" in labels
    assert "com.alexji.sfos.retro" in labels
    assert all(j.weekday == 0 for j in JOBS), "all jobs must run Sunday"


def test_jobs_run_in_dependency_order():
    """eval must precede evolver must precede retro: each downstream job
    needs the upstream's data to be fresh that morning."""
    by_label = {j.label: (j.hour, j.minute) for j in JOBS}
    eval_t = by_label["com.alexji.sfos.eval"]
    evol_t = by_label["com.alexji.sfos.evolver"]
    retro_t = by_label["com.alexji.sfos.retro"]
    assert eval_t < evol_t < retro_t


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
