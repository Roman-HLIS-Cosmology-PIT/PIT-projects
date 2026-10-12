"""Optional offline checks of data safety and project authorization."""
from copy import deepcopy
from pathlib import Path
import unittest
import yaml
from project_workflow.project_registry import Invalid, load_context, parse_yaml, validate_record, authorize_change
from project_workflow.generated_files import render

ROOT = Path(__file__).resolve().parents[2]


def source_data():
    config = yaml.safe_load((ROOT / 'project_workflow/config.yaml').read_text())
    config['steering_committee'] = 'test-org/steering-committee'
    data = {'project_workflow/config.yaml':yaml.safe_dump(config,sort_keys=False), 'projects/.gitkeep':''}
    data.update({p.relative_to(ROOT).as_posix():p.read_text() for p in (ROOT/'working_groups').glob('*.yaml')})
    data.update(render(load_context(data.__getitem__,paths=data)))
    return data


def is_test_sc(org, slug, actor):
    return (org,slug,actor) == ('test-org','steering-committee','sc-user')


def snapshot(data):
    from test_issue_submission import tree
    return tree(data)


def project():
    return {'schema_version':1,'project_id':'P001','name':'Project','lead':'Alice','lead_github_username':'alice',
        'contributors':[],'summary':'Scientific goals','kind':'EP','status':'active',
        'working_groups':[{'code':'SCM','deliverable_ids':['SCM-1']}], 'phd_thesis':False}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        data = source_data()
        self.ctx = load_context(data.__getitem__,paths=data)

    def test_multiple_groups_and_empty_deliverables(self):
        p = project();p['working_groups'].append({'code':'CAS','deliverable_ids':[]})
        validate_record(p,'projects/P001.yaml',self.ctx)

    def test_unknown_duplicate_and_empty_groups_rejected(self):
        for groups in ([],[{'code':'OTHER','deliverable_ids':[]}],project()['working_groups']*2):
            p = project();p['working_groups'] = groups
            with self.subTest(groups=groups),self.assertRaises(Invalid):
                validate_record(p,'projects/P001.yaml',self.ctx)

    def test_cross_group_deliverables_rejected(self):
        p = project();p['working_groups'][0]['deliverable_ids']=['CAS-1']
        with self.assertRaises(Invalid):validate_record(p,'projects/P001.yaml',self.ctx)

    def test_old_fields_and_unknown_fields_rejected(self):
        for key in ('working_group','deliverable_ids','email','acceptance'):
            p = project();p[key]='obsolete'
            with self.subTest(key=key),self.assertRaises(Invalid):validate_record(p,'projects/P001.yaml',self.ctx)

    def test_member_create_self_only_and_ep_only(self):
        for kind,actor in [('EP','alice'),('EP','mallory'),('CPP','alice'),('KEY','alice')]:
            p = project();p['kind']=kind
            if kind=='EP' and actor=='alice':
                authorize_change(None,p,actor,self.ctx,organization_member=True)
            else:
                with self.assertRaises(Invalid):authorize_change(None,p,actor,self.ctx,organization_member=True)

    def test_sc_can_manage_cpp_key_without_lead(self):
        for kind in ('CPP','KEY'):
            p = project();p.update(kind=kind,lead=None,lead_github_username=None)
            validate_record(p,'projects/P001.yaml',self.ctx)
            authorize_change(None,p,'sc-user',self.ctx,organization_member=True,is_sc=True)

    def test_member_cannot_update_accepted_project(self):
        with self.assertRaises(Invalid):authorize_change(project(),project(),'alice',self.ctx,organization_member=True)

    def test_unsafe_yaml_and_remote_refs_rejected(self):
        for text in ('a: 1\na: 2','a: &x [1]\nb: *x','a: !!python/object:evil {}'):
            with self.subTest(text=text),self.assertRaises(Invalid):parse_yaml(text)
        data=source_data(); c=yaml.safe_load(data['project_workflow/config.yaml'])
        c['schema']['properties']['summary']={'$ref':'https://example.org/schema'}
        data['project_workflow/config.yaml']=yaml.safe_dump(c)
        with self.assertRaises(Invalid):load_context(data.__getitem__,paths=data)
