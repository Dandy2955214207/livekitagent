// Isolated RTC fixture; no production configuration selects this fake provider.
import { createRuntime } from '../src/runtime.mjs';
import { serveStdio } from '../src/stdio.mjs';
import { fakeProvider } from './fake-provider.mjs';
import { modelBox } from '../src/guide-monitor.mjs';
serveStdio(async(config,emit)=>{
  let scene = 'both';
  const provider=await fakeProvider({monitorReply(event){
    const ctx=JSON.parse(event.response.instructions);
    const previousFrames = ctx.preferred && ctx.target?.subject === '右上蓝色瓶子' ? ['absent', 'absent'] : undefined;
    if (scene === 'empty') return 'Reply: ' + JSON.stringify({clip:ctx.clip,state:'unknown',reason:'no_subject',previousFrames});
    if (scene === 'replacement') {
      const same = ctx.target?.subject === '中央橙色盒子';
      return 'Reply: ' + JSON.stringify({clip:ctx.clip,state:'matched',subject:'中央橙色盒子',relation:same?'same':'new',
        previous:same?'visible':'absent',previousFrames,boxes:[[250,150,750,850],[250,150,750,850]],
        evidence:[{frame:1,detail:'橙色盒子'},{frame:2,detail:'橙色盒子'}],summary:same?'':'这是一个橙色盒子。'});
    }
    const edge=ctx.selection?.point?.x>800 || !ctx.selection && ctx.target?.subject==='右上蓝色瓶子';
    const subject=edge?'右上蓝色瓶子':'中央绿色盆栽';
    const box=edge?[.84,.03,.12,.2]:[.25,.15,.5,.7];
    const same=ctx.target?.subject===subject;
    return 'Reply: '+JSON.stringify({clip:ctx.clip,state:'matched',subject,boxes:[modelBox(box),modelBox(box)],
      relation:same?'same':'new',previous:ctx.target?'visible':'absent',
      evidence:[{frame:1,detail:subject},{frame:2,detail:subject}],summary:same?'':edge?'瓶身是蓝色。':'主体是一株盆栽。'});
  }});
  const runtime=await createRuntime(config,emit,{gateway:{upstreamUrl:provider.url}});
  const handle=runtime.handle,close=runtime.close;let turned=false;
  runtime.handle=async event=>{
    if(event.type==='image'&&event.grid){
      // Read the synthetic canvas background from the actual captured frame.
      const [r,g,b]=Buffer.from(event.grid,'base64');
      scene=r>g+40&&g<100?'empty':b>r+40&&r<100?'replacement':'both';
    }
    await handle(event);
    if(event.type==='audio'&&!turned){turned=true;setTimeout(()=>provider.turn(provider.peers[0],{
      text:'持续介绍我展示的物品，只讲主体',tool:{name:'start_scene_task',arguments:JSON.stringify({mode:'guide',goal:'只讲展示的主体物品',depth:'brief'})}}),100);}
  };
  runtime.close=async()=>{await close();await provider.close();};return runtime;
});
