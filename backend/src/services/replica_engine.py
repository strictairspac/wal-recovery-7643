"""跨副本拼合恢复模型（教学）。

两份采样日志副本都可能损坏，但事务号(tx)、点号(key)和日志位置(LSN)仍可信；
断电前留存的提交凭证(voucher)是提交事实的权威依据。引擎按 LSN 对齐两份副本，
能跨副本拼回整笔写入；核对不实的提交涉及的点先隔离，其余点继续出账；
后来核实的提交可以覆盖存疑读数；检查点绝不让存疑值出账。

与 wal_engine 一样是 Python 教学模型：不注入真实 PostgreSQL 故障，不执行物理恢复。
"""
import copy
import hashlib
import json
import re

KEY_RE = re.compile(r'[A-Za-z0-9_-]{1,50}')
TX_RE = re.compile(r'[A-Za-z0-9_-]{1,30}')
OPS = ('begin', 'write', 'commit', 'abort', 'checkpoint')
MAX_RECORDS = 200
MAX_KEYS = 30
VALUE_LIMIT = 1_000_000_000

# 隔离原因（亦作为前端展示的稳定枚举）
REASON_BOTH_GAP = '两份副本同位置均缺失，写入不可读'
REASON_VALUE_CONFLICT = '两副本读数不一致且无副本损坏标记，存疑值不采信'
REASON_TX_EVIDENCE = '事务结束证据冲突，提交无法核实'
REASON_NO_COMMIT_EVIDENCE = '提交记录缺失且无提交凭证，事务未核实'
REASON_VOUCHER_MISMATCH = '副本提交记录与提交凭证冲突'
REASON_BEGIN_MISSING = '事务开始记录双副本缺失，事务链不完整'


def _is_gap(rec):
    """gap/null/空对象均表示该副本在该位置不可读（位置本身仍可信）。"""
    return rec is None or rec == {} or rec.get('op') in (None, 'gap')


def _valid_int(value):
    return not isinstance(value, bool) and isinstance(value, int) and abs(value) <= VALUE_LIMIT


def _validate_shapes(initial, replica_a, replica_b, vouchers, crash_after):
    if not initial or len(initial) > MAX_KEYS:
        raise ValueError('需1—30个初始账本键')
    for key, value in initial.items():
        if not KEY_RE.fullmatch(key) or not _valid_int(value):
            raise ValueError('初始键为字母数字下划线短横线，值为绝对值不超过10亿的整数')
    for name, replica in (('副本A', replica_a), ('副本B', replica_b)):
        if not isinstance(replica, list) or not (1 <= len(replica) <= MAX_RECORDS):
            raise ValueError(f'{name}需为1—{MAX_RECORDS}条记录的数组（缺失位置用null占位）')
    if len(replica_a) != len(replica_b):
        raise ValueError('两份副本必须按同一日志位置对齐，长度需一致')
    if not isinstance(vouchers, list):
        raise ValueError('提交凭证需为数组')
    seen = set()
    for v in vouchers:
        if not isinstance(v, dict) or not v.get('tx') or not TX_RE.fullmatch(str(v['tx'])):
            raise ValueError('提交凭证必须包含合法事务标识 tx')
        if v['tx'] in seen:
            raise ValueError(f"事务 {v['tx']} 的提交凭证重复")
        seen.add(v['tx'])
    if not isinstance(crash_after, int) or not 0 <= crash_after <= len(replica_a):
        raise ValueError('崩溃点超出日志范围')


def _record_identity(rec):
    """提取可信骨架：操作类型、事务号、点号（值可能损坏，单独处理）。"""
    op = rec.get('op')
    tx = rec.get('tx')
    key = rec.get('key')
    value = rec.get('value')
    return op, tx, key, value


