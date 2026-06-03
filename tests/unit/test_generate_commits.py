"""Unit tests for the generate-commits machinery."""
import pytest
import generate_commits as gc
import hw_logic


@pytest.mark.unit
class TestBuildSnapshot:
    def test_returns_project_id(self, seeded_project):
        pid = seeded_project['id']
        snap = gc.build_snapshot(pid)
        assert snap['project_id'] == pid

    def test_empty_project_has_empty_bom(self, seeded_project):
        pid = seeded_project['id']
        snap = gc.build_snapshot(pid)
        assert snap['bom'] == []

    def test_includes_bom_after_save(self, seeded_project, seeded_hw_template):
        pid = seeded_project['id']
        bom = [{'id': 'bom1', 'template_id': seeded_hw_template['id'],
                'qty': 3, 'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3}]
        hw_logic.save_bom(pid, bom)
        snap = gc.build_snapshot(pid)
        assert len(snap['bom']) == 1
        assert snap['bom'][0]['id'] == 'bom1'


@pytest.mark.unit
class TestDeriveTemplateSnapshot:
    def test_extracts_tracked_fields(self, seeded_hw_template):
        tmpl = seeded_hw_template
        inst = {'id': 'i1', 'template_id': tmpl['id'], 'project_id': 'p1'}
        snap = gc.derive_template_snapshot(inst, {tmpl['id']: tmpl})
        for f in ('u_size', 'category'):
            if f in tmpl:
                assert f in snap

    def test_missing_template_gives_no_tracked_fields(self):
        inst = {'id': 'i1', 'template_id': 'missing', 'project_id': 'p1'}
        snap = gc.derive_template_snapshot(inst, {})
        # No tracked fields present; only ports key may be present (empty list)
        for f in gc.HW_TRACKED_FIELDS:
            assert f not in snap


@pytest.mark.unit
class TestCreateInitialCommit:
    def test_creates_commit_record(self, seeded_project):
        pid = seeded_project['id']
        cid = gc.create_initial_commit(pid)
        assert cid.startswith('c-')
        commit = gc.get_generate_commit(cid)
        assert commit is not None
        assert commit['kind'] == 'initial'
        assert commit['project_id'] == pid

    def test_idempotent(self, seeded_project):
        pid = seeded_project['id']
        cid1 = gc.create_initial_commit(pid)
        cid2 = gc.create_initial_commit(pid)
        assert cid1 == cid2

    def test_sets_head_commit(self, seeded_project):
        pid = seeded_project['id']
        cid = gc.create_initial_commit(pid)
        assert gc.get_project_head(pid) == cid

    def test_backfills_instances(self, seeded_project, seeded_hw_template):
        pid = seeded_project['id']
        # Create an instance manually without commit tracking
        inst = {
            'id': 'hw-test-01', 'template_id': seeded_hw_template['id'],
            'project_id': pid, 'asset_tag': 'srv-001', 'serial': '',
            'status': 'in-stock', 'location': {}, 'port_overrides': {},
        }
        hw_logic.save_hw_instance(inst)

        cid = gc.create_initial_commit(pid)

        updated = hw_logic.get_hw_instance('hw-test-01')
        assert updated['created_in_commit'] == cid
        assert updated['last_touched_in_commit'] == cid
        assert 'template_snapshot' in updated
        assert len(updated['history']) >= 1
        assert updated['history'][0]['reason'] == 'initial-import'


@pytest.mark.unit
class TestProjectCommits:
    def test_empty_before_any_commit(self, seeded_project):
        pid = seeded_project['id']
        assert gc.project_commits(pid) == []

    def test_returns_commits_newest_first(self, seeded_project):
        pid = seeded_project['id']
        cid1 = gc.create_initial_commit(pid)
        # Manually record a second commit
        commit2 = {
            'id': 'c-zzzzzzzz', 'project_id': pid, 'created_at': '2026-01-02T00:00:00Z',
            'user': 'u1', 'kind': 'bom-line', 'trigger': {}, 'parent_commit': cid1,
            'snapshot_key': '', 'diff_summary': {},
            'impact': {'hw_created': [], 'hw_updated': [], 'hw_deleted': [],
                       'ne_created': [], 'ne_updated': [], 'ne_deleted': [],
                       'ne_rematerialized': [], 'conflicts': []},
            'merge_mode_default': '3way', 'notes': '',
        }
        gc.save_generate_commit(commit2)
        import db
        db.r.lpush(f'project:{pid}:commits', 'c-zzzzzzzz')

        commits = gc.project_commits(pid)
        assert commits[0]['id'] == 'c-zzzzzzzz'
        assert commits[1]['id'] == cid1


@pytest.mark.unit
class TestComputePending:
    def test_no_head_with_bom_has_changes(self, seeded_project, seeded_hw_template):
        pid = seeded_project['id']
        bom = [{'id': 'b1', 'template_id': seeded_hw_template['id'],
                'qty': 2, 'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 2}]
        hw_logic.save_bom(pid, bom)
        pending = gc.compute_pending(pid)
        assert pending['has_changes'] is True

    def test_no_changes_after_commit(self, seeded_project, seeded_hw_template):
        pid = seeded_project['id']
        bom = [{'id': 'b1', 'template_id': seeded_hw_template['id'],
                'qty': 2, 'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 2}]
        hw_logic.save_bom(pid, bom)
        gc.create_initial_commit(pid)
        pending = gc.compute_pending(pid)
        assert pending['has_changes'] is False

    def test_detects_new_bom_line(self, seeded_project, seeded_hw_template):
        pid = seeded_project['id']
        gc.create_initial_commit(pid)
        bom = [{'id': 'new-line', 'template_id': seeded_hw_template['id'],
                'qty': 1, 'tag_prefix': 'sw', 'tag_start': 1, 'tag_pad': 2}]
        hw_logic.save_bom(pid, bom)
        pending = gc.compute_pending(pid)
        assert pending['has_changes'] is True
        assert len(pending['diff_summary']['bom_added']) == 1


@pytest.mark.unit
class TestGenerateWithCommit:
    def test_creates_commit_and_stamps_instances(self, seeded_project, seeded_hw_template):
        pid = seeded_project['id']
        item = {'id': 'bom1', 'template_id': seeded_hw_template['id'],
                'qty': 2, 'tag_prefix': 'sw', 'tag_start': 1, 'tag_pad': 2}
        hw_logic.save_bom(pid, [item])

        cid, created = gc.generate_with_commit(
            pid=pid, user='tester', kind='bom-line',
            trigger={'bom_line_id': 'bom1'},
            work_fn=lambda: hw_logic.generate_instances_from_bom_line(pid, item),
        )

        assert cid.startswith('c-')
        assert len(created) == 2
        assert gc.get_project_head(pid) == cid

        for inst in created:
            stored = hw_logic.get_hw_instance(inst['id'])
            assert stored['created_in_commit'] == cid
            assert stored['last_touched_in_commit'] == cid
            assert 'template_snapshot' in stored
            assert stored['history'][0]['reason'] == 'created'

    def test_creates_initial_commit_if_none(self, seeded_project, seeded_hw_template):
        pid = seeded_project['id']
        item = {'id': 'bom1', 'template_id': seeded_hw_template['id'],
                'qty': 1, 'tag_prefix': 'rt', 'tag_start': 1, 'tag_pad': 2}
        hw_logic.save_bom(pid, [item])

        # No initial commit yet
        import db
        assert db.r.get(f'project:{pid}:initial_commit') is None

        cid, _ = gc.generate_with_commit(
            pid=pid, user='u1', kind='bom-line', trigger={},
            work_fn=lambda: hw_logic.generate_instances_from_bom_line(pid, item),
        )

        assert db.r.get(f'project:{pid}:initial_commit') is not None


@pytest.mark.unit
class TestMergeLogic:
    def test_clean_field_update(self, seeded_hw_template):
        inst = {
            'id': 'hw1',
            'template_id': seeded_hw_template['id'],
            'project_id': 'p1',
            'u_size': 1,
            'template_snapshot': {'u_size': 1},
            'history': [],
        }
        new_tmpl = {**seeded_hw_template, 'u_size': 2}
        merged, conflicts = gc._merge_instance_fields(inst, new_tmpl, '3way')
        assert merged['u_size'] == 2
        assert conflicts == []

    def test_conflict_keeps_local_in_3way(self, seeded_hw_template):
        inst = {
            'id': 'hw1',
            'template_id': seeded_hw_template['id'],
            'project_id': 'p1',
            'u_size': 99,  # local edit
            'template_snapshot': {'u_size': 1},  # what template gave last time
            'history': [],
        }
        new_tmpl = {**seeded_hw_template, 'u_size': 2}  # template changed to 2
        merged, conflicts = gc._merge_instance_fields(inst, new_tmpl, '3way')
        assert merged['u_size'] == 99  # kept local
        assert len(conflicts) == 1
        assert conflicts[0]['field'] == 'u_size'

    def test_overwrite_mode_applies_remote(self, seeded_hw_template):
        inst = {
            'id': 'hw1',
            'template_id': seeded_hw_template['id'],
            'project_id': 'p1',
            'u_size': 99,
            'template_snapshot': {'u_size': 1},
            'history': [],
        }
        new_tmpl = {**seeded_hw_template, 'u_size': 2}
        merged, conflicts = gc._merge_instance_fields(inst, new_tmpl, 'overwrite')
        assert merged['u_size'] == 2
        assert conflicts == []
