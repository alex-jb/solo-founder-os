"""Tests for scheduler — launchd plist + cron line builders."""
from __future__ import annotations
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.scheduler import (
    build_launchd_plist,
    build_cron_line,
    launch_agent_path,
    _xml_escape,
)


# ─── _xml_escape ─────────────────────────────────────────

def test_xml_escape_handles_ampersand_and_brackets():
    assert _xml_escape("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_xml_escape_handles_quotes():
    assert _xml_escape('say "hi"') == "say &quot;hi&quot;"


# ─── build_launchd_plist ─────────────────────────────────

def test_plist_minimal():
    plist = build_launchd_plist(
        label="com.alex.test",
        program=["/usr/bin/true"],
    )
    assert "<?xml" in plist
    assert "com.alex.test" in plist
    assert "/usr/bin/true" in plist


def test_plist_calendar_schedule():
    plist = build_launchd_plist(
        label="com.alex.daily",
        program=["/bin/echo", "hi"],
        schedule={"hour": 7, "minute": 3},
    )
    assert "StartCalendarInterval" in plist
    assert "<key>Hour</key>" in plist
    assert "<integer>7</integer>" in plist
    assert "<key>Minute</key>" in plist
    assert "<integer>3</integer>" in plist


def test_plist_interval_schedule():
    plist = build_launchd_plist(
        label="com.alex.poller",
        program=["/bin/echo"],
        schedule=420,
    )
    assert "<key>StartInterval</key>" in plist
    assert "<integer>420</integer>" in plist
    assert "StartCalendarInterval" not in plist


def test_plist_no_schedule_omits_block():
    plist = build_launchd_plist(
        label="com.alex.x",
        program=["/bin/echo"],
        run_at_load=True,
    )
    assert "StartInterval" not in plist
    assert "StartCalendarInterval" not in plist
    assert "RunAtLoad" in plist


def test_plist_program_args_each_in_string_tag():
    plist = build_launchd_plist(
        label="com.alex.x",
        program=["/bin/sh", "-c", "echo hi"],
    )
    assert "<string>/bin/sh</string>" in plist
    assert "<string>-c</string>" in plist
    assert "<string>echo hi</string>" in plist


def test_plist_xml_escapes_unsafe_chars_in_program():
    plist = build_launchd_plist(
        label="com.alex.x",
        program=["/bin/sh", "-c", "a & b < c"],
    )
    assert "<string>a &amp; b &lt; c</string>" in plist


def test_plist_stdout_stderr_paths():
    plist = build_launchd_plist(
        label="com.alex.x",
        program=["/bin/echo"],
        stdout_path="/tmp/out.log",
        stderr_path="/tmp/err.log",
    )
    assert "<key>StandardOutPath</key>" in plist
    assert "/tmp/out.log" in plist
    assert "<key>StandardErrorPath</key>" in plist
    assert "/tmp/err.log" in plist


def test_plist_working_dir():
    plist = build_launchd_plist(
        label="com.alex.x",
        program=["/bin/echo"],
        working_dir="/Users/alexji",
    )
    assert "<key>WorkingDirectory</key>" in plist
    assert "/Users/alexji" in plist


def test_plist_keep_alive_true():
    plist = build_launchd_plist(
        label="com.alex.daemon",
        program=["/bin/yes"],
        keep_alive=True,
    )
    assert "<key>KeepAlive</key>" in plist
    assert "<true/>" in plist


def test_plist_requires_label():
    with pytest.raises(ValueError, match="label"):
        build_launchd_plist(label="", program=["/bin/echo"])


def test_plist_requires_program():
    with pytest.raises(ValueError, match="program"):
        build_launchd_plist(label="x", program=[])


def test_plist_complete_example_matches_funnel_pattern():
    """Sanity check: produces something close to what funnel-analytics-agent's
    install-cron.sh writes today."""
    plist = build_launchd_plist(
        label="com.alex.funnel-analytics.brief",
        program=[
            "/Users/alexji/Desktop/funnel-analytics-agent/scripts/_run.sh",
            "--out", "/path/to/brief.md",
        ],
        schedule={"hour": 7, "minute": 3},
        stdout_path="/Users/alexji/.funnel-analytics-agent/brief.log",
        stderr_path="/Users/alexji/.funnel-analytics-agent/brief.err.log",
        working_dir="/Users/alexji",
    )
    # Must be valid plist XML
    assert plist.startswith('<?xml version="1.0"')
    assert plist.rstrip().endswith("</plist>")
    # All five required pieces present
    for required in ("com.alex.funnel-analytics.brief", "_run.sh",
                     "Hour</key>", "brief.log", "brief.err.log"):
        assert required in plist


# ─── build_cron_line ─────────────────────────────────────

def test_cron_line_basic():
    line = build_cron_line(
        schedule="*/7 * * * *",
        command="/usr/bin/poll.sh",
    )
    assert "*/7 * * * * /usr/bin/poll.sh" in line


def test_cron_line_with_comment():
    line = build_cron_line(
        schedule="0 7 * * *",
        command="run-brief",
        comment="Daily 7am brief",
    )
    assert line.startswith("# Daily 7am brief\n")
    assert "0 7 * * * run-brief" in line


def test_cron_line_multiline_comment():
    line = build_cron_line(
        schedule="0 0 * * *",
        command="run",
        comment="Line 1\nLine 2",
    )
    assert "# Line 1" in line
    assert "# Line 2" in line


def test_cron_line_ends_with_newline():
    line = build_cron_line(schedule="* * * * *", command="x")
    assert line.endswith("\n")


# ─── launch_agent_path ───────────────────────────────────

def test_launch_agent_path_default(tmp_path):
    p = launch_agent_path("com.alex.foo", home=tmp_path)
    assert p == tmp_path / "Library" / "LaunchAgents" / "com.alex.foo.plist"


def test_launch_agent_path_uses_home_by_default():
    p = launch_agent_path("x")
    assert "Library/LaunchAgents/x.plist" in str(p)