def _merge_position(lsn, ra, rb):
    """对齐单个 LSN 位置的两份副本，返回 (合并记录, 备注列表)。

    位置、事务号、点号可信；同字段冲突时优先结构更完整、且带损坏标记的副本，
    冲突无法消解则保留两份读数交由上层隔离。
    """
    notes = []
    if _is_gap(ra) and _is_gap(rb):
        return None, ['both_gap']
    if _is_gap(ra):
        notes.append('a_gap_b_readable')
        return dict(rb), notes
    if _is_gap(rb):
        notes.append('b_gap_a_readable')
        return dict(ra), notes

    op_a, tx_a, key_a, val_a = _record_identity(ra)
    op_b, tx_b, key_b, val_b = _record_identity(rb)

    # 操作类型冲突：优先非未知操作；都合法但不同则无法消解
    if op_a != op_b:
        if op_a in OPS and op_b not in OPS:
            notes.append('b_corrupt_op')
            return dict(ra), notes
        if op_b in OPS and op_a not in OPS:
            notes.append('a_corrupt_op')
            return dict(rb), notes
        raise ValueError(f'LSN {lsn}: 两副本操作类型冲突且无法消解')
    op = op_a if op_a in OPS else op_b
    if op not in OPS:
        raise ValueError(f'LSN {lsn}: 两副本操作类型均损坏')

    # 事务号（可信字段，冲突即结构损坏）
    tx = tx_a if tx_a else tx_b
    if tx_a and tx_b and tx_a != tx_b:
        raise ValueError(f'LSN {lsn}: 事务号冲突，可信字段不一致')
    if not tx and op != 'checkpoint':
        raise ValueError(f'LSN {lsn}: 事务号在两份副本均缺失')

    merged = {'op': op, 'tx': tx}
    if op == 'write':
        key = key_a if key_a else key_b
        if key_a and key_b and key_a != key_b:
            raise ValueError(f'LSN {lsn}: 点号冲突，可信字段不一致')
        if not key:
            raise ValueError(f'LSN {lsn}: 点号在两份副本均缺失')
        merged['key'] = key
        # 值的三种情形：一致 / 单副本可读 / 双可读冲突
        if val_a is not None and val_b is not None:
            if val_a == val_b and _valid_int(val_a):
                merged['value'] = val_a
            elif not _valid_int(val_a) and _valid_int(val_b):
                merged['value'] = val_b
                notes.append('a_corrupt_value')
            elif not _valid_int(val_b) and _valid_int(val_a):
                merged['value'] = val_a
                notes.append('b_corrupt_value')
            else:
                notes.append('value_conflict')
                merged['value_a'] = val_a
                merged['value_b'] = val_b
        elif val_a is not None and _valid_int(val_a):
            merged['value'] = val_a
            notes.append('b_gap_value')
        elif val_b is not None and _valid_int(val_b):
            merged['value'] = val_b
            notes.append('a_gap_value')
        else:
            notes.append('both_gap_value')
    return merged, notes


