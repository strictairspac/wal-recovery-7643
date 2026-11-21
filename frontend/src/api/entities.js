import {api} from './client.js';
export const logSetsAPI={list:()=>api('/log-sets'),create:data=>api('/log-sets',{method:'POST',body:JSON.stringify(data)})};
export const crashCasesAPI={list:()=>api('/crash-cases'),create:data=>api('/crash-cases',{method:'POST',body:JSON.stringify(data)})};
export const recoveriesAPI={list:()=>api('/recoveries'),get:id=>api('/recoveries/'+id),create:data=>api('/recoveries',{method:'POST',body:JSON.stringify(data)})};
export const replicaBundlesAPI={list:()=>api('/replica-bundles'),create:data=>api('/replica-bundles',{method:'POST',body:JSON.stringify(data)})};
export const replicaRecoveriesAPI={list:()=>api('/replica-recoveries'),get:id=>api('/replica-recoveries/'+id),create:data=>api('/replica-recoveries',{method:'POST',body:JSON.stringify(data)})};
