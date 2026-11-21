import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {replicaBundlesAPI,replicaRecoveriesAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';

const SAMPLE_INITIAL={sample_A:100,sample_B:50};
const SAMPLE_A=[
 {op:'begin',tx:'T1'},{op:'write',tx:'T1',key:'sample_A',value:75},{op:'commit',tx:'T1'},{op:'checkpoint'},
 {op:'begin',tx:'T2'},{op:'write',tx:'T2',key:'sample_B',value:40},
 {op:'begin',tx:'T3'},{op:'write',tx:'T3',key:'sample_A',value:'DAMAGED'},{op:'commit',tx:'T3'},
 {op:'write',tx:'T2',key:'sample_B',value:35},{op:'commit',tx:'T2'}];
const SAMPLE_B=[
 {op:'begin',tx:'T1'},{op:'write',tx:'T1',key:'sample_A',value:75},{op:'commit',tx:'T1'},{op:'checkpoint'},
 {op:'begin',tx:'T2'},null,
 {op:'begin',tx:'T3'},{op:'write',tx:'T3',key:'sample_A',value:60},{op:'commit',tx:'T3'},
 {op:'write',tx:'T2',key:'sample_B',value:35},{op:'commit',tx:'T2'}];
const SAMPLE_VOUCHERS=[{tx:'T1'},{tx:'T3'}];

export function replicasPage(){
 const b=store.bundles[0];
 return `<div class="section-title"><h2>双副本凭证包</h2><span>位置/事务号/点号可信 · 值与整条记录可能损坏 · 缺失位置用 null 占位</span></div>
 ${table(['凭证包','初始账本','副本长度','崩溃点','凭证事务','说明'],store.bundles.map(x=>[
   esc(x.name),esc(JSON.stringify(x.initial)),`${x.replica_a.length} / ${x.replica_b.length}`,x.crash_after,
   esc(x.vouchers.map(v=>v.tx).join(', ')),esc(x.description)]))}
 <section class="split">
  <div>
   <h2>录入两份副本与提交凭证</h2>
   <p>两份副本按同一日志位置（LSN）对齐。引擎逐格拼合：一副本缺失取另一副本，单副本值损坏取可读副本，两副本读数冲突则该点隔离。提交凭证是断电前留存的权威依据，核对不实的提交涉及点先隔离，其余点继续出账；检查点不会放行任何存疑值。</p>
   <p class="big-note">核不实先隔离，核实后可覆盖。</p>
   <details><summary>查看示例副本（含损坏与null占位）</summary><pre>${esc(JSON.stringify({replica_a:SAMPLE_A,replica_b:SAMPLE_B,vouchers:SAMPLE_VOUCHERS},null,2))}</pre></details>
  </div>
  ${session.role!=='viewer'?`<form id="bundle-form">
   <label>凭证包名<input name="name" value="shift-20261117-双副本" required></label>
   <label>说明<input name="description" value="断电后双副本拼合复核"></label>
   <label>初始账本 JSON<textarea name="initial" rows="2" required>${esc(JSON.stringify(b?.initial||SAMPLE_INITIAL))}</textarea></label>
   <label>副本 A JSON（损坏值可保留原值，缺失位置填 null）<textarea name="replica_a" rows="7" required>${esc(JSON.stringify(b?.replica_a||SAMPLE_A,null,1))}</textarea></label>
   <label>副本 B JSON（与副本A等长、按位置对齐）<textarea name="replica_b" rows="7" required>${esc(JSON.stringify(b?.replica_b||SAMPLE_B,null,1))}</textarea></label>
   <label>断电前提交凭证 JSON<textarea name="vouchers" rows="2" required>${esc(JSON.stringify(b?.vouchers||SAMPLE_VOUCHERS))}</textarea></label>
   <label>崩溃点（已持久化日志条数）<input name="crash_after" type="number" min="0" max="200" value="${b?.crash_after??10}" required></label>
   <button type="submit">校验并保存凭证包</button>
  </form>`:'<p class="readonly">观察员仅查看与导出，新增需要操作员或管理员。</p>'}
 </section>
 <div class="section-title"><h2>副本拼合恢复队列</h2><button class="secondary" id="refresh">刷新状态</button></div>
 ${session.role!=='viewer'?`<form id="replica-run-form" class="inline-form"><label>凭证包<select name="bundle_id">${store.bundles.map(x=>`<option value="${x.id}">${esc(x.name)} · 崩溃点${x.crash_after}</option>`).join('')}</select></label>
  <label>记录标签<input name="label" value="双副本断电核对" required></label><button type="submit">冻结凭证包并拼合恢复 →</button></form>`:''}
 ${table(['编号','恢复记录','状态','出账核对','隔离点','证据'],store.replicaRuns.map(r=>[
   r.id,esc(r.label),badge(r.status),
   r.result?.invariant_passed===true?'与凭证核实结果一致':r.result?.error?esc(r.result.error):r.result?'存在隔离点':'待计算',
   esc((r.result?.quarantined_keys||[]).join(', ')||'无'),
   `<a href="#replica-detail/${r.id}">查看拼合轨迹 →</a>`]))}`;
}

export function bindReplicas(render){
 document.querySelector('#refresh')?.addEventListener('click',render);
 action(document.querySelector('#bundle-form'),async f=>{
  const d=formObject(f);
  d.initial=JSON.parse(d.initial);
  d.replica_a=JSON.parse(d.replica_a);
  d.replica_b=JSON.parse(d.replica_b);
  d.vouchers=JSON.parse(d.vouchers);
  d.crash_after=Number(d.crash_after);
  await replicaBundlesAPI.create(d);
  await render();
 });
 action(document.querySelector('#replica-run-form'),async f=>{
  const d=formObject(f);
  d.bundle_id=Number(d.bundle_id);
  const job=await replicaRecoveriesAPI.create(d);
  location.hash='#replica-detail/'+job.id;
 });
}