def reconcile(initial, replica_a, replica_b, vouchers, crash_after):
    """跨副本拼合并执行崩溃恢复，返回可出账账本、隔离点与完整核对轨迹。"""
    _validate_shapes(initial, replica_a, replica_b, vouchers, crash_after)
    voucher_txs = {v['tx'] for v in vouchers}
    total = len(replica_a)

    # 第一阶段：按可信位置逐格拼合，形成重建日志与每位置核对备注
    reconstructed = []
    position_notes = []
    for lsn in range(total):
        merged, notes = _merge_position(lsn + 1, replica_a[lsn], replica_b[lsn])
        reconstructed.append(merged)
        position_notes.append(notes)

    state = copy.deepcopy(initial)          # 含存疑读数的工作账本
    durable = copy.deepcopy(initial)        # 最近检查点：只装可出账值
    checkpoint_lsn = 0
    active = {}                             # tx -> {'writes':[行], 'begin_ok':bool}
    used = set()
    locks = {}
    winners = []                            # 已核实提交
    aborted = []
    quarantined = {}                        # key -> {'reason':str,'lsn':int,'tx':str,'suspect':值或None}
    trace = []
    tx_end_evidence = {}                    # tx -> 副本记录的结束方式（用于与凭证对质）

    def quarantine(key, reason, lsn, tx, suspect=None):
        # 同一存疑点只保留首次隔离原因；后来核实的提交可显式解除（见提交处理）
        if key not in quarantined:
            quarantined[key] = {'reason': reason, 'lsn': lsn, 'tx': tx, 'suspect': suspect}

    prefix = reconstructed[:crash_after]
    for idx, rec in enumerate(prefix, 1):
        if rec is None:
            trace.append({'lsn': idx, 'phase': 'gap', 'tx': None, 'key': None,
                          'note': '两副本同位置缺失，该位置无可重放记录'})
            continue
        op = rec['op']
        tx = rec.get('tx')
        notes = position_notes[idx - 1]

        if op == 'checkpoint':
            if active:
                raise ValueError(f'LSN {idx}: 仅支持无活动事务的静止检查点')
            if quarantined:
                raise ValueError(
                    f'LSN {idx}: 检查点拒绝出账，存疑点尚未解除隔离：' + '、'.join(sorted(quarantined)))
            checkpoint_lsn = idx
            durable = copy.deepcopy(state)
            trace.append({'lsn': idx, 'phase': 'checkpoint', 'tx': None, 'key': None,
                          'note': '静止检查点，全部数据页可出账'})
            continue

        if op == 'begin':
            if tx in used:
                raise ValueError(f'LSN {idx}: 事务标识不得重复')
            used.add(tx)
            active[tx] = {'writes': [], 'begin_ok': 'both_gap' not in notes}
            if 'both_gap' in notes:
                trace.append({'lsn': idx, 'phase': 'begin_reconstructed', 'tx': tx, 'key': None,
                              'note': 'begin位置双副本缺失，事务链存疑'})
            continue

        if tx not in active:
            # begin 双缺失但后续记录可拼：补建活动事务并标记链不完整
            if tx not in used:
                used.add(tx)
                active[tx] = {'writes': [], 'begin_ok': False}
            else:
                raise ValueError(f'LSN {idx}: 事务 {tx} 未开启或已结束')

        if op == 'write':
            key = rec['key']
            if key not in initial:
                raise ValueError(f'LSN {idx}: 写入点必须存在于初始账本')
            if key in locks and locks[key] != tx:
                raise ValueError(f'LSN {idx}: 违反严格写锁，活动事务持有同点')
            locks[key] = tx
            before = state[key]
            if 'value_conflict' in notes:
                quarantine(key, REASON_VALUE_CONFLICT, idx, tx,
                           suspect={'replica_a': rec.get('value_a'), 'replica_b': rec.get('value_b')})
                state[key] = rec.get('value_a')
                row = {'lsn': idx, 'tx': tx, 'key': key, 'before': before,
                       'after': state[key], 'trusted': False}
                trace.append(dict(row, phase='redo_suspect',
                                  note='两副本读数冲突，存疑值仅入工作账本并隔离该点'))
            elif 'both_gap_value' in notes or 'both_gap' in notes or 'value' not in rec:
                quarantine(key, REASON_BOTH_GAP, idx, tx)
                row = {'lsn': idx, 'tx': tx, 'key': key, 'before': before,
                       'after': None, 'trusted': False}
                trace.append(dict(row, phase='redo_unreadable',
                                  note='两副本均读不出值，该点隔离，保留上一可信值'))
            else:
                value = rec['value']
                if not _valid_int(value):
                    quarantine(key, REASON_BOTH_GAP, idx, tx, suspect=value)
                    row = {'lsn': idx, 'tx': tx, 'key': key, 'before': before,
                           'after': None, 'trusted': False}
                    trace.append(dict(row, phase='redo_unreadable',
                                      note='副本数值损坏且无另一副本可拼，该点隔离'))
                else:
                    state[key] = value
                    row = {'lsn': idx, 'tx': tx, 'key': key, 'before': before,
                           'after': value, 'trusted': True}
                    source = '跨副本拼合' if any(n.endswith('gap_a_readable') or n.endswith('gap_b_readable')
                                                 or n.endswith('gap_value') or n.endswith('corrupt_value')
                                                 for n in notes) else '两副本一致'
                    trace.append(dict(row, phase='redo', note=f'写入可信（{source}）'))
            active[tx]['writes'].append(row)
            continue

        if op in ('commit', 'abort'):
            tx_end_evidence[tx] = op
            voucher_says_commit = tx in voucher_txs
            if op == 'abort' and voucher_says_commit:
                # 凭证是断电前留存的权威：副本 abort 与凭证冲突，提交仍可核实，
                # 但该事务结束证据冲突，涉及点先隔离。
                for row in active[tx]['writes']:
                    quarantine(row['key'], REASON_VOUCHER_MISMATCH, idx, tx,
                               suspect=row['after'])
                del active[tx]
                locks = {k: v for k, v in locks.items() if v != tx}
                trace.append({'lsn': idx, 'phase': 'evidence_conflict', 'tx': tx, 'key': None,
                              'note': '副本abort与提交凭证冲突，涉及点隔离等待人工核实'})
                continue
            if op == 'commit' and not voucher_says_commit:
                # 副本自称提交但无凭证：提交核对不实，涉及点先隔离
                for row in active[tx]['writes']:
                    quarantine(row['key'], REASON_NO_COMMIT_EVIDENCE, idx, tx,
                               suspect=row['after'])
                del active[tx]
                locks = {k: v for k, v in locks.items() if v != tx}
                trace.append({'lsn': idx, 'phase': 'commit_unverified', 'tx': tx, 'key': None,
                              'note': '提交记录无凭证支撑，涉及点隔离，不参与出账'})
                continue
            if op == 'abort':
                for row in reversed(active[tx]['writes']):
                    state[row['key']] = row['before']
                    trace.append(dict(row, phase='redo_abort_undo',
                                      note='已abort事务写入逆序撤销'))
                del active[tx]
                aborted.append(tx)
                locks = {k: v for k, v in locks.items() if v != tx}
                continue
            # 核实提交（副本commit + 凭证，或仅有凭证而commit记录损坏/缺失在收尾处理）
            if not active[tx]['begin_ok']:
                for row in active[tx]['writes']:
                    quarantine(row['key'], REASON_BEGIN_MISSING, idx, tx, suspect=row['after'])
            for row in active[tx]['writes']:
                # 后来核实的提交覆盖存疑读数：可信写入解除该点隔离
                if row.get('trusted') and row['key'] in quarantined:
                    del quarantined[row['key']]
                    trace.append({'lsn': idx, 'phase': 'quarantine_release', 'tx': tx,
                                  'key': row['key'], 'note': '提交经凭证核实，覆盖此前存疑读数'})
            winners.append(tx)
            del active[tx]
            locks = {k: v for k, v in locks.items() if v != tx}
            trace.append({'lsn': idx, 'phase': 'commit_verified', 'tx': tx, 'key': None,
                          'note': '提交凭证核对一致，事务写入确认出账'})

    # 收尾：崩溃时仍活动的事务。凭证可核实提交（commit记录可能在损坏/缺失位置），
    # 否则按未结束事务撤销；凡核对不实，涉及点隔离。
    for tx in sorted(active):
        info = active[tx]
        voucher_says_commit = tx in voucher_txs
        evidence = tx_end_evidence.get(tx)
        if voucher_says_commit and evidence in (None, 'commit'):
            if not info['begin_ok']:
                for row in info['writes']:
                    quarantine(row['key'], REASON_BEGIN_MISSING, crash_after, tx, suspect=row['after'])
            for row in info['writes']:
                if row.get('trusted') and row['key'] in quarantined:
                    del quarantined[row['key']]
                    trace.append({'lsn': crash_after, 'phase': 'quarantine_release', 'tx': tx,
                                  'key': row['key'], 'note': '凭凭证补全提交，覆盖存疑读数'})
            winners.append(tx)
            trace.append({'lsn': crash_after, 'phase': 'commit_completed_by_voucher', 'tx': tx,
                          'key': None, 'note': '崩溃前提交记录损坏，提交凭证核实生效'})
        else:
            reason = REASON_TX_EVIDENCE if evidence == 'commit' else REASON_NO_COMMIT_EVIDENCE
            for row in info['writes']:
                quarantine(row['key'], reason, crash_after, tx, suspect=row.get('after'))
                state[row['key']] = row['before']
                trace.append(dict(row, phase='undo_loser',
                                  note='未核实事务逆序撤销，涉及点隔离'))
    for tx in sorted(voucher_txs - set(winners) - set(aborted)):
        trace.append({'lsn': crash_after, 'phase': 'voucher_unmatched', 'tx': tx, 'key': None,
                      'note': '凭证事务在持久化前缀中无任何写入，登记待人工核对'})

    # 出账账本：隔离点回退到最近检查点的可信值，绝不把存疑值报出去
    billing = copy.deepcopy(state)
    isolation = []
    for key in sorted(quarantined):
        info = quarantined[key]
        billing[key] = durable[key]
        isolation.append({'key': key, 'reason': info['reason'], 'lsn': info['lsn'],
                          'tx': info['tx'], 'suspect': info['suspect'],
                          'billed_value': durable[key]})

    # 独立 oracle：只从检查点账本应用“凭证核实且写入可信”的事务
    oracle = copy.deepcopy(durable)
    verified = set(winners)
    for rec in prefix:
        if rec and rec.get('op') == 'write' and rec.get('tx') in verified and 'value' in rec:
            oracle[rec['key']] = rec['value']
    invariant_passed = billing == oracle

    checksum = hashlib.sha256(
        json.dumps({'billing': billing, 'isolation': isolation}, sort_keys=True).encode()).hexdigest()
    source = json.dumps({'initial': initial, 'replica_a': replica_a, 'replica_b': replica_b,
                         'vouchers': vouchers, 'crash_after': crash_after}, sort_keys=True)
    return {
        'simulation': True,
        'model_version': 'cross-replica-voucher-v1',
        'crash_after': crash_after,
        'total_positions': total,
        'checkpoint_lsn': checkpoint_lsn,
        'durable_checkpoint': durable,
        'working_ledger': state,
        'billing_ledger': billing,
        'oracle': oracle,
        'invariant_passed': invariant_passed,
        'committed_verified': winners,
        'aborted': aborted,
        'quarantined_keys': sorted(quarantined),
        'isolation': isolation,
        'reconstructed_log': reconstructed,
        'position_notes': position_notes,
        'trace': trace,
        'voucher_txs': sorted(voucher_txs),
        'result_sha256': checksum,
        'input_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'boundary': 'Python 教学模型；两份副本的位置/事务号/点号可信、值可能损坏，'
                    '提交凭证为断电前权威依据。存疑点隔离不出账，检查点不放行存疑值。'
                    '没有注入真实PostgreSQL崩溃，未执行数据库物理恢复。',
    }


def validate_inputs(initial, replica_a, replica_b, vouchers, crash_after):
    """供录入接口在入队前做结构校验（不跑恢复）。"""
    _validate_shapes(initial, replica_a, replica_b, vouchers, crash_after)
    # 预演一次合并，尽早暴露位置级结构冲突；完整恢复在 worker 执行
    for lsn in range(len(replica_a)):
        _merge_position(lsn + 1, replica_a[lsn], replica_b[lsn])
    return True
