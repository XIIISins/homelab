"""Phase 10d: the n8n workflow check in aiops/tools/lint.py.

The committed workflows must be clean, and each rule must actually bite: every
test mutates a copy of the real stub workflow in a throwaway tree and expects
exactly the finding the rule exists to produce.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import lint  # noqa: E402

STUB = REPO / "aiops" / "n8n" / "workflows" / "ingest-zabbix.json"


class N8nWorkflowLint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for rel in ("ansible/roles/n8n-agent/defaults/main.yml", "terraform/vault/main.tf"):
            dst = self.tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, dst)
        (self.tmp / "aiops/n8n/workflows").mkdir(parents=True)
        self.wf = json.loads(STUB.read_text())

    def check(self, wf=None, raw=None):
        path = self.tmp / "aiops/n8n/workflows/t.json"
        path.write_text(raw if raw is not None else json.dumps(wf if wf is not None else self.wf))
        return lint.check_n8n_workflows(self.tmp)

    def node(self, wf, type_suffix):
        return next(n for n in wf["nodes"] if n["type"].endswith(type_suffix))

    def assertFinding(self, errs, needle):
        self.assertTrue(any(needle in e for e in errs), f"expected {needle!r} in {errs}")

    def test_committed_workflows_are_clean(self):
        self.assertEqual(lint.check_n8n_workflows(REPO), [])

    def test_clean_copy_passes(self):
        self.assertEqual(self.check(), [])

    def test_code_node_denied(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "set")["type"] = "n8n-nodes-base.code"
        self.assertFinding(self.check(wf), "denied type n8n-nodes-base.code")

    def test_execute_command_denied(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "set")["type"] = "n8n-nodes-base.executeCommand"
        self.assertFinding(self.check(wf), "denied type n8n-nodes-base.executeCommand")

    def test_unknown_node_type_not_allowed(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "set")["type"] = "n8n-nodes-base.somethingNew"
        self.assertFinding(self.check(wf), "not in the allow-list")

    def test_inline_discord_webhook_rejected(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "httpRequest")["parameters"]["url"] = "https://discord.com/api/webhooks/123456789/AbCd-eF_gh"
        errs = self.check(wf)
        self.assertFinding(errs, "Discord webhook URL")

    def test_inline_anthropic_key_rejected(self):
        raw = json.dumps(self.wf).replace("aiops-ingest-zabbix", "sk-ant-api03-abcdefghijklmnop")
        self.assertFinding(self.check(raw=raw), "Anthropic API key")

    def test_credential_with_data_rejected(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "webhook")["credentials"]["httpHeaderAuth"]["data"] = {"value": "x"}
        self.assertFinding(self.check(wf), "must be a reference")

    def test_unauthenticated_webhook_rejected(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "webhook")["parameters"]["authentication"] = "none"
        self.assertFinding(self.check(wf), "must use headerAuth")

    def test_webhook_outside_aiops_prefix_rejected(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "webhook")["parameters"]["path"] = "other/zabbix"
        self.assertFinding(self.check(wf), "must start with `aiops/`")

    def test_webhook_must_respond_immediately(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "webhook")["parameters"]["responseMode"] = "lastNode"
        self.assertFinding(self.check(wf), "respond immediately")

    def test_unknown_source_rejected(self):
        wf = copy.deepcopy(self.wf)
        w = self.node(wf, "webhook")
        w["parameters"]["path"] = "aiops/nope"
        w["credentials"]["httpHeaderAuth"]["name"] = "aiops-ingest-nope"
        self.assertFinding(self.check(wf), "not in n8n-agent n8n_ingest_sources")

    def test_literal_url_to_unknown_host_rejected(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "httpRequest")["parameters"]["url"] = "https://example.invalid/hook"
        self.assertFinding(self.check(wf), "literal URL host")

    def test_env_url_expression_must_be_aiops_scoped(self):
        wf = copy.deepcopy(self.wf)
        self.node(wf, "httpRequest")["parameters"]["url"] = "={{ $env.PATH }}"
        self.assertFinding(self.check(wf), "must use $env.AIOPS_*")

    def test_toolbelt_call_without_its_credential_rejected(self):
        wf = copy.deepcopy(self.wf)
        node = next(n for n in wf["nodes"] if n["name"] == "Toolbelt ingest")
        del node["credentials"]
        self.assertFinding(self.check(wf), "without the `aiops-toolbelt` credential")

    def test_toolbelt_call_with_another_credential_rejected(self):
        wf = copy.deepcopy(self.wf)
        node = next(n for n in wf["nodes"] if n["name"] == "Get group")
        node["credentials"]["httpHeaderAuth"]["name"] = "aiops-ingest-zabbix"
        self.assertFinding(self.check(wf), "without the `aiops-toolbelt` credential")

    # ---- the diagnosis agent (10d3) ---------------------------------------------------------------
    def agent_node(self, wf, ntype):
        return next(n for n in wf["nodes"] if n["type"] == ntype)

    def test_committed_workflow_is_what_the_generator_produces(self):
        sys.path.insert(0, str(REPO / "aiops" / "n8n"))
        import build_ingest

        self.assertEqual(build_ingest.OUT.read_text(), build_ingest.render(), "run python3 aiops/n8n/build_ingest.py")

    def test_committed_watchdog_is_what_the_generator_produces(self):
        sys.path.insert(0, str(REPO / "aiops" / "n8n"))
        import build_ingest

        self.assertEqual(build_ingest.WATCHDOG_OUT.read_text(), build_ingest.render_watchdog(), "run python3 aiops/n8n/build_ingest.py")

    def test_the_watchdog_only_talks_to_the_toolbelt_and_discord_and_needs_no_webhook(self):
        wf = json.loads((REPO / "aiops" / "n8n" / "workflows" / "watchdog.json").read_text())
        types = {n["type"] for n in wf["nodes"]}
        self.assertEqual(types, {"n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.httpRequest"})
        self.assertFalse(wf["active"])

    def test_other_langchain_nodes_are_not_allowed(self):
        for bad in ("toolCode", "toolWorkflow", "mcpClientTool", "lmChatOpenAi", "agentTool"):
            wf = copy.deepcopy(self.wf)
            self.agent_node(wf, "@n8n/n8n-nodes-langchain.toolHttpRequest")["type"] = f"@n8n/n8n-nodes-langchain.{bad}"
            self.assertFinding(self.check(wf), "not in the allow-list")

    def test_agent_needs_a_bounded_iteration_cap_and_a_system_message(self):
        for opts, msg in (({"systemMessage": "x"}, "maxIterations"), ({"systemMessage": "x", "maxIterations": 50}, "maxIterations"),
                          ({"maxIterations": 5}, "system message")):
            wf = copy.deepcopy(self.wf)
            self.agent_node(wf, "@n8n/n8n-nodes-langchain.agent")["parameters"]["options"] = opts
            self.assertFinding(self.check(wf), msg)

    def test_model_must_use_the_agent_credential(self):
        wf = copy.deepcopy(self.wf)
        self.agent_node(wf, "@n8n/n8n-nodes-langchain.lmChatAnthropic")["credentials"]["anthropicApi"]["name"] = "someone-elses"
        self.assertFinding(self.check(wf), "aiops-anthropic")

    def test_agent_tool_is_held_to_the_same_url_and_credential_rules_as_http_nodes(self):
        wf = copy.deepcopy(self.wf)
        tool = self.agent_node(wf, "@n8n/n8n-nodes-langchain.toolHttpRequest")
        tool["parameters"]["url"] = "https://example.invalid/{tool}"
        self.assertFinding(self.check(wf), "literal URL host")
        wf = copy.deepcopy(self.wf)
        del self.agent_node(wf, "@n8n/n8n-nodes-langchain.toolHttpRequest")["credentials"]
        self.assertFinding(self.check(wf), "without the `aiops-toolbelt` credential")

    def test_the_tool_description_the_model_sees_lists_exactly_the_toolbelt_allow_list(self):
        sys.path.insert(0, str(REPO / "aiops" / "n8n"))
        sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
        import build_ingest
        import tools

        listed = {ln[2:].split("(", 1)[0] for ln in build_ingest.tool_description().splitlines() if ln.startswith("- ")}
        self.assertEqual(listed, set(tools.SPEC))

    def test_the_system_prompt_only_names_tools_that_exist(self):
        import re

        sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
        import tools

        prefixes = {n.split(".")[0] for n in tools.SPEC}
        text = (REPO / "aiops" / "n8n" / "prompts" / "diagnose.system.md").read_text()
        for name in set(re.findall(r"`([a-z]+\.[a-z_]+)`", text)):
            if name.split(".")[0] in prefixes:
                self.assertIn(name, tools.SPEC, f"the prompt names {name}, which is not a Toolbelt tool")

    def test_active_workflow_rejected(self):
        wf = copy.deepcopy(self.wf)
        wf["active"] = True
        self.assertFinding(self.check(wf), "`active` must be false")

    def test_dangling_connection_rejected(self):
        wf = copy.deepcopy(self.wf)
        wf["connections"]["Toolbelt ingest"]["main"][0][0]["node"] = "Nope"
        self.assertFinding(self.check(wf), "connection to unknown node")

    def test_role_and_terraform_sources_must_agree(self):
        tf = self.tmp / "terraform/vault/main.tf"
        tf.write_text(tf.read_text().replace('toset(["zabbix"])', 'toset(["zabbix", "semaphore"])'))
        self.assertFinding(self.check(), "role n8n_ingest_sources")

    def test_invalid_json_reported(self):
        self.assertFinding(self.check(raw="{not json"), "invalid JSON")


if __name__ == "__main__":
    unittest.main()
