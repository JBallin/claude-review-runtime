"""Trusted reusable-workflow settings, captured selection, and informational presentation."""

import copy
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from test_claude_review_check import BASE_TIP, HEAD, OTHER, REPO, check, presentation_owner


class RepositoryConfigurationTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def select(self, environment, metadata=None):
        """Execute the actual argument boundary with deterministic local inputs."""
        with tempfile.TemporaryDirectory() as tmp:
            path = None
            if metadata is not None:
                path = Path(tmp) / "review.json"
                path.write_text(metadata if isinstance(metadata, str) else json.dumps(metadata))
            output = io.StringIO()
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(check, "write_output") as write, redirect_stdout(output):
                result = check.cmd_selection(str(path) if path else None)
            return result, {call.args[0]: call.args[1] for call in write.call_args_list}, output.getvalue()

    def record(self, config, *, owner=None, model=None, auxiliary=None):
        owner = owner or presentation_owner()
        environment = {"SELECTION_CONFIG": json.dumps(config), "GITHUB_RUN_ATTEMPT": "2"}
        with mock.patch.dict(os.environ, environment, clear=True), tempfile.TemporaryDirectory() as tmp:
            execution = None
            if model:
                usage = {model: {"inputTokens": 1}}
                if auxiliary:
                    usage[auxiliary] = {"inputTokens": 1}
                stream = [
                    {"type": "system", "subtype": "init", "session_id": "session", "model": model},
                    {"type": "assistant", "session_id": "session", "message": {"model": model}},
                    {"type": "result", "session_id": "session", "modelUsage": usage},
                ]
                execution = Path(tmp) / "execution.json"
                execution.write_text(json.dumps(stream))
            return check.selection_record(owner, str(execution) if execution else None)

    def completed_comment(self, owner, record):
        with mock.patch.dict(os.environ, {
                "BASE_SHA": owner["base"], "DETAILS_URL": f"https://github.com/{REPO}/actions/runs/{owner['run']}"}, clear=True):
            body = check.status_comment_body(owner["head"], owner["base_ref"], "success", owner=owner,
                                           selection=record, trigger_label="PR opened for review",
                                           completed_at="2026-10-30T17:00:00Z")
        return {"id": 55, "user": {"login": "github-actions[bot]", "type": "Bot"}, "body": body}

    def test_absent_defaults_are_explicit_runtime_execution_arguments(self):
        for environment in ({"TRIGGER_KIND": "automatic"}, {"TRIGGER_COMMENT": "/claude-review"}):
            with self.subTest(environment=environment):
                result, output, _ = self.select(environment)
                self.assertEqual(result, 0)
                self.assertEqual((output["model_args"], output["effort_args"]), ("--model sonnet", "--effort medium"))
                config = json.loads(output["selection_config"])
                self.assertEqual((config["model_source"], config["effort_source"]), ("runtime", "runtime"))
                self.assertEqual(config["errors"], [])

    def test_legacy_actions_variable_environments_do_not_supply_execution_settings(self):
        legacy = {"REPOSITORY_REVIEW_MODEL": "invalid", "REPOSITORY_REVIEW_EFFORT": "invalid"}
        for environment, expected, sources in (
                ({"TRIGGER_KIND": "automatic", **legacy}, ("sonnet", "medium"), ("runtime", "runtime")),
                ({"TRIGGER_COMMENT": "/claude-review high", **legacy}, ("sonnet", "high"), ("runtime", "manual")),
                ({"TRIGGER_KIND": "automatic", **legacy, "WORKFLOW_REVIEW_MODEL": "haiku", "WORKFLOW_REVIEW_EFFORT": "low"},
                 ("haiku", "low"), ("workflow", "workflow"))):
            with self.subTest(environment=environment):
                result, output, message = self.select(environment)
                self.assertEqual(result, 0)
                config = json.loads(output["selection_config"])
                self.assertEqual((config["requested_model"], config["requested_effort"]), expected)
                self.assertEqual((config["model_source"], config["effort_source"]), sources)
                self.assertEqual(config["diagnostics"], [])
                self.assertNotIn("::warning::", message)
                self.assertEqual((output["model_args"], output["effort_args"]),
                                 ("--model " + expected[0], "--effort " + expected[1]))

    def test_manual_override_resolves_only_its_own_field(self):
        cases = (
            ("/claude-review", "sonnet", "medium", "workflow", "workflow"),
            ("/claude-review opus", "opus", "medium", "manual", "workflow"),
            ("/claude-review high", "sonnet", "high", "workflow", "manual"),
            ("/claude-review haiku low", "haiku", "low", "manual", "manual"),
        )
        for body, model, effort, model_source, effort_source in cases:
            with self.subTest(body=body):
                config = check.resolve_configuration(body, "sonnet", "medium")
                self.assertEqual((config["requested_model"], config["requested_effort"]), (model, effort))
                self.assertEqual((config["model_source"], config["effort_source"]), (model_source, effort_source))
                self.assertEqual(config["errors"], [])

    def test_aliases_and_effort_levels_reach_only_literal_arguments(self):
        for model in ("opus", "sonnet", "haiku"):
            for effort in ("low", "medium", "high", "xhigh", "max"):
                with self.subTest(model=model, effort=effort):
                    result, output, _ = self.select({"TRIGGER_KIND": "automatic",
                        "WORKFLOW_REVIEW_MODEL": model, "WORKFLOW_REVIEW_EFFORT": effort})
                    self.assertEqual(result, 0)
                    self.assertEqual(output["model_args"], "--model " + model)
                    self.assertEqual(output["effort_args"], "--effort " + effort)
                    command = f"/claude-review {model} {effort}"
                    self.assertEqual(check.manual_selection(command.upper()),
                                     {"requested_model": model, "requested_effort": effort})

    def test_workflow_normalization_does_not_relax_manual_grammar(self):
        config = check.resolve_configuration("/claude-review", "  SoNnEt\n", "\tHIGH ")
        self.assertEqual((config["requested_model"], config["requested_effort"]), ("sonnet", "high"))
        blank = check.resolve_configuration("/claude-review", " \n", "\t")
        self.assertEqual((blank["model_source"], blank["effort_source"]), ("runtime", "runtime"))
        for body in (" /claude-review sonnet", "/claude-review\tsonnet", "/claude-review haiku\n"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                check.manual_selection(body)

    def test_invalid_defaults_fall_back_independently_and_unused_defaults_are_shadowed(self):
        for model, effort, errors, expected in (
                ("claude-opus-5-5", "high", ["model"], ("sonnet", "high")),
                ("haiku", "ultra", ["effort"], ("haiku", "medium")),
                ("opus[1m]", "--effort high", ["model", "effort"], ("sonnet", "medium"))):
            with self.subTest(model=model, effort=effort):
                result, output, message = self.select({"TRIGGER_KIND": "automatic",
                    "WORKFLOW_REVIEW_MODEL": model, "WORKFLOW_REVIEW_EFFORT": effort})
                self.assertEqual(result, 0)
                config = json.loads(output["selection_config"])
                self.assertEqual(config["errors"], errors)
                self.assertEqual((config["requested_model"], config["requested_effort"]), expected)
                self.assertEqual(check.valid_configuration(config), config)
                self.assertEqual(config["diagnostics"], [
                    {"field": field, "source": "workflow", "reason": "invalid"} for field in errors])
                self.assertIn("::warning::", message)
                self.assertEqual((output["model_args"], output["effort_args"]),
                                 ("--model " + expected[0], "--effort " + expected[1]))
        config = check.resolve_configuration("/claude-review haiku max", "invalid", "invalid")
        self.assertEqual(config["errors"], [])
        self.assertEqual(config["diagnostics"], [])
        self.assertEqual((config["requested_model"], config["requested_effort"]), ("haiku", "max"))

    def test_invalid_manual_fields_fall_back_without_losing_valid_sibling(self):
        cases = (
            ("/claude-review invalid high", "haiku", "low", ("haiku", "high"), ("workflow", "manual"), ["model"]),
            ("/claude-review opus invalid", "haiku", "low", ("opus", "low"), ("manual", "workflow"), ["effort"]),
            ("/claude-review invalid invalid", "haiku", "low", ("haiku", "low"), ("workflow", "workflow"), ["model", "effort"]),
            ("/claude-review invalid invalid", "invalid", "invalid", ("sonnet", "medium"), ("runtime", "runtime"), ["model", "effort"]),
        )
        for body, model, effort, expected, sources, errors in cases:
            with self.subTest(body=body, model=model, effort=effort):
                result, output, message = self.select({"TRIGGER_COMMENT": body,
                    "WORKFLOW_REVIEW_MODEL": model, "WORKFLOW_REVIEW_EFFORT": effort})
                self.assertEqual(result, 0)
                config = json.loads(output["selection_config"])
                self.assertEqual((config["requested_model"], config["requested_effort"]), expected)
                self.assertEqual((config["model_source"], config["effort_source"]), sources)
                self.assertEqual(config["errors"], errors)
                self.assertEqual(check.valid_configuration(config), config)
                self.assertIn("::warning::", message)
                owner = presentation_owner(kind="issue_comment", target=12)
                record = self.record(config, owner=owner, model="claude-" + expected[0] + "-5-5")
                notice = " ".join(check.selection_notice(record))
                for field in errors:
                    self.assertIn(field, notice.lower())
                for field, fallback in zip(("model", "effort"), expected):
                    if field in errors:
                        self.assertIn(fallback.capitalize(), notice)
                self.assertEqual(check.selection_details(record), [
                    "**Model:** " + expected[0].title() + " · **Effort:** " + expected[1].capitalize()])

    def test_unknown_single_value_warns_at_request_level_and_resolves_both_fields(self):
        for model, effort, expected, errors in (
                ("haiku", "low", ("haiku", "low"), ["request"]),
                ("", "", ("sonnet", "medium"), ["request"]),
                ("invalid", "invalid", ("sonnet", "medium"), ["model", "effort", "request"])):
            with self.subTest(model=model, effort=effort):
                result, output, message = self.select({"TRIGGER_COMMENT": "/claude-review unknown",
                    "WORKFLOW_REVIEW_MODEL": model, "WORKFLOW_REVIEW_EFFORT": effort})
                self.assertEqual(result, 0)
                config = json.loads(output["selection_config"])
                self.assertEqual((config["requested_model"], config["requested_effort"]), expected)
                self.assertEqual(config["errors"], errors)
                self.assertIn({"field": "request", "source": "manual", "reason": "invalid"}, config["diagnostics"])
                self.assertEqual(check.valid_configuration(config), config)
                self.assertIn("**Usage:** `/claude-review", message)
                self.assertEqual((output["model_args"], output["effort_args"]),
                                 ("--model " + expected[0], "--effort " + expected[1]))

    def test_invalid_setting_content_is_not_copied_into_arguments_or_errors(self):
        hostile = "opus\n--tools Bash; $(id) <script>"
        result, output, message = self.select({"TRIGGER_KIND": "automatic",
                                              "WORKFLOW_REVIEW_MODEL": hostile,
                                              "WORKFLOW_REVIEW_EFFORT": "high"})
        self.assertEqual(result, 0)
        self.assertEqual(output["model_args"], "--model sonnet")
        self.assertEqual(output["effort_args"], "--effort high")
        self.assertNotIn(hostile, message + json.dumps(output))
        self.assertNotIn("$(id)", message + json.dumps(output))

    def test_hostile_manual_option_values_are_diagnostic_data_not_arguments(self):
        body = "/claude-review $(id) high"
        result, output, message = self.select({"TRIGGER_COMMENT": body})
        self.assertEqual(result, 0)
        self.assertEqual((output["model_args"], output["effort_args"]), ("--model sonnet", "--effort high"))
        self.assertNotIn("$(id)", message + json.dumps(output))
        config = json.loads(output["selection_config"])
        self.assertEqual(config["diagnostics"], [{"field": "model", "source": "manual", "reason": "invalid"}])

    def test_configuration_schema_rejects_ambiguous_or_inconsistent_fields(self):
        original = check.resolve_configuration("/claude-review", "opus", "high")
        changes = ({"version": True}, {"version": 3}, {"extra": "value"}, {"errors": ["model", "model"]},
                   {"errors": ["effort", "model"]}, {"errors": ["other"]}, {"errors": "model"},
                   {"requested_model": "default"}, {"model_source": "pr"}, {"model_source": "inherited"},
                   {"requested_effort": ""}, {"errors": ["effort"]},
                   {"diagnostics": [{"field": "model", "source": "pr", "reason": "invalid"}]},
                   {"diagnostics": [{"field": "model", "source": "manual", "reason": "other"}]},
                   {"diagnostics": [{"field": "model", "source": "workflow", "reason": "missing"}], "errors": ["model"]},
                   {"diagnostics": [{"field": "model", "source": "manual", "reason": "invalid", "raw": "unsafe"}]},
                   {"diagnostics": [{"field": "request", "source": "workflow", "reason": "invalid"}], "errors": ["request"]},
                   {"diagnostics": [{"field": "request", "source": "manual", "reason": "missing"}], "errors": ["request"]})
        for change in changes:
            with self.subTest(change=change):
                self.assertIsNone(check.valid_configuration({**original, **change}))

    def test_model_only_and_effort_only_resolve_the_other_runtime_field(self):
        for values, expected in (({"WORKFLOW_REVIEW_MODEL": "haiku"}, ("--model haiku", "--effort medium")),
                                 ({"WORKFLOW_REVIEW_EFFORT": "max"}, ("--model sonnet", "--effort max"))):
            result, output, _ = self.select({"TRIGGER_KIND": "automatic", **values})
            self.assertEqual(result, 0)
            self.assertEqual((output["model_args"], output["effort_args"]), expected)

    def test_review_uses_captured_configuration_after_workflow_inputs_change(self):
        for body, kind in (("/claude-review high", "issue_comment"), (None, "automatic")):
            config = check.resolve_configuration(body, "sonnet", "medium", automatic=kind == "automatic")
            captured = {"selection_config": config, "trigger_comment": body}
            environment = {"TRIGGER_KIND": kind, "WORKFLOW_REVIEW_MODEL": "haiku",
                           "WORKFLOW_REVIEW_EFFORT": "low"}
            if body:
                environment["TRIGGER_COMMENT"] = body
            with self.subTest(kind=kind):
                result, output, _ = self.select(environment, captured)
                self.assertEqual(result, 0)
                self.assertEqual(json.loads(output["selection_config"]), config)
                self.assertEqual(output["model_args"], "--model sonnet")
                self.assertEqual(output["effort_args"], "--effort " + config["requested_effort"])

    def test_fallback_diagnostics_and_effective_fields_are_frozen_across_capture(self):
        body = "/claude-review invalid"
        config = check.resolve_configuration(body, "haiku", "low")
        captured = {"trigger_comment": body, "selection_config": config,
                    "requested_model": "haiku", "requested_effort": "low"}
        result, output, message = self.select({"TRIGGER_KIND": "issue_comment", "TRIGGER_COMMENT": body,
            "WORKFLOW_REVIEW_MODEL": "opus", "WORKFLOW_REVIEW_EFFORT": "max"}, captured)
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output["selection_config"]), config)
        self.assertEqual((output["model_args"], output["effort_args"]), ("--model haiku", "--effort low"))
        self.assertIn("::warning::", message)
        owner = presentation_owner(kind="issue_comment", target=12)
        record = self.record(config, owner=owner, model="claude-haiku-5-5")
        before = copy.deepcopy(record)
        for renderer in (
                lambda: check.status_comment_body(HEAD, "main", "success", owner=owner, selection=record),
                lambda: check.completion_comment_body(REPO, HEAD, "main", BASE_TIP, "", "", selection=record)):
            rendered = renderer()
            main, details = rendered.split("<details>", 1)
            self.assertIn("request", main.lower())
            self.assertIn("using Model: Haiku (configured default), Effort: Low (configured default)", main)
            self.assertIn("**Usage:** `/claude-review [", main)
            self.assertIn("**Model:** Haiku · **Effort:** Low", details)
        self.assertEqual(record, before)

    def test_notice_sources_and_usage_are_independent_for_each_diagnostic(self):
        config = check.resolve_configuration("/claude-review invalid invalid", "invalid", "low")
        original = copy.deepcopy(config)
        notices = check.configuration_notices(config)
        self.assertEqual(notices, [
            "⚠️ **Review request:** Invalid model; using Sonnet (built-in default).",
            "⚠️ **Review configuration:** Invalid configured model; using Sonnet (built-in default).",
            "⚠️ **Review request:** Invalid effort; using Low (configured default).",
            check.MANUAL_USAGE,
        ])
        for body, model, effort in (("/claude-review invalid high", "haiku", "low"),
                                   ("/claude-review haiku invalid", "sonnet", "medium"),
                                   ("/claude-review invalid", "haiku", "low"),
                                   ("/claude-review opus --effort", "haiku", "low")):
            with self.subTest(body=body):
                resolved = check.resolve_configuration(body, model, effort)
                messages = check.configuration_notices(resolved)
                self.assertEqual(messages.count(check.MANUAL_USAGE), 1)
                self.assertEqual(messages[-1], check.MANUAL_USAGE)
                self.assertTrue(all("**Review request:**" in line for line in messages[:-1]))
                self.assertNotRegex(" ".join(messages), r"\((?:Workflow|Runtime|Manual)\)")
        automatic = check.resolve_configuration(None, "invalid", "high", automatic=True)
        self.assertEqual(check.configuration_notices(automatic), [
            "⚠️ **Review configuration:** Invalid configured model; using Sonnet (built-in default)."])
        retained = check.configuration_notices(check.resolve_configuration("/claude-review opus --effort", "haiku", "low"))
        self.assertIn("Model: Opus (from command), Effort: Low (configured default)", retained[0])
        self.assertEqual(config, original)

    def test_usage_is_one_separate_line_after_request_and_model_warnings(self):
        owner = presentation_owner(kind="issue_comment", target=12)
        config = check.resolve_configuration("/claude-review opus invalid", "sonnet", "medium")
        record = self.record(config, owner=owner, model="claude-sonnet-5-5")
        original = copy.deepcopy(record)
        for render in (
                lambda: check.status_comment_body(HEAD, "main", "success", owner=owner, selection=record),
                lambda: check.completion_comment_body(REPO, HEAD, "main", BASE_TIP, "", "", selection=record)):
            visible = render().split("<details>", 1)[0]
            self.assertEqual(visible.count("**Usage:**"), 1)
            self.assertIn("  \n" + check.MANUAL_USAGE, visible)
            self.assertLess(visible.index("⚠️ **Review request:**"), visible.index("**Usage:**"))
            self.assertLess(visible.index("⚠️ **Model:**"), visible.index("**Usage:**"))
            self.assertIn("[haiku|sonnet|opus] [low|medium|high|xhigh|max]", visible)
        self.assertEqual(record, original)

    def test_unsupported_syntax_capture_continues_with_literal_resolved_settings(self):
        for body, model, effort in (
                ("/claude-review effort high", "haiku", "low"),
                ("/claude-review effort", "haiku", "low"),
                ("/claude-review opus effort", "opus", "low"),
                ("/claude-review --model", "haiku", "low"),
                ("/claude-review --model=", "haiku", "low"),
                ("/claude-review opus --effort", "opus", "low"),
                ("/claude-review opus high extra", "opus", "high"),
                ("/claude-review high --model", "haiku", "high"),
                ("/claude-review invalid high --effort", "haiku", "high"),
                ("/claude-review invalid high extra", "haiku", "high")):
            with self.subTest(body=body):
                config = check.resolve_configuration(body, "haiku", "low")
                captured = {"selection_config": config, "trigger_comment": body}
                environment = {"TRIGGER_KIND": "issue_comment", "TRIGGER_COMMENT": body,
                    "WORKFLOW_REVIEW_MODEL": "haiku", "WORKFLOW_REVIEW_EFFORT": "low"}
                for metadata in (None, captured):
                    result, output, message = self.select(environment, metadata)
                    self.assertEqual(result, 0)
                    self.assertEqual((output["model_args"], output["effort_args"]),
                                     ("--model " + model, "--effort " + effort))
                    self.assertEqual(json.loads(output["selection_config"]), config)
                    self.assertIn("::warning::", message)
                    self.assertIn("Unsupported syntax", message)
                    self.assertIn(model.capitalize(), message)
                    self.assertIn(effort.capitalize(), message)
                    self.assertIn("**Usage:** `/claude-review", message)
                changed = {**config, "diagnostics": [], "errors": []}
                self.assertEqual(check.valid_configuration(changed), changed)
                result, _, message = self.select(environment,
                    {"selection_config": changed, "trigger_comment": body})
                self.assertEqual(result, 1)
                self.assertIn("diagnostics disagree", message)
                # Configuration recovery never overrides the ambient model guard.
                result, _, message = self.select({**environment, "ANTHROPIC_MODEL": "custom"}, captured)
                self.assertEqual(result, 1)
                self.assertIn("conflicts with the selected model", message)

    def test_captured_manual_fields_must_match_the_accepted_command(self):
        body = "/claude-review opus high"
        correct = check.resolve_configuration(body, "sonnet", "medium")
        cases = (
            {**correct, "requested_model": "sonnet"},
            {**correct, "model_source": "workflow"},
            {**correct, "effort_source": "workflow"},
        )
        for config in cases:
            with self.subTest(config=config):
                result, _, message = self.select({"TRIGGER_KIND": "issue_comment", "TRIGGER_COMMENT": body},
                                                {"trigger_comment": body, "selection_config": config})
                self.assertEqual(result, 1)
                self.assertIn("disagrees with the accepted request", message)
        result, _, _ = self.select({"TRIGGER_COMMENT": body},
                                  {"trigger_comment": "/claude-review haiku high", "selection_config": correct})
        self.assertEqual(result, 1)

    def test_automatic_capture_cannot_claim_a_manual_override(self):
        manual = check.resolve_configuration("/claude-review opus", "", "high")
        result, _, message = self.select({"TRIGGER_KIND": "automatic"}, {"selection_config": manual})
        self.assertEqual(result, 1)
        self.assertIn("manual override", message)

    def test_automatic_capture_cannot_claim_manual_diagnostics(self):
        config = check.resolve_configuration("/claude-review invalid", "haiku", "low")
        self.assertEqual((config["model_source"], config["effort_source"]), ("workflow", "workflow"))
        result, _, message = self.select({"TRIGGER_KIND": "automatic"}, {"selection_config": config})
        self.assertEqual(result, 1)
        self.assertIn("manual override", message)

    def test_legacy_snapshot_does_not_admit_new_malformed_requests_without_diagnostics(self):
        body = "/claude-review invalid high"
        legacy = {"trigger_comment": body, "requested_model": "", "requested_effort": "high"}
        result, _, message = self.select({"TRIGGER_KIND": "issue_comment", "TRIGGER_COMMENT": body}, legacy)
        self.assertEqual(result, 1)
        self.assertIn("legacy snapshot", message)
        # Old keyword syntax is readable in historical receipts, not admitted
        # as a current execution request merely because metadata is legacy.
        legacy["trigger_comment"] = body = "/claude-review effort high"
        result, _, _ = self.select({"TRIGGER_KIND": "issue_comment", "TRIGGER_COMMENT": body}, legacy)
        self.assertEqual(result, 1)
        # The overlapping original short forms keep their old omitted fields.
        legacy = {"trigger_comment": "/claude-review opus", "requested_model": "opus", "requested_effort": ""}
        result, output, _ = self.select({"TRIGGER_KIND": "issue_comment", "TRIGGER_COMMENT": "/claude-review opus"}, legacy)
        self.assertEqual(result, 0)
        self.assertEqual((output["model_args"], output["effort_args"]), ("--model opus", ""))
        self.assertEqual(json.loads(output["selection_config"])["version"], 1)

    def test_captured_manual_diagnostics_cannot_be_erased_or_changed(self):
        body = "/claude-review invalid"
        config = check.resolve_configuration(body, "haiku", "low")
        for diagnostics in ([], [{"field": "model", "source": "manual", "reason": "invalid"}]):
            changed = {**config, "diagnostics": diagnostics, "errors": ["model"] if diagnostics else []}
            self.assertEqual(check.valid_configuration(changed), changed)
            result, _, message = self.select({"TRIGGER_KIND": "issue_comment", "TRIGGER_COMMENT": body},
                {"selection_config": changed, "trigger_comment": body})
            self.assertEqual(result, 1)
            self.assertIn("diagnostics disagree", message)

    def test_duplicate_json_keys_are_rejected_at_capture_boundaries(self):
        config = check.resolve_configuration(None, "opus", "high", automatic=True)
        serialized = json.dumps(config)
        duplicate = serialized.replace('"version": 2', '"version": 2, "version": 2')
        result, _, _ = self.select({"TRIGGER_KIND": "automatic", "SELECTION_CONFIG": duplicate})
        self.assertEqual(result, 1)
        result, _, _ = self.select({"TRIGGER_KIND": "automatic"}, '{"selection_config":' + duplicate + '}')
        self.assertEqual(result, 1)
        for malformed in ('null', '[]', '"not a snapshot"'):
            result, _, _ = self.select({"TRIGGER_KIND": "automatic"}, malformed)
            self.assertEqual(result, 1)

    def test_job_outputs_and_captured_metadata_must_describe_the_same_selection(self):
        config = check.resolve_configuration(None, "opus", "high", automatic=True)
        different = check.resolve_configuration(None, "haiku", "low", automatic=True)
        for environment, captured in (
                ({"TRIGGER_KIND": "automatic", "SELECTION_CONFIG": json.dumps(different)},
                 {"selection_config": config}),
                ({"TRIGGER_KIND": "automatic"},
                 {"selection_config": config, "requested_model": "sonnet"})):
            result, _, message = self.select(environment, captured)
            self.assertEqual(result, 1)
            self.assertIn("disagrees across job inputs", message)

    def test_ambient_conflicts_are_checked_for_every_selected_alias_and_effort(self):
        for model in ("opus", "sonnet", "haiku"):
            config = check.resolve_configuration(None, model, "high", automatic=True)
            captured = {"selection_config": config}
            for environment, expected in (({"ANTHROPIC_MODEL": model}, 0),
                                           ({"ANTHROPIC_MODEL": "claude-" + model + "-5-5"}, 1),
                                           ({"CLAUDE_CODE_EFFORT_LEVEL": "high"}, 0),
                                           ({"CLAUDE_CODE_EFFORT_LEVEL": "low"}, 1)):
                with self.subTest(model=model, environment=environment):
                    result, _, _ = self.select({"TRIGGER_KIND": "automatic", **environment}, captured)
                    self.assertEqual(result, expected)
        fallback = check.resolve_configuration(None, automatic=True)
        result, output, _ = self.select({"TRIGGER_KIND": "automatic", "ANTHROPIC_MODEL": "custom-model",
                                        "CLAUDE_CODE_EFFORT_LEVEL": "custom-level"}, {"selection_config": fallback})
        self.assertEqual(result, 1)

    def test_automatic_evidence_displays_resolved_settings_without_version_or_observed_effort(self):
        config = check.resolve_configuration(None, automatic=True)
        owner = presentation_owner()
        unknown = self.record(config, owner=owner)
        self.assertEqual(check.selection_details(unknown), ["**Model:** Sonnet · **Effort:** Medium"])
        self.assertEqual(check.selection_notice(unknown, running=True), [])
        self.assertIn("primary model evidence missing", " ".join(check.selection_notice(unknown)))
        observed = self.record(config, owner=owner, model="claude-sonnet-5-5", auxiliary="claude-haiku-5-5")
        self.assertEqual(check.selection_details(observed), ["**Model:** Sonnet · **Effort:** Medium"])
        self.assertEqual(check.selection_notice(observed), [])
        self.assertEqual(observed["models"], ["claude-haiku-5-5", "claude-sonnet-5-5"])
        self.assertIsNone(observed["observed_effort"])
        body = check.status_comment_body(HEAD, "main", "success", owner=owner, selection=observed)
        main, details = body.split("<details>", 1)
        self.assertNotIn("**Model:**", main)
        self.assertIn("**Model:** Sonnet · **Effort:** Medium", details)
        self.assertNotIn("Sonnet 5.5", details)
        self.assertNotIn("Observed effort", body)

    def test_automatic_and_plain_manual_settings_lead_details_before_baseline_and_runtime(self):
        environment = {"RUNTIME_REPOSITORY": "JBallin/claude-review-runtime", "RUNTIME_SHA": "f" * 40,
                       "RUNTIME_WORKFLOW_PATH": ".github/workflows/claude.yml"}
        for body, kind in ((None, "automatic"), ("/claude-review", "issue_comment")):
            owner = presentation_owner() if kind == "automatic" else presentation_owner(kind=kind, target=12)
            config = check.resolve_configuration(body, automatic=kind == "automatic")
            record = self.record(config, owner=owner, model="claude-sonnet-5-5")
            original = copy.deepcopy(record)
            for state in ("in_progress", "success"):
                with self.subTest(kind=kind, state=state), mock.patch.dict(os.environ, environment, clear=True):
                    rendered = check.status_comment_body(HEAD, "main", state, owner=owner, selection=record,
                        runtime=check.executing_runtime(), base_sha=BASE_TIP)
                    details = rendered.split("</summary>", 1)[1].strip()
                    settings = "**Model:** Sonnet · **Effort:** Medium"
                    self.assertEqual(details.splitlines()[0].rstrip(), settings)
                    self.assertNotIn("<br", details)
                    self.assertLess(details.index(settings), details.index("baseline"))
                    self.assertLess(details.index(settings), details.index("**Runtime used:**"))
                    self.assertEqual(check.current_selection(owner, {"body": rendered}), original)
            self.assertEqual(record, original)

    def test_conflicting_inherited_model_evidence_stays_quiet_without_losing_effort(self):
        for body, default_effort, effort in (
                (None, "", ""), (None, "medium", "medium"),
                ("/claude-review", "", ""), ("/claude-review", "medium", "medium"),
                ("/claude-review high", "", "high"),
                ("/claude-review high", "medium", "high")):
            with self.subTest(body=body, default_effort=default_effort):
                automatic = body is None
                owner = presentation_owner() if automatic else presentation_owner(kind="issue_comment", target=12)
                config = {"version": 1, "requested_model": "", "requested_effort": effort,
                          "model_source": "inherited", "effort_source": "manual" if body and body != "/claude-review" else
                          "repository" if default_effort else "inherited", "errors": []}
                record = self.record(config, owner=owner, model="claude-opus-5-5")
                models = ["claude-opus-5-5", "claude-sonnet-5-5"]
                record["models"] = models
                record["primary_model_evidence"]["assistant"] = models
                original = copy.deepcopy(record)
                self.assertEqual(check.valid_selection(record, owner), record)
                self.assertEqual(check.primary_selection_models(record), models)
                for running in (False, True):
                    self.assertEqual(check.selection_notice(record, running=running), [])
                self.assertEqual(check.selection_details(record),
                                 ["**Effort:** " + effort.capitalize()] if effort else [])
                for state in ("in_progress", "success"):
                    rendered = check.status_comment_body(HEAD, "main", state, owner=owner, selection=record)
                    main, details = rendered.split("<details>", 1)
                    self.assertNotIn("**Model:**", main + details)
                    self.assertNotIn("primary model evidence ambiguous", main)
                    self.assertNotIn("**Effort:**", main)
                    if effort:
                        self.assertIn("**Effort:** " + effort.capitalize(), details)
                    else:
                        self.assertNotIn("**Effort:**", details)
                    self.assertEqual(check.current_selection(owner, {"body": rendered}), original)
                if not automatic:
                    receipt = check.completion_comment_body(REPO, HEAD, "main", BASE_TIP, "", "", selection=record)
                    self.assertNotIn("**Model:**", receipt)
                    self.assertNotIn("primary model evidence ambiguous", receipt)
                    if effort:
                        self.assertIn("**Effort:** " + effort.capitalize(), receipt.split("<details>", 1)[1])
                self.assertEqual(record, original)

    def test_workflow_and_manual_model_selections_still_warn_on_primary_conflict(self):
        for body, default_model in ((None, "sonnet"), ("/claude-review", "sonnet"),
                                    ("/claude-review opus", "")):
            with self.subTest(body=body, default_model=default_model):
                automatic = body is None
                owner = presentation_owner() if automatic else presentation_owner(kind="issue_comment", target=12)
                config = check.resolve_configuration(body, default_model, "high", automatic=automatic)
                record = self.record(config, owner=owner, model="claude-opus-5-5")
                record["models"] = ["claude-opus-5-5", "claude-sonnet-5-5"]
                record["primary_model_evidence"]["assistant"] = list(record["models"])
                warning = config["requested_model"].title() + " selected; primary model evidence ambiguous"
                for running in (False, True):
                    self.assertIn(warning, check.selection_notice(record, running=running)[0])
                rendered = check.status_comment_body(HEAD, "main", "success", owner=owner, selection=record)
                self.assertIn(warning, rendered.split("<details>", 1)[0])
                self.assertIn("**Completed**", rendered)

    def test_alias_version_advancement_is_valid_but_primary_mismatch_stays_visible(self):
        config = check.resolve_configuration(None, "haiku", "max", automatic=True)
        owner = presentation_owner()
        observed = self.record(config, owner=owner, model="claude-haiku-7-1")
        self.assertEqual(check.selection_notice(observed), [])
        self.assertEqual(check.selection_details(observed), ["**Model:** Haiku · **Effort:** Max"])
        mismatch = self.record(config, owner=owner, model="claude-sonnet-5-5")
        body = check.status_comment_body(HEAD, "main", "success", owner=owner, selection=mismatch)
        main, details = body.split("<details>", 1)
        self.assertIn("Haiku selected; primary model mismatch (Sonnet observed)", main)
        self.assertIn("**Model:** Haiku · **Effort:** Max", details)

    def test_record_keeps_captured_source_and_validates_generation_and_attempt(self):
        config = check.resolve_configuration("/claude-review high", "sonnet", "low")
        owner = presentation_owner(kind="issue_comment", target=12)
        record = self.record(config, owner=owner, model="claude-sonnet-5-5")
        with mock.patch.dict(os.environ, {"SELECTION_CONFIG": json.dumps(config),
                                          "WORKFLOW_REVIEW_MODEL": "haiku", "WORKFLOW_REVIEW_EFFORT": "max"}):
            fresh = check.current_selection(owner)
        self.assertEqual(fresh["configuration"], config)
        self.assertEqual((fresh["requested_model"], fresh["requested_effort"]), ("sonnet", "high"))
        self.assertEqual(check.valid_selection(record, owner), record)
        self.assertIsNone(check.valid_selection(record, {**owner, "generation": "f" * 64}))
        wrong_attempt = copy.deepcopy(record)
        wrong_attempt["primary_model_evidence"]["attempt"] = 3
        self.assertIsNone(check.valid_selection(wrong_attempt, owner))
        wrong_config = copy.deepcopy(record)
        wrong_config["configuration"]["requested_model"] = "haiku"
        self.assertIsNone(check.valid_selection(wrong_config, owner))

    def test_previous_completed_settings_remain_bound_to_the_completed_generation(self):
        previous_owner = presentation_owner()
        previous = self.record(check.resolve_configuration(None, "opus", "high", automatic=True),
                               owner=previous_owner, model="claude-opus-5-5")
        comment = self.completed_comment(previous_owner, previous)
        historical = check.last_completed_review_details(comment)
        self.assertEqual(historical["selection"], previous)
        current_owner = presentation_owner(head=OTHER, base="e" * 40, run=6, started=3)
        current = self.record(check.resolve_configuration(None, "haiku", "low", automatic=True),
                              owner=current_owner)
        body = check.status_comment_body(OTHER, "main", "in_progress", owner=current_owner, selection=current,
            last_review=(HEAD, "main", "success"), last_review_details=historical, trigger_label="Draft marked ready")
        current_section, previous_section = body.split("**Current attempt**", 1)[1].split("**Previous completed review**", 1)
        self.assertIn("**Model:** Haiku · **Effort:** Low", current_section)
        self.assertNotIn("Opus", current_section)
        self.assertIn("**Model:** Opus · **Effort:** High", previous_section)
        self.assertNotIn("Haiku", previous_section)
        self.assertEqual(current_section.strip().splitlines()[0].rstrip(), "**Model:** Haiku · **Effort:** Low")
        self.assertEqual(previous_section.strip().splitlines()[0].rstrip(), "**Model:** Opus · **Effort:** High")
        self.assertLess(current_section.index("**Model:**"), current_section.index("baseline"))
        self.assertLess(previous_section.index("**Model:**"), previous_section.index("against"))
        without_settings = copy.deepcopy(historical)
        without_settings.pop("selection")
        missing = check.status_comment_body(OTHER, "main", "in_progress", owner=current_owner, selection=current,
            last_review=(HEAD, "main", "success"), last_review_details=without_settings, trigger_label="Draft marked ready")
        missing_previous = missing.split("**Previous completed review**", 1)[1]
        self.assertNotIn("**Model:**", missing_previous)
        self.assertNotIn("**Effort:**", missing_previous)
        self.assertEqual(historical["selection"], previous)
        replay = {**comment, "body": body}
        self.assertEqual(check.last_completed_review_details(replay)["selection"], previous)

    def test_previous_model_mismatch_warns_only_in_its_own_captured_group(self):
        previous_owner = presentation_owner()
        previous = self.record(check.resolve_configuration(None, "opus", "high", automatic=True),
                               owner=previous_owner, model="claude-sonnet-5-5")
        original = copy.deepcopy(previous)
        comment = self.completed_comment(previous_owner, previous)
        historical = check.last_completed_review_details(comment)
        current_owner = presentation_owner(head=OTHER, base="e" * 40, run=6, started=3)
        current = self.record(check.resolve_configuration(None, "sonnet", "medium", automatic=True),
                              owner=current_owner, model="claude-sonnet-5-5")
        current_original = copy.deepcopy(current)

        def render(details):
            return check.status_comment_body(OTHER, "main", "in_progress", owner=current_owner, selection=current,
                last_review=(HEAD, "main", "success"), last_review_details=details, trigger_label="Draft marked ready")

        body = render(historical)
        top, groups = body.split("**Current attempt**", 1)
        current_section, previous_section = groups.split("**Previous completed review**", 1)
        warning = "⚠️ **Model:** Opus selected; primary model mismatch (Sonnet observed)"
        self.assertNotIn("⚠️ **Model:**", top + current_section)
        self.assertIn("**Model:** Sonnet · **Effort:** Medium", current_section)
        self.assertEqual(previous_section.count(warning), 1)
        self.assertEqual(previous_section.strip().splitlines()[0].rstrip(), warning)
        self.assertLess(previous_section.index(warning), previous_section.index("**Model:** Opus"))
        self.assertLess(previous_section.index(warning), previous_section.index("against"))
        self.assertEqual(check.last_completed_review_details({**comment, "body": body})["selection"], original)
        self.assertEqual(previous, original)
        self.assertEqual(current, current_original)

        # A canonical-looking receipt belonging to another generation cannot
        # supply either the historical settings or its mismatch warning.
        marker = next(line for line in comment["body"].splitlines()
                      if line.startswith(check.STATUS_REVIEWED_SELECTION_PREFIX))
        foreign = {**previous, "generation": current_owner["generation"]}
        replacement = check.STATUS_REVIEWED_SELECTION_PREFIX + quote(json.dumps(
            foreign, sort_keys=True, separators=(",", ":")), safe="") + " -->"
        stale_receipt = {**comment, "body": comment["body"].replace(marker, replacement)}
        stale_details = check.last_completed_review_details(stale_receipt)
        self.assertNotIn("selection", stale_details)
        stale_previous = render(stale_details).split("**Previous completed review**", 1)[1]
        self.assertNotIn("⚠️ **Model:**", stale_previous)
        self.assertNotIn("**Model:**", stale_previous)
        mismatched = copy.deepcopy(historical)
        mismatched["selection"]["generation"] = "f" * 64
        self.assertNotIn("⚠️ **Model:**", render(mismatched).split("**Previous completed review**", 1)[1])
        self.assertEqual(previous, original)

    def test_previously_recorded_v2_repository_configuration_stays_historical(self):
        owner = presentation_owner()
        config = {"version": 2, "errors": [], "diagnostics": [], "requested_model": "opus",
                  "requested_effort": "high", "model_source": "repository", "effort_source": "repository"}
        self.assertEqual(check.valid_configuration(config), config)
        record = self.record(config, owner=owner, model="claude-opus-5-5")
        original = copy.deepcopy(record)
        comment = self.completed_comment(owner, record)
        current = check.resolve_configuration(None, "haiku", "low", automatic=True)
        with mock.patch.dict(os.environ, {"SELECTION_CONFIG": json.dumps(current),
                                         "WORKFLOW_REVIEW_MODEL": "haiku", "WORKFLOW_REVIEW_EFFORT": "low"}):
            historical = check.last_completed_review_details(comment)
        self.assertEqual(historical["selection"], original)
        self.assertEqual(historical["selection"]["configuration"]["model_source"], "repository")
        self.assertEqual(record, original)

    def test_historical_selection_rejects_duplicate_wrong_owner_and_noncanonical_receipts(self):
        owner = presentation_owner()
        record = self.record(check.resolve_configuration(None, "opus", "high", automatic=True), owner=owner)
        comment = self.completed_comment(owner, record)
        marker = next(line for line in comment["body"].splitlines()
                      if line.startswith(check.STATUS_REVIEWED_SELECTION_PREFIX))
        wrong_owner = {**record, "generation": "f" * 64}
        replacements = (
            marker + "\n" + marker,
            check.STATUS_REVIEWED_SELECTION_PREFIX + quote(json.dumps(wrong_owner, sort_keys=True, separators=(",", ":")), safe="") + " -->",
            check.STATUS_REVIEWED_SELECTION_PREFIX + quote(json.dumps(record), safe="") + " -->",
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                corrupted = {**comment, "body": comment["body"].replace(marker, replacement)}
                self.assertNotIn("selection", check.last_completed_review_details(corrupted))
        untrusted = {**comment, "user": {"login": "someone", "type": "User"}}
        self.assertNotIn("selection", check.last_completed_review_details(untrusted))

    def test_legacy_completed_settings_do_not_borrow_current_request_configuration(self):
        owner = presentation_owner(kind="issue_comment", target=12)
        record = self.record(check.resolve_configuration("/claude-review opus high"), owner=owner)
        legacy = {key: value for key, value in record.items() if key != "configuration"}
        self.assertEqual(check.valid_selection(legacy, owner), legacy)
        comment = self.completed_comment(owner, legacy)
        comment["body"] = re.sub(re.escape(check.STATUS_REVIEWED_SELECTION_PREFIX) + r".*? -->\n", "", comment["body"])
        current = check.resolve_configuration("/claude-review haiku low")
        with mock.patch.dict(os.environ, {"SELECTION_CONFIG": json.dumps(current),
                                          "TRIGGER_COMMENT": "/claude-review haiku low"}):
            historical = check.last_completed_review_details(comment)
        self.assertEqual(historical["selection"], legacy)
        self.assertEqual(historical["selection"]["requested_model"], "opus")

        # A failed partial rerun's current receipt cannot backfill a legacy
        # completed result merely because it retained the same generation.
        failed = {**comment, "body": comment["body"].replace(
            check.STATUS_STATE_PREFIX + "success -->", check.STATUS_STATE_PREFIX + "failure -->")}
        self.assertNotIn("selection", check.last_completed_review_details(failed))

    def test_immutable_manual_receipt_keeps_routine_settings_in_details_and_warning_visible(self):
        owner = presentation_owner(kind="issue_comment", target=12)
        config = check.resolve_configuration("/claude-review sonnet high")
        record = self.record(config, owner=owner, model="claude-haiku-5-5")
        original = copy.deepcopy(record)
        marker = check.completion_marker(REPO, 7, HEAD, "main", BASE_TIP, "c" * 40)
        body = check.completion_comment_body(REPO, HEAD, "main", BASE_TIP, marker,
                                             f"https://github.com/{REPO}/actions/runs/5", selection=record)
        main, details = body.split("<details>", 1)
        self.assertIn("Sonnet selected; primary model mismatch (Haiku observed)", main)
        self.assertNotIn("**Effort:**", main)
        self.assertIn("**Model:** Sonnet · **Effort:** High", details)
        self.assertIn(marker, body)
        self.assertEqual(record, original)
        self.assertNotIn("Observed effort", body)


if __name__ == "__main__":
    unittest.main()
