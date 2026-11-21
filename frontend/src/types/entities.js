/** @typedef {{id:number,name:string,initial:object,records:object[],description:string}} LogSet */
/** @typedef {{id:number,name:string,log_set_id:number,crash_after:number}} CrashCase */
/** @typedef {{id:number,crash_case_id:number,label:string,status:string,snapshot:object,result:object|null}} Recovery */
/** @typedef {{id:number,name:string,initial:object,replica_a:object[],replica_b:object[],vouchers:object[],crash_after:number,description:string}} ReplicaBundle */
/** @typedef {{id:number,bundle_id:number,label:string,status:string,snapshot:object,result:object|null}} ReplicaRecovery */
export {};
