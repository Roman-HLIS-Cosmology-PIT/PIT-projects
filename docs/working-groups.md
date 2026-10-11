# Working-group catalogues

`working_groups/<CODE>.yaml` is the source of working-group choices. Each file
has exactly three fields:

```yaml
working_group: SCM
description: Shape and Color Measurement
deliverables:
  - id: SCM-1
    title: Calibration
```

Codes must match their filenames and start with an uppercase letter, followed
by uppercase letters, digits, underscores or hyphens. Files use `.yaml` and
sit directly in `working_groups/`. At least one group must exist. Descriptions
and deliverable titles must be nonblank strings; use `|` for long descriptions.
Deliverable IDs must be nonblank strings and unique within the selected group.
An empty `deliverables: []` is valid. No prefix/zero-padding convention is
enforced: the existing CPIP catalogue's `CPI-*` IDs remain valid.

Add a group by adding another correctly named YAML file. Add a deliverable by
adding an `id`/`title` entry in its group's file. Neither operation requires
updating the project schema, Python scripts, or an index of allowed groups.
SC approval is required as for other administrative configuration changes.

Projects continue to use `working_group: SCM` and a `deliverable_ids` list.
The validator checks both values against the directory at the proposed commit.
Removing or renaming a group/used deliverable fails until all affected project
records are updated in the same PR. There is no cascading deletion.

## Combined YAML and JSON

Run from the repository root:

```bash
python -m scripts.project_registry working-groups --output build/working_groups.yaml
python -m scripts.project_registry catalog --output build/projects.json
```

The combined YAML has a `working_groups` list with all codes, descriptions and
deliverables. The JSON contains the same list plus `schema_version` and
`projects`. Edit the per-group source files and regenerate these outputs;
`build/` remains ignored. Registry health checks that both can be generated.

## Apply this update to the existing repository

The update is based on `main` commit `5129ae9605a15d2d5b7bcf8a8507e5556c9ec3af`.
All existing deliverable IDs and titles are preserved. SCM's description is
filled in; CAS and CPIP use code labels until their official descriptions are
provided. Accepted project data needs no format migration.

Apply two updates in order. The PR validator runs code from its trusted base;
moving the data before the new reader reaches `main` would fail the old check.
Do not work around this by running proposed PR code with the team token.

1. **Validator update.** Copy the files in `01-validator-update/` from the
   supplied update ZIP to your clone, or apply `01-validator-update.patch`.
   This updates both Python scripts, registry tests, and the new working-group
   tests. Keep the current schema and `config/deliverables/` unchanged.
   Run the commands below, publish/review this code-only change yourself, and
   merge it into `main`. The new reader can validate the old layout during
   this step.
2. **Catalogue move.** Starting from that updated `main`, copy the files in
   `02-catalogue-move/`, then delete the three old files
   `config/deliverables/SCM.yaml`, `CAS.yaml`, and `CPIP.yaml`. Remove the now-empty
   directory if present. Alternatively apply `02-catalogue-move.patch`, which
   includes the deletions. This creates `working_groups/`, removes the schema
   enum, and updates documentation/templates and Registry health. Test, publish,
   review, and merge this change yourself.

Both steps have local and offline PR validation checks. Each subsequent group
or deliverable addition can be a normal single SC PR. Old-layout support is
retained solely to read historical base commits during this migration; there
are no legacy catalogue files in the final layout. Mixed old/new catalogues
are rejected to prevent competing sources of truth.

```bash
python -m scripts.project_registry validate
python -m unittest discover -s tests -v
python -m scripts.project_registry catalog --output build/projects.json
python -m scripts.project_registry working-groups --output build/working_groups.yaml
```

## Verify extension on GitHub

As an SC member, add `working_groups/TEST.yaml` in a test branch:

```yaml
working_group: TEST
description: Temporary working group for a test PR
deliverables:
  - id: TEST-1
    title: Test deliverable
```

Copy a project template to an unused `projects/P<digits>.yaml`, use real
appropriate lead/contributor identities, and set `working_group: TEST` and
`deliverable_ids: [TEST-1]`. The Project policy check should pass without schema
changes. Change the group to `MISSING`, or use a nonexistent deliverable: fail.
Restore the valid values and then remove the TEST group file while leaving the
project: fail. Close this disposable PR without merging when testing is complete.
