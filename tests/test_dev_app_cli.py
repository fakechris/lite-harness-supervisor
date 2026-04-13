from __future__ import annotations

from supervisor import dev_app


def test_dev_parser_exposes_only_devtime_commands():
    parser = dev_app.build_dev_parser()

    help_text = parser.format_help()

    assert " eval " in f" {help_text} "
    assert " learn " in f" {help_text} "
    assert " oracle " in f" {help_text} "
    assert " run " not in f" {help_text} "
    assert " daemon " not in f" {help_text} "
