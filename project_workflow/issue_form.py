"""Generate one EP submission form from config and working-group catalogues."""
from copy import deepcopy
from pathlib import Path
import argparse
import re
import sys
import yaml

from project_workflow.project_registry import Invalid, load_local_context, parse_yaml

LABEL = 'new-project'
BRANCH_PREFIX = 'project-submissions/issue-'
BOT_LOGIN = 'github-actions[bot]'
BOT_ID = 41898282
CONTRIBUTOR_FIELD = re.compile(r'contributor_([1-9][0-9]*)_(name|github_username)\Z')
CORE_FIELDS = {'lead', 'lead_github_username', 'working_groups', 'phd_thesis',
               'slack_channel', 'summary', 'github_url', 'related_links'}


def option_value(value):
    return value.split('—', 1)[0].strip()


def group_options(ctx):
    return {f"{code} — {' '.join(group['description'].split()).replace(',', ';')}": code
            for code, group in sorted(ctx.working_groups.items())}


def deliverable_field(code):
    return 'deliverables_' + code


def deliverable_options(ctx, code):
    options = []
    for item in ctx.working_groups[code]['deliverables']:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', item['id']):
            raise Invalid('Issue-form deliverable IDs must use letters, digits, dots, underscores or hyphens')
        title = ' '.join(item['title'].split()).replace(',', ';')
        options.append(f"{item['id']} — {title}")
    return options


def form(ctx):
    spec = deepcopy(ctx.form_source)
    expanded = []
    for item in spec['body']:
        source = item.pop('options_from', None)
        if item.get('type') == 'working_group_sections':
            for code, group in sorted(ctx.working_groups.items()):
                options = deliverable_options(ctx, code)
                # Native dropdowns cannot have an empty options list.
                if options:
                    attrs = {k:v.replace('{code}', code) for k,v in item['attributes'].items()}
                    expanded.append({'type':'dropdown','id':deliverable_field(code),
                        'attributes':{**attrs,'multiple':True,'options':options},
                        'validations':{'required':False}})
            continue
        if item.get('id') == 'working_groups':
            if source != 'working_groups' or item.get('type') != 'dropdown' or not item['attributes'].get('multiple') or not item.get('validations',{}).get('required'):
                raise Invalid('Working groups must be a required catalogue-backed multiple-selection dropdown')
            item['attributes']['options'] = list(group_options(ctx))
        elif source is not None:
            raise Invalid('Unsupported options source')
        expanded.append(item)
    spec['body'] = expanded
    fields = {x['id']: x for x in expanded if x.get('type') != 'markdown'}
    expected = CORE_FIELDS | {deliverable_field(c) for c in ctx.working_groups if ctx.deliverables[c]}
    extras = set(fields) - expected
    if not expected <= set(fields) or any(not CONTRIBUTOR_FIELD.fullmatch(k) for k in extras):
        raise Invalid('Form fields must match the EP submission handler')
    slots = {CONTRIBUTOR_FIELD.fullmatch(k)[1] for k in extras}
    if any({f'contributor_{i}_name',f'contributor_{i}_github_username'} - set(fields) for i in slots):
        raise Invalid('Each contributor requires paired name and username fields')
    labels = [x['attributes']['label'] for x in fields.values()]
    if len(set(labels)) != len(labels) or any(not isinstance(x,str) or not x.strip() or '\n' in x for x in labels):
        raise Invalid('Answer labels must be unique nonempty single-line text')
    return spec


def current_labels(ctx):
    return {x['id']:x['attributes']['label'] for x in form(ctx)['body'] if x['type'] != 'markdown'}


def label_keys(ctx):
    return {label:key for key,label in current_labels(ctx).items()}


def generated_text(ctx):
    return '# Generated from project_workflow/config.yaml and working_groups/; do not edit this output.\n' + yaml.safe_dump(form(ctx),sort_keys=False,allow_unicode=True,width=110)


def validate_form(text, ctx):
    """Check parser labels, controls and catalogue choices, not presentation wording."""
    spec = parse_yaml(text)
    if not isinstance(spec,dict) or LABEL not in spec.get('labels',[]):
        raise Invalid('Submission form must apply the new-project label')
    fields = [x for x in spec.get('body',[]) if x.get('type') != 'markdown']
    expected = {x['id']:x for x in form(ctx)['body'] if x['type'] != 'markdown'}
    if len(fields) != len(expected) or {x.get('id') for x in fields} != set(expected):
        raise Invalid('Submission form fields must match the handler')
    for x in fields:
        e = expected[x['id']]
        for key in ('type','validations'):
            if x.get(key) != e.get(key):
                raise Invalid('Submission controls must match the source configuration')
        for key in ('label','options','multiple'):
            if x['attributes'].get(key) != e['attributes'].get(key):
                raise Invalid('Submission labels and catalogue options must match the source')
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args()
    try:
        ctx = load_local_context(args.root)
        path = args.root / '.github/ISSUE_TEMPLATE/new-project.yml'
        if args.check:
            validate_form(path.read_text(),ctx)
            print('Submission form matches its parser and catalogues.')
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(generated_text(ctx),encoding='utf-8')
            print('Generated New Project form.')
        return 0
    except (Invalid,OSError,ValueError,KeyError,TypeError) as exc:
        print(str(exc),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
