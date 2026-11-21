import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc} from '../utils/html.js';
import {downloadReplica} from '../api/client.js';
import {message} from '../hooks/action.js';

function ledgerRows(r){
 const qs=new Set(r.quarantined_keys||[]);
 return Object.keys(r.billing_ledger||{}).map(k=>{
  const quarantined=qs.has(k);
  return `<tr${quarantined?' class="quarantined"':''}>
   <td>${esc(k)}${quarantined?' <span class="tag-warn">已隔离</span>':''}</td>
   <td>${r.durable_checkpoint[k]}</td>
   <td>${esc(r.working_ledger[k])}</td>
   <td><b>${r.billing_ledger[k]}</b></td>
   <td>${r.oracle[k]}</td>
   <td>${quarantined?esc(r.isolation.find(i=>i.key===k).reason):'可信出账'}</td>
  </tr>`;
 }).join('');
}

function ledgerTable(r){
 return `<div class="table-wrap"><table><thead><tr>
  <th>账本点</th><th>检查点可信值</th><th>工作账本（含存疑）</th><th>实际出账</th><th>凭证独立核对</th><th>隔离原因</th>
 </tr></thead><tbody>${ledgerRows(r)}</tbody></table></div>`;
}

function isolationTable(r){
 if(!(r.isolation||[]).length)return '<p class="notice">无隔离点，全部账本点均可正常出账。</p>';
 return table(['隔离点','隔离原因','发生LSN','相关事务','存疑读数','出账回退值'],
  r.isolation.map(i=>[esc(i.key),esc(i.reason),i.lsn,esc(i.tx),
   esc(i.suspect===null?'不可读':JSON.stringify(i.suspect)),i.billed_value]));
}

export function replicaDetailPage(job){
 if(!job)return '<section class="empty"><h2>还没有副本恢复记录</h2><p>先录入两份副本与提交凭证，再提交拼合恢复任务。</p><a href="#replicas">进入双副本拼合 →</a></section>';
 const r=job.result;
 return `<div class="section-title"><h2>${esc(job.label)} <small>#${job.id}</small></h2>${badge(job.status)}</div>
 ${r?`<section class="verdict">
   <div><p>出账账本核对</p><h2>${r.error?esc(r.error):r.invariant_passed?'与凭证核实结果一致':'核对不一致'}</h2></div>
   <p>检查点LSN ${r.checkpoint_lsn??'—'} · 崩溃点 ${r.crash_after??'—'} / ${r.total_positions??'—'} 个位置<br>
   凭证核实事务 ${esc((r.committed_verified||[]).join(', ')||'无')}<br>
   隔离点 ${esc((r.quarantined_keys||[]).join(', ')||'无')} · abort事务 ${esc((r.aborted||[]).join(', ')||'无')}</p>
  </section>
  <h2>断电后读数与出账（隔离点不报旧值/存疑值）</h2>
  ${r.error?`<p class="notice error">${esc(r.error)}</p>`:ledgerTable(r)}
  <h2>隔离原因核对</h2>${r.error?'':isolationTable(r)}
  <h2>跨副本拼合与恢复轨迹</h2>
  ${r.trace?table(['LSN','阶段','事务','点','说明 / before → after'],r.trace.map(t=>[
   t.lsn,esc(t.phase),esc(t.tx||'—'),esc(t.key||'—'),
   esc(t.note||(t.phase==='undo_loser'?`${t.after} → ${t.before}`:`${t.before} → ${t.after}`))])):''}
  <p class="checksum">输入摘要 ${esc(r.input_sha256||'无')}<br>结果摘要 ${esc(r.result_sha256||'无')}<br>模型版本 ${esc(r.model_version||'—')}</p>`
 :'<p class="notice">后台拼合计算中，请刷新状态。</p>'}
 <div class="toolbar"><button id="export">导出副本拼合恢复证据</button><button class="secondary" id="refresh">刷新状态</button></div>
 <details><summary>查看冻结的副本与凭证输入</summary><pre>${esc(JSON.stringify(job.snapshot,null,2))}</pre></details>`;
}

export function bindReplicaDetail(job,render){
 document.querySelector('#refresh')?.addEventListener('click',render);
 document.querySelector('#export')?.addEventListener('click',()=>downloadReplica(job.id).catch(e=>message(e.message,true)));
}
