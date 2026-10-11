"""Catalogue discovery, extension, export and authorization checks."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from project_workflow.project_registry import Invalid, load_context, load_local_context, parse_yaml, validate_record
from project_workflow.validate_pr import validate_snapshot
from test_registry import ROOT, is_test_sc, project, snapshot, source_data


class WorkingGroupTests(unittest.TestCase):
    def setUp(self):
        self.data = source_data()

    def check_pr(self, base, proposed, author="sc-user"):
        from project_workflow.generated_files import render
        proposed = {**proposed, **render(load_context(proposed.__getitem__, paths=proposed))}
        return validate_snapshot(snapshot(base), snapshot(proposed), base.__getitem__, proposed.__getitem__, author, sc_lookup=is_test_sc)

    def test_add_group_without_schema_or_code_change(self):
        proposed = deepcopy(self.data)
        proposed["working_groups/NEW.yaml"] = "working_group: NEW\ndescription: A new science group\ndeliverables: [{id: shared-id, title: First deliverable}]\n"
        proposed["projects/P001.yaml"] = yaml.safe_dump(project(working_group="NEW", deliverable_ids=["shared-id"]))
        self.assertEqual(self.check_pr(self.data, proposed), 1)
        ctx = load_context(proposed.__getitem__, paths=proposed)
        self.assertEqual(ctx.working_groups["NEW"]["description"], "A new science group")

    def test_unknown_group_rejected_even_with_no_deliverables(self):
        ctx = load_context(self.data.__getitem__, paths=self.data)
        with self.assertRaises(Invalid):
            validate_record(project(working_group="UNKNOWN", deliverable_ids=[]), "projects/P001.yaml", ctx)

    def test_add_deliverable_without_schema_or_code_change(self):
        proposed = deepcopy(self.data)
        group = parse_yaml(proposed["working_groups/SCM.yaml"])
        group["deliverables"].append({"id": "extra-deliverable", "title": "New measurement"})
        proposed["working_groups/SCM.yaml"] = yaml.safe_dump(group)
        proposed["projects/P001.yaml"] = yaml.safe_dump(project(deliverable_ids=["extra-deliverable"]))
        self.assertEqual(self.check_pr(self.data, proposed), 1)

    def test_removing_used_group_requires_project_migration(self):
        self.data["projects/P001.yaml"] = yaml.safe_dump(project())
        proposed = deepcopy(self.data)
        del proposed["working_groups/SCM.yaml"]
        with self.assertRaises(Invalid):
            self.check_pr(self.data, proposed)
        proposed["projects/P001.yaml"] = yaml.safe_dump(project(working_group="CAS", deliverable_ids=["CAS-001"]))
        self.assertEqual(self.check_pr(self.data, proposed), 1)

    def test_invalid_definition_rejected(self):
        valid = parse_yaml(self.data["working_groups/SCM.yaml"])
        invalid = [
            {**valid, "working_group": "OTHER"}, {**valid, "description": " "},
            {**valid, "description": None}, {**valid, "extra": "unknown"},
            {key: value for key, value in valid.items() if key != "description"},
            {**valid, "deliverables": [{"id": "X", "title": " "}]},
            {**valid, "deliverables": [{"id": "X", "title": "one"}, {"id": "X", "title": "two"}]},
        ]
        for group in invalid:
            proposed = {**self.data, "working_groups/SCM.yaml": yaml.safe_dump(group)}
            with self.subTest(group=group), self.assertRaises(Invalid):
                load_context(proposed.__getitem__, paths=proposed)

    def test_no_groups_or_missing_directory_rejected(self):
        proposed = {p: v for p, v in self.data.items() if not p.startswith("working_groups/")}
        for paths in (proposed, set(proposed) | {"working_groups"}):
            with self.assertRaises(Invalid):
                load_context(proposed.__getitem__, paths=paths)

    def test_unsafe_nested_or_wrong_extension_paths_rejected(self):
        for path in ("working_groups/lower.yaml", "working_groups/../SCM.yaml", "working_groups/nested/NEW.yaml", "working_groups/NEW.yml"):
            proposed = {**self.data, path: "working_group: NEW\ndescription: Test\ndeliverables: []"}
            with self.subTest(path=path), self.assertRaises(Invalid):
                load_context(proposed.__getitem__, paths=proposed)

    def test_group_choices_cannot_be_duplicated_in_schema(self):
        config = parse_yaml(self.data["project_workflow/config.yaml"])
        config["schema"]["properties"]["working_group"]["enum"] = ["SCM", "CAS", "CPIP"]
        proposed = {**self.data, "project_workflow/config.yaml": yaml.safe_dump(config)}
        with self.assertRaises(Invalid):
            load_context(proposed.__getitem__, paths=proposed)


    def test_local_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_fixture(root)
            source = root / "working_groups/SCM.yaml"
            backup = root / "definition.yaml"
            source.rename(backup)
            source.symlink_to(backup)
            with self.assertRaises(Invalid):
                load_local_context(root)

    def write_fixture(self, root):
        for path, content in self.data.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        (root / "projects").mkdir()

    def test_combined_yaml_and_json_exports_include_group_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_fixture(root)
            exports = {}
            for command, filename in (("working-groups", "groups.yaml"), ("catalog", "projects.json")):
                target = root / filename
                result = subprocess.run([sys.executable, "-m", "project_workflow.project_registry", "--root", str(root), command, "--output", str(target)], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                exports[command] = yaml.safe_load(target.read_text())
            self.assertEqual(exports["working-groups"]["working_groups"], exports["catalog"]["working_groups"])
            scm = next(g for g in exports["catalog"]["working_groups"] if g["working_group"] == "SCM")
            self.assertEqual(scm["description"], "Shape and Color Measurement")
            self.assertEqual(scm["deliverables"], [{"id": "SCM-001", "title": "Test deliverable"}])


if __name__ == "__main__":
    unittest.main()
