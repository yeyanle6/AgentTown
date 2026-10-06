import { json,localMode,localRequest,rawDb,sameOrigin,CommandSchema } from '@/lib/community-server';
import { getChatGPTUser } from '@/app/chatgpt-auth';
export async function POST(request:Request){
 try{
  if(!sameOrigin(request))return json({error:'跨站写入被拒绝'},403);
  if(!request.headers.get('content-type')?.includes('application/json'))return json({error:'需要 JSON 请求'},415);
  if(Number(request.headers.get('content-length')||0)>20000)return json({error:'请求过大'},413);
  const text=await request.text();if(text.length>20000)return json({error:'请求过大'},413);
  const command=CommandSchema.parse(JSON.parse(text));const id=command.id||crypto.randomUUID();
  if(localMode())return await localRequest('/api/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...command,id})});
  if(!await getChatGPTUser())return json({error:'请先登录以干预社区'},401);
  const db=rawDb();const state=await db.prepare("SELECT payload FROM community_snapshots WHERE id='current'").first<{payload:string}>();
  if(!state)return json({error:'执行器尚未连接'},503);
  const snapshot=JSON.parse(state.payload);
  if(['resume','tick','create_task'].includes(command.kind) && (snapshot.meta.state==='completed'||Date.now()>=Date.parse(snapshot.meta.stop_at)))return json({error:'本次运行已达到截止时间'},409);
  if(command.kind==='create_task'){
   const p=command.payload;const templates=['market_review','market_research','release_research','price_stats','source_validator','portfolio_metrics','ledger_audit'];
   if(!templates.includes(String(p.template))||!Number.isInteger(p.budget)||Number(p.budget)<20||Number(p.budget)>500)return json({error:'请选择支持的任务类型，并填写20–500整数预算'},400);
  }
  await db.prepare('INSERT OR IGNORE INTO community_commands(id,kind,payload,status,created_at) VALUES(?,?,?,\'pending\',?)').bind(id,command.kind,JSON.stringify(command.payload),new Date().toISOString()).run();
  return json({id,status:'queued',message:'干预已保存，等待执行器确认。'});
 }catch(error){return json({error:error instanceof Error?error.message:'操作失败'},400);}
}
export async function GET(request:Request){try{const id=new URL(request.url).searchParams.get('id');if(!id)return json({error:'缺少命令ID'},400);if(localMode())return json({status:'applied'});return json(await rawDb().prepare('SELECT id,status,result FROM community_commands WHERE id=?').bind(id).first()||{status:'missing'});}catch{return json({error:'无法读取干预结果'},503);}}
