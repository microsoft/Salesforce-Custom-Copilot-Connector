"""Tests for the CLI argument parser (commands.build_parser)."""
from __future__ import annotations

import pytest

from commands import build_parser
from commands.guide import cmd_guide
from commands.deploy import cmd_full_deployment
from commands.ingest import cmd_ingest
from commands.ingest_item import cmd_ingest_item
from commands.ingest_object import cmd_ingest_object


@pytest.fixture
def parser():
    return build_parser()


def test_no_args_command_is_none(parser):
    args = parser.parse_args([])
    assert args.command is None


def test_guide_sets_func(parser):
    args = parser.parse_args(["guide"])
    assert args.func is cmd_guide


def test_full_deployment_sets_func(parser):
    args = parser.parse_args(["full-deployment"])
    assert args.func is cmd_full_deployment


def test_full_deployment_verbose(parser):
    args = parser.parse_args(["full-deployment", "--verbose"])
    assert args.verbose is True
    assert args.func is cmd_full_deployment


def test_ingest_sets_func(parser):
    args = parser.parse_args(["ingest"])
    assert args.func is cmd_ingest


def test_ingest_item_sets_func(parser):
    args = parser.parse_args(["ingest-item", "--id", "500abc123"])
    assert args.func is cmd_ingest_item
    assert args.id == "500abc123"


def test_ingest_item_requires_id(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest-item"])


def test_ingest_object_sets_func(parser):
    args = parser.parse_args(["ingest-object", "--type", "Case"])
    assert args.func is cmd_ingest_object
    assert args.type == "Case"


def test_ingest_object_requires_type(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest-object"])


def test_verbose_before_subcommand(parser):
    """--verbose after subcommand works."""
    args = parser.parse_args(["ingest", "--verbose"])
    assert args.verbose is True
    assert args.command == "ingest"


def test_unknown_command_raises_system_exit(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["nonexistent-command"])


def test_default_verbose_is_false(parser):
    args = parser.parse_args(["ingest"])
    assert args.verbose is False


def test_full_deployment_continuous_defaults(parser):
    args = parser.parse_args(["full-deployment"])
    assert args.continuous is False
    assert args.full_crawl_hours == 24
    assert args.incremental_hours == 4


def test_full_deployment_continuous_with_hours(parser):
    args = parser.parse_args(["full-deployment", "--continuous", "--full-crawl-hours", "48", "--incremental-hours", "2"])
    assert args.continuous is True
    assert args.full_crawl_hours == 48
    assert args.incremental_hours == 2


def test_ingest_continuous_defaults(parser):
    args = parser.parse_args(["ingest"])
    assert args.continuous is False
    assert args.full_crawl_hours == 24
    assert args.incremental_hours == 4


def test_ingest_continuous_with_hours(parser):
    args = parser.parse_args(["ingest", "--continuous", "--full-crawl-hours", "48", "--incremental-hours", "6"])
    assert args.continuous is True
    assert args.full_crawl_hours == 48
    assert args.incremental_hours == 6
