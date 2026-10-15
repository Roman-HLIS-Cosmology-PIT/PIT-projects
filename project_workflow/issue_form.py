"""Generate EP submission and CPP declaration forms from shared sources."""
from copy import deepcopy
from pathlib import Path
import argparse
import re
import sys
import yaml

from project_workflow.project_registry import Invalid, load_local_context, parse_yaml

LABEL = 'new-project'
FORM_LABELS = {'EP': LABEL, 'CPP': 'new-cpp-project', 'CLAIM': 'claim-cpp'}
FORM_PATHS = {'EP': '.github/ISSUE_TEMPLATE/new-project.yml',
              'CPP': '.github/ISSUE_TEMPLATE/new-cpp-project.yml',
              'CLAIM': '.github/ISSUE_TEMPLATE/claim-cpp.yml'}
CPP_OMITTED_FIELDS = {'lead', 'lead_github_username', 'phd_thesis'}
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


def form(ctx, kind='EP'):
    if kind not in FORM_LABELS:
        raise Invalid('Unsupported submission kind')
    spec = deepcopy(ctx.form_source)
    variants = spec.pop('variants', {})
    if kind != 'EP':
        variant = deepcopy(variants.get(kind, {}))
        required = {'name', 'description', 'labels', 'introduction'} | ({'title'} if kind == 'CLAIM' else set())
        if set(variant) != required:
            raise Invalid('Form variant requires name, description, labels, introduction and claim title where applicable')
        introduction = variant.pop('introduction')
        if not spec['body'] or spec['body'][0].get('type') != 'markdown':
            raise Invalid('Shared form must start with its introduction')
        spec['body'][0]['attributes']['value'] = introduction
        spec.update(variant)
    if spec.get('labels') != [FORM_LABELS[kind]]:
        raise Invalid('Each form must apply only its own submission label')
    expanded = []
    for item in spec['body']:
        ep_only = item.pop('ep_only', False)
        identifier = item.get('id', '')
        if kind == 'CPP' and (ep_only or identifier in CPP_OMITTED_FIELDS or CONTRIBUTOR_FIELD.fullmatch(identifier)):
            continue
        if kind == 'CLAIM':
            if identifier == 'lead_github_username':
                expanded.append(item)
                from project_workflow.claims import project_options
                expanded.append({'type':'dropdown', 'id':'claim_project',
                    'attributes':{'label':'CPP project to claim', 'multiple':False,
                        'description':'Select exactly one project. Availability is checked again on submission.',
                        'options':project_options(ctx) or ['No CPP projects available']},
                    'validations':{'required':True}})
                continue
            if identifier in {'working_groups', 'summary', 'phd_thesis'} or item.get('type') == 'working_group_sections':
                continue
            if item.get('type') == 'markdown' and item['attributes']['value'].startswith('## Deliverables'):
                continue
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
    if kind == 'CPP':
        expected -= CPP_OMITTED_FIELDS
    elif kind == 'CLAIM':
        expected = {'lead','lead_github_username','claim_project','slack_channel','github_url','related_links'}
    extras = set(fields) - expected
    if not expected <= set(fields) or (extras if kind == 'CPP' else any(not CONTRIBUTOR_FIELD.fullmatch(k) for k in extras)):
        raise Invalid('Form fields must match the submission handler')
    slots = {CONTRIBUTOR_FIELD.fullmatch(k)[1] for k in extras}
    if any({f'contributor_{i}_name',f'contributor_{i}_github_username'} - set(fields) for i in slots):
        raise Invalid('Each contributor requires paired name and username fields')
    labels = [x['attributes']['label'] for x in fields.values()]
    if len(set(labels)) != len(labels) or any(not isinstance(x,str) or not x.strip() or '\n' in x for x in labels):
        raise Invalid('Answer labels must be unique nonempty single-line text')
    return spec


def current_labels(ctx, kind='EP'):
    return {x['id']:x['attributes']['label'] for x in form(ctx,kind)['body'] if x['type'] != 'markdown'}


def label_keys(ctx, kind='EP'):
    return {label:key for key,label in current_labels(ctx,kind).items()}


def generated_text(ctx, kind='EP'):
    return '# Generated from project_workflow/config.yaml and working_groups/; do not edit this output.\n' + yaml.safe_dump(form(ctx,kind),sort_keys=False,allow_unicode=True,width=110)


def validate_form(text, ctx, kind='EP'):
    """Check parser labels, controls and catalogue choices, not presentation wording."""
    spec = parse_yaml(text)
    if not isinstance(spec,dict) or spec.get('labels') != [FORM_LABELS[kind]]:
        raise Invalid('Submission form must apply only its own submission label')
    fields = [x for x in spec.get('body',[]) if x.get('type') != 'markdown']
    expected = {x['id']:x for x in form(ctx,kind)['body'] if x['type'] != 'markdown'}
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
        for kind, relative in FORM_PATHS.items():
            path = args.root / relative
            if args.check:
                validate_form(path.read_text(),ctx,kind)
                print(kind + ' form matches its parser and catalogues.')
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(generated_text(ctx,kind),encoding='utf-8')
                print('Generated ' + kind + ' form.')
        return 0
    except (Invalid,OSError,ValueError,KeyError,TypeError) as exc:
        print(str(exc),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
