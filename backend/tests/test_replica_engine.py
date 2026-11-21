"""stdlib-only 单元测试：python3 -m tests.test_replica_engine（在 backend/ 下运行）。"""
import copy
import unittest

from src.services.replica_engine import reconcile, validate_inputs

INITIAL = {'sample_A': 100, 'sample_B': 50, 'sample_C': 20}


def w(tx, key, value):
    return {'op': 'write', 'tx': tx, 'key': key, 'value': value}


def gap():
    return None


class ReplicaReconcileTests(unittest.TestCase):
    def base_log(self):
        return [
            {'op': 'begin', 'tx': 'T1'},
            w('T1', 'sample_A', 75),
            {'op': 'commit', 'tx': 'T1'},
            {'op': 'checkpoint'},
            {'op': 'begin', 'tx': 'T2'},
            w('T2', 'sample_B', 40),
            {'op': 'begin', 'tx': 'T3'},
            w('T3', 'sample_A', 60),
            {'op': 'commit', 'tx': 'T3'},
            w('T2', 'sample_B', 35),
            {'op': 'commit', 'tx': 'T2'},
        ]

    def test_clean_replicas_with_vouchers(self):
        log = self.base_log()
        out = reconcile(INITIAL, copy.deepcopy(log), copy.deepcopy(log),
                        [{'tx': 'T1'}, {'tx': 'T2'}, {'tx': 'T3'}], 11)
        self.assertTrue(out['invariant_passed'])
        self.assertEqual(out['billing_ledger'], {'sample_A': 60, 'sample_B': 35, 'sample_C': 20})
        self.assertEqual(out['quarantined_keys'], [])
        self.assertEqual(sorted(out['committed_verified']), ['T1', 'T2', 'T3'])

    def test_cross_replica_value_repair(self):
        # 副本A的 T3 写入值损坏，副本B可读；位置/事务/点号仍可信
        a = self.base_log()
        b = self.base_log()
        a[7] = {'op': 'write', 'tx': 'T3', 'key': 'sample_A', 'value': 'CORRUPT'}
        out = reconcile(INITIAL, a, b, [{'tx': 'T1'}, {'tx': 'T2'}, {'tx': 'T3'}], 11)
        self.assertTrue(out['invariant_passed'])
        self.assertEqual(out['billing_ledger']['sample_A'], 60)
        self.assertEqual(out['quarantined_keys'], [])
        phases = [t['phase'] for t in out['trace']]
        self.assertIn('redo', phases)

    def test_cross_replica_gap_repair(self):
        # 副本B整条 T3 write 缺失（gap占位），副本A完整
        a = self.base_log()
        b = self.base_log()
        b[7] = gap()
        out = reconcile(INITIAL, a, b, [{'tx': 'T1'}, {'tx': 'T2'}, {'tx': 'T3'}], 11)
        self.assertEqual(out['billing_ledger']['sample_A'], 60)
        self.assertEqual(out['quarantined_keys'], [])
        self.assertTrue(any('跨副本拼合' in t.get('note', '') for t in out['trace']))

    def test_unverified_commit_isolates_key_but_other_keys_bill(self):
        # 断电点10：T2 自称提交但无凭证；T3 已核实。B 点隔离，A/C 继续出账
        log = self.base_log()
        vouchers = [{'tx': 'T1'}, {'tx': 'T3'}]
        out = reconcile(INITIAL, copy.deepcopy(log), copy.deepcopy(log), vouchers, 10)
        self.assertIn('sample_B', out['quarantined_keys'])
        self.assertNotIn('sample_A', out['quarantined_keys'])
        # B 回退到检查点可信值 50，绝不报出 35/40
        self.assertEqual(out['billing_ledger']['sample_B'], 50)
        self.assertEqual(out['billing_ledger']['sample_A'], 60)
        self.assertEqual(out['billing_ledger']['sample_C'], 20)
        self.assertTrue(out['invariant_passed'])
        iso = {i['key']: i for i in out['isolation']}
        self.assertIn('凭证', iso['sample_B']['reason'])

    def test_voucher_completes_corrupted_commit_record(self):
        # 两份副本的 T3 commit 位置都缺失，但凭证证明 T3 已提交
        a = self.base_log()
        b = self.base_log()
        a[8] = gap()
        b[8] = gap()
        out = reconcile(INITIAL, a, b, [{'tx': 'T1'}, {'tx': 'T2'}, {'tx': 'T3'}], 11)
        self.assertIn('T3', out['committed_verified'])
        self.assertEqual(out['billing_ledger']['sample_A'], 60)
        self.assertTrue(any(t['phase'] == 'commit_completed_by_voucher' for t in out['trace']))

    def test_value_conflict_quarantines_and_verified_commit_overrides(self):
        # LSN8 两副本读数冲突 → 隔离；同事务 T3 随后凭凭证核实提交 → 覆盖存疑、解除隔离
        a = self.base_log()
        b = self.base_log()
        a[7] = w('T3', 'sample_A', 60)
        b[7] = w('T3', 'sample_A', 999)
        out = reconcile(INITIAL, a, b, [{'tx': 'T1'}, {'tx': 'T2'}, {'tx': 'T3'}], 11)
        # T3 唯一一笔写入本身冲突，无可信写入可覆盖 → 保持隔离，出账回退检查点值75
        self.assertIn('sample_A', out['quarantined_keys'])
        self.assertEqual(out['billing_ledger']['sample_A'], 75)
        # 再构造：冲突写入之后同一已核实事务有一笔可信写入覆盖
        a2 = self.base_log()
        b2 = self.base_log()
        a2[7] = w('T3', 'sample_A', 60)
        b2[7] = w('T3', 'sample_A', 999)
        a2.insert(8, w('T3', 'sample_A', 61))
        b2.insert(8, w('T3', 'sample_A', 61))
        out2 = reconcile(INITIAL, a2, b2, [{'tx': 'T1'}, {'tx': 'T2'}, {'tx': 'T3'}], 12)
        self.assertNotIn('sample_A', out2['quarantined_keys'])
        self.assertEqual(out2['billing_ledger']['sample_A'], 61)
        self.assertTrue(any(t['phase'] == 'quarantine_release' for t in out2['trace']))

    def test_checkpoint_blocked_when_quarantine_open(self):
        # 检查点位置前存在未解除隔离 → 拒绝，不让存疑值随检查点出账
        log = [
            {'op': 'begin', 'tx': 'T1'},
            w('T1', 'sample_A', 75),
            {'op': 'commit', 'tx': 'T1'},  # 无凭证 → A 隔离
            {'op': 'checkpoint'},
        ]
        with self.assertRaises(ValueError) as ctx:
            reconcile(INITIAL, copy.deepcopy(log), copy.deepcopy(log), [], 4)
        self.assertIn('检查点拒绝出账', str(ctx.exception))

    def test_abort_voucher_conflict_isolates(self):
        log = [
            {'op': 'begin', 'tx': 'T1'},
            w('T1', 'sample_A', 75),
            {'op': 'abort', 'tx': 'T1'},
        ]
        # 凭证说 T1 提交了，副本说 abort → 证据冲突，隔离 A
        out = reconcile(INITIAL, copy.deepcopy(log), copy.deepcopy(log), [{'tx': 'T1'}], 3)
        self.assertIn('sample_A', out['quarantined_keys'])
        self.assertEqual(out['billing_ledger']['sample_A'], 100)

    def test_loser_undo_and_isolation(self):
        # 崩溃点8：T2 未结束也无凭证 → 撤销 B 写入并隔离 B
        log = self.base_log()
        out = reconcile(INITIAL, copy.deepcopy(log), copy.deepcopy(log),
                        [{'tx': 'T1'}, {'tx': 'T3'}], 8)
        self.assertEqual(out['billing_ledger']['sample_B'], 50)
        self.assertIn('sample_B', out['quarantined_keys'])
        self.assertTrue(any(t['phase'] == 'undo_loser' for t in out['trace']))

    def test_both_replicas_unreadable_write(self):
        a = self.base_log()
        b = self.base_log()
        a[7] = {'op': 'write', 'tx': 'T3', 'key': 'sample_A'}  # 值缺失
        b[7] = gap()
        out = reconcile(INITIAL, a, b, [{'tx': 'T1'}, {'tx': 'T2'}, {'tx': 'T3'}], 11)
        self.assertIn('sample_A', out['quarantined_keys'])
        self.assertEqual(out['billing_ledger']['sample_A'], 75)  # 检查点可信值

    def test_validation_rejects_bad_shapes(self):
        log = self.base_log()
        with self.assertRaises(ValueError):
            validate_inputs({}, copy.deepcopy(log), copy.deepcopy(log), [], 11)
        with self.assertRaises(ValueError):
            validate_inputs(INITIAL, copy.deepcopy(log), copy.deepcopy(log)[:-1], [], 10)
        with self.assertRaises(ValueError):
            validate_inputs(INITIAL, copy.deepcopy(log), copy.deepcopy(log), [{'tx': 'T1'}], 99)
        with self.assertRaises(ValueError):
            validate_inputs(INITIAL, copy.deepcopy(log), copy.deepcopy(log), [{'tx': '!!'}], 11)

    def test_trusted_identity_conflict_rejected(self):
        a = self.base_log()
        b = self.base_log()
        a[1] = w('T1', 'sample_A', 75)
        b[1] = w('T9', 'sample_A', 75)  # 事务号是可信字段，冲突必须拒绝
        with self.assertRaises(ValueError):
            reconcile(INITIAL, a, b, [{'tx': 'T1'}], 11)


if __name__ == '__main__':
    unittest.main(verbosity=2)
