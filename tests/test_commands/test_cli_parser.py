"""Tests for the CLI argument parser (commands.build_parser)."""
from __future__ import annotations

import pytest

from commands import build_parser
from commands.guide import cmd_guide
from commands.deploy import cmd_full_deployment
from commands.ingest import cmd_ingest
from commands.single_item import cmd_single_item
from commands.single_object import cmd_single_object


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
    args = parser.parse_args(["--verbose", "full-deployment"])
    assert args.verbose is True
    assert args.func is cmd_full_deployment


def test_ingest_sets_func(parser):
    args = parser.parse_args(["ingest"])
    assert args.func is cmd_ingest


def test_single_item_with_id(parser):
    args = parser.parse_args(["single-item", "500abc123"])
    assert args.func is cmd_single_item
    assert args.item_id == "500abc123"


def test_single_object_with_type(parser):
    args = parser.parse_args(["single-object", "Case"])
    assert args.func is cmd_single_object
    assert args.object_type == "Case"


def test_verbose_before_subcommand(parser):
    args = parser.parse_args(["--verbose", "ingest"])
    assert args.verbose is True
    assert args.command == "ingest"


def test_unknown_command_raises_system_exit(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["nonexistent-command"])


def test_default_verbose_is_false(parser):
    args = parser.parse_args(["ingest"])
    assert args.verbose is False
