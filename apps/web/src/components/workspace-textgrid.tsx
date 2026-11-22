"use client";
import {useEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {apiFetch,apiUpload,apiBlob} from "@/lib/api-client";
import {saveGeneratedFile} from "@/lib/api-download";
import {SearchSelect} from "@/components/ui/search-select";
import {useWorkspaceAnnotations} from "./workspace-annotations";
import type {Material} from "@/lib/material-types";
import styles from "./workspace-textgrid.module.css";
import {audioTime} from "@/lib/audio-time";

type Item={start:string;end:string|null;text:string};
type Tier={type:"IntervalTier"|"TextTier";name:string;start:string;end:string;items:Item[];theme_id:string|null;generation?:{praat:string;original_start:number;original_end:number;preprocessing?:{kind:string}|null;parameters:{threshold_db:number;minimum_silence:number;minimum_sounding:number;minimum_pitch:number;time_step:number;subtract_mean:boolean}}|null};
type Grid={id:string;revision:number;selection?:{tier:number;item:number|null}|null;payload:{title:string;duration:string;start:string;end:string;offset:string;tiers:Tier[]}};
type Selection={tier:number;item:number};
const stamp=audioTime;

function LayerMenu({layer,index,count,busy,revision,update}:{layer:Tier;index:number;count:number;busy:boolean;revision:number;update:(body:Record<string,unknown>)=>Promise<void>}) {
  const [name,setName]=useState(layer.name);
  const menu=useRef<HTMLDetailsElement>(null);
  useEffect(()=>setName(layer.name),[layer.name]);
  useEffect(()=>{if(menu.current)menu.current.open=false;},[revision]);
  return <details ref={menu} className={styles.layerMenu}><summary aria-label={layer.name+" 层操作"}>层操作</summary>
    <div className={styles.layerTools}>
      <label>层名<input aria-label={layer.name+" 层名"} value={name} onChange={event=>setName(event.target.value)}/></label>
      <button type="button" disabled={busy||!name.trim()||name===layer.name} onClick={()=>void update({action:"rename_tier",tier:index,name})}>保存层名</button>
      <button type="button" disabled={busy||index===0} onClick={()=>void update({action:"move_tier",tier:index,position:index-1})}>上移</button>
      <button type="button" disabled={busy||index===count-1} onClick={()=>void update({action:"move_tier",tier:index,position:index+1})}>下移</button>
      <button type="button" disabled={busy} onClick={()=>void update({action:"add_tier",tier_type:"IntervalTier",position:index+1})}>添加区间层</button>
      <button type="button" disabled={busy} onClick={()=>void update({action:"add_tier",tier_type:"TextTier",position:index+1})}>添加点层</button>
      <button type="button" disabled={busy} onClick={()=>void update({action:"delete_tier",tier:index})}>删除此层</button>
    </div>
  </details>;
}

export function TextGridTracks({material,range,cursor,selection,clockShift=0,onLocate,audioInput}:{audioInput?:{datasetId:string;packageId?:string;sampleId?:string};material:Material;range:[number,number];cursor:number|null;selection?:{start:string;end:string};clockShift?:number;onLocate:(start:number,end?:number,play?:boolean)=>void}) {
  const client=useQueryClient(),input=useRef<HTMLInputElement>(null);
  const annotations=useWorkspaceAnnotations();
  const queryKey=["textgrids",material.id,material.sha256,material.version];
  const grids=useQuery({queryKey,queryFn:({signal})=>apiFetch<Grid[]>("/api/v1/materials/"+material.id+"/textgrids",{signal})});
  const [id,setId]=useState(""),[open,setOpen]=useState(false),[file,setFile]=useState<File|null>(null),[offset,setOffset]=useState("0");
  const [exportOpen,setExportOpen]=useState(false),[format,setFormat]=useState("long_text");
  const [selected,setSelected]=useState<Selection|null>(null),[text,setText]=useState(""),[start,setStart]=useState(""),[end,setEnd]=useState("");
  const [activeTier,setActiveTier]=useState(0),[textSplit,setTextSplit]=useState<number|null>(null),[undo,setUndo]=useState<{id:string;revision:number}|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  const [segmentOpen,setSegmentOpen]=useState(false);
  const [segmentOptions,setSegmentOptions]=useState({minimum_pitch:75,time_step:0.01,subtract_mean:true,threshold_db:-25,minimum_silence:0.1,minimum_sounding:0.05});
  const grid=grids.data?.find(item=>item.id===id)||grids.data?.[0],tier=selected&&grid?.payload.tiers[selected.tier],item=tier&&selected?tier.items[selected.item]:undefined;
  const shift=Number(grid?.payload.offset||0)+clockShift;
  const width=range[1]-range[0]||1;
  const x=(time:number)=>(time-range[0])/width*100;
  useEffect(()=>{setId("");setSelected(null);setActiveTier(0);setUndo(null);setFile(null);setOpen(false);setExportOpen(false);setError("");setNotice("");},[material.id,material.sha256,material.version]);
  function choose(g:Grid,selection:Selection,locate=true) {
    const value=g.payload.tiers[selection.tier].items[selection.item];
    setActiveTier(selection.tier);setSelected(selection);setText(value.text);setTextSplit(null);setStart(value.start);setEnd(value.end||"");setError("");setNotice("");
    if(locate)onLocate(Number(value.start)+Number(g.payload.offset)+clockShift,value.end===null?undefined:Number(value.end)+Number(g.payload.offset)+clockShift);
  }
  async function create(){
    if(busy)return;setBusy(true);setError("");setNotice("");
    try{
      const result=await apiFetch<Grid>("/api/v1/materials/"+material.id+"/textgrids/create",{method:"POST",body:"{}"});
      saved(result);setSelected(null);setActiveTier(0);setUndo(null);setOpen(false);
    }catch(reason){setError(reason instanceof Error?reason.message:"未能新建时间标注，请重试。");}finally{setBusy(false);}
  }
  function saved(result:Grid){
    client.setQueryData<Grid[]>(queryKey,old=>[result,...(old||[]).filter(item=>item.id!==result.id)]);
    setId(result.id);setNotice("已保存 · 版本 "+result.revision);
  }
  async function importFile(){
    if(!file||busy)return;setBusy(true);setError("");setNotice("");
    try{
      const form=new FormData();form.append("file",file);form.append("offset",offset);
      const result=await apiUpload<Grid>("/api/v1/materials/"+material.id+"/textgrids",form);
      saved(result);setSelected(null);setActiveTier(0);setUndo(null);setFile(null);setOpen(false);
    }catch(reason){setError(reason instanceof Error?reason.message:"导入失败，请重新选择 TextGrid。");}finally{setBusy(false);}
  }
  async function update(body:Record<string,unknown>){
    if(!grid||busy)return;setBusy(true);setError("");setNotice("");
    try{
      const result=await apiFetch<Grid>("/api/v1/textgrids/"+grid.id,{method:"PATCH",body:JSON.stringify({revision:grid.revision,...body})});
      saved(result);setUndo(body.action==="restore"?null:{id:grid.id,revision:grid.revision});
      const focus=result.selection;
      if(focus){setActiveTier(focus.tier);if(focus.item!==null)choose(result,{tier:focus.tier,item:focus.item},false);else setSelected(null);}
      else setSelected(null);
      setNotice((body.action==="restore"?"已撤回 · 版本 ":"已保存 · 版本 ")+result.revision);
    }
    catch(reason){setError(reason instanceof Error?reason.message:"未能保存，请重试。");}finally{setBusy(false);}
  }
  async function segment(){
    if(!audioInput||busy)return;setBusy(true);setError("");setNotice("正在按强度分段…");
    try{
      const result=await apiFetch<Grid>("/api/v1/materials/"+material.id+"/textgrids/intensity",{method:"POST",body:JSON.stringify({
        ...segmentOptions,dataset_id:audioInput.datasetId,package_id:audioInput.packageId,sample_id:audioInput.sampleId,
        record_id:grid?.id,revision:grid?.revision,start:selection?Number(selection.start):null,end:selection?Number(selection.end):null})});
      saved(result);setUndo(grid?{id:grid.id,revision:grid.revision}:null);setActiveTier(result.selection?.tier??0);setSelected(null);setSegmentOpen(false);
    }catch(reason){setNotice("");setError(reason instanceof Error?reason.message:"分段失败，请检查选段。");}finally{setBusy(false);}
  }
  function split(ti:number){
    if(cursor===null||!grid)return;
    const current=selected?.tier===ti&&item?.end!=null&&Number(item.start)+shift<cursor&&cursor<Number(item.end)+shift;
    void update({action:"split_interval",tier:ti,start:String(cursor),clock_shift:String(clockShift),
      ...(current?{item:selected!.item,text,text_split:textSplit}:{})});
  }
  function textCaret(target:HTMLTextAreaElement){
    setTextSplit(target.selectionStart===target.selectionEnd?Array.from(target.value.slice(0,target.selectionStart)).length:null);
  }
  const linkedTheme=(id:string|null)=>annotations?.state?.payload.themes.find(theme=>theme.id===id||theme.merged_ids?.includes(id||""));
  async function download(){
    if(!grid)return;setError("");setBusy(true);
    try{
      const controller=new AbortController();
      const blob=await apiBlob("/api/v1/textgrids/"+grid.id+"/export?format="+format,controller.signal);
      const outcome=await saveGeneratedFile(blob,grid.payload.title.replace(/\.TextGrid$/i,"")+"-v"+grid.revision+".TextGrid",controller.signal,undefined,"zh-CN");
      if(outcome==="saved"){setNotice("TextGrid 已导出");setExportOpen(false);}
    }catch(reason){setError(reason instanceof Error?reason.message:"导出失败。");}finally{setBusy(false);}
  }
  return <section className={styles.root} aria-label="时间标注">
    <div className={styles.header}><strong>时间标注</strong><button type="button" disabled={busy} onClick={()=>void create()}>新建时间标注</button><button type="button" onClick={()=>setOpen(value=>!value)}>导入 TextGrid</button>
      {audioInput&&<button type="button" disabled={busy} aria-expanded={segmentOpen} onClick={()=>setSegmentOpen(v=>!v)}>按强度分段</button>}
      {grid&&<button type="button" onClick={()=>setExportOpen(value=>!value)} aria-expanded={exportOpen} disabled={busy}>导出 TextGrid</button>}
      {grid&&undo?.id===grid.id&&<button type="button" disabled={busy} onClick={()=>void update({action:"restore",restore_revision:undo.revision})}>撤回上次操作</button>}
      {notice&&<span role="status">{notice}</span>}
    </div>
    {segmentOpen&&<fieldset className={styles.segmentation} disabled={busy}><legend>按强度分段</legend><p>{selection?"当前播放音轨 · "+stamp(Number(selection.start))+"–"+stamp(Number(selection.end))+" 秒":"当前播放音轨 · 全段"}</p>
      <div className={styles.segmentFields}>{([
        ["threshold_db","低于峰值的阈值（dB）",-100,-0.001,1],
        ["minimum_silence","最短静音（秒）",0,100,.01],
        ["minimum_sounding","最短有声（秒）",0,100,.01],
      ] as const).map(([key,label,min,max,step])=><label key={key}>{label}<input type="number" aria-label={label} value={segmentOptions[key]} min={min} max={max} step={step} onChange={e=>setSegmentOptions(v=>({...v,[key]:Number(e.target.value)}))}/></label>)}</div>
      <details><summary>强度参数</summary><div className={styles.segmentFields}>
        <label>最低周期频率 Hz<input type="number" min={1} max={1000} value={segmentOptions.minimum_pitch} onChange={e=>setSegmentOptions(v=>({...v,minimum_pitch:Number(e.target.value)}))}/></label>
        <label>帧步长（秒）<input type="number" min={.001} step={.001} value={segmentOptions.time_step} onChange={e=>setSegmentOptions(v=>({...v,time_step:Number(e.target.value)}))}/></label>
        <label><input type="checkbox" checked={segmentOptions.subtract_mean} onChange={e=>setSegmentOptions(v=>({...v,subtract_mean:e.target.checked}))}/>扣除局部均值</label>
      </div></details><button type="button" disabled={busy||segmentOptions.threshold_db>=0||segmentOptions.minimum_silence<0||segmentOptions.minimum_sounding<0||segmentOptions.minimum_pitch<=0||segmentOptions.time_step<=0} onClick={()=>void segment()}>{busy?"正在分段…":"生成建议层"}</button>
    </fieldset>}
    {grid&&exportOpen&&<div className={styles.header}>
      <label>格式 <SearchSelect aria-label="TextGrid 导出格式" value={format} onChange={event=>setFormat(event.target.value)}>
        <option value="long_text">长文本（默认）</option><option value="short_text">短文本</option><option value="binary">二进制</option>
      </SearchSelect></label>
      <button type="button" disabled={busy} onClick={()=>void download()}>{busy?"正在导出…":"保存 TextGrid"}</button>
    </div>}
    {open&&<div className={styles.importer}>
      <span>录音：{material.filename}</span>
      <input ref={input} type="file" accept=".TextGrid,.textgrid" aria-label="选择 TextGrid" onChange={event=>setFile(event.target.files?.[0]||null)}/>
      <label>时间偏移（秒）<input type="number" step="any" value={offset} onChange={event=>setOffset(event.target.value)}/></label>
      <button type="button" disabled={!file||busy} onClick={()=>void importFile()}>{busy?"正在导入…":"导入到此录音"}</button>
    </div>}
    {(error||grids.isError)&&<p role="alert">{error||grids.error?.message}</p>}
    {grid&&<>
      <div className={styles.header}>
        {grids.data!.length>1?<SearchSelect aria-label="时间标注文件" value={grid.id} onChange={event=>{setId(event.target.value);setSelected(null);setActiveTier(0);setUndo(null);}}>{grids.data!.map(value=><option key={value.id} value={value.id}>{value.payload.title} · 版本 {value.revision}</option>)}</SearchSelect>:<span>{grid.payload.title} · 版本 {grid.revision}</span>}
        <span>{grid.payload.tiers.length} 层 · {grid.payload.start}–{grid.payload.end} 秒{Number(grid.payload.offset)!==0?" · 导入偏移 "+grid.payload.offset+" 秒":""}{clockShift!==0?" · 录音时间 "+(clockShift>0?"+":"")+clockShift+" 秒 = 当前横轴":""}</span>
      </div>
      {!grid.payload.tiers.length&&<div className={styles.header}><button type="button" disabled={busy} onClick={()=>void update({action:"add_tier",tier_type:"IntervalTier"})}>添加区间层</button><button type="button" disabled={busy} onClick={()=>void update({action:"add_tier",tier_type:"TextTier"})}>添加点层</button></div>}
      {grid.payload.tiers.map((layer,ti)=><div className={styles.layer} key={ti}>
        <div className={styles.layerTitle}><button type="button" className={styles.tierSelect} aria-pressed={activeTier===ti} onClick={()=>{setActiveTier(ti);setSelected(null);}}>{layer.name} · {layer.type==="IntervalTier"?"区间":"点"}</button>
          {annotations&&<SearchSelect aria-label={layer.name+" 关联主题"} value={linkedTheme(layer.theme_id)?.id||layer.theme_id||""} onChange={event=>void update({tier:ti,theme_id:event.target.value||null})}>
            <option value="">不关联主题</option>{layer.theme_id&&!linkedTheme(layer.theme_id)&&<option value={layer.theme_id}>原关联主题（已不可用）</option>}{(annotations.state?.payload.themes||[]).filter(theme=>theme.is_codable!==false).map(theme=><option key={theme.id} value={theme.id}>{theme.name}</option>)}
          </SearchSelect>}
        </div>
        <LayerMenu layer={layer} index={ti} count={grid.payload.tiers.length} busy={busy} revision={grid.revision} update={update}/>
        {activeTier===ti&&<div className={styles.actions}>
          {layer.type==="IntervalTier"?<>
            <button type="button" disabled={busy||cursor===null} title={textSplit===null?"原文字保留在左侧，新增右侧留空":"按标签中的文字光标分配到两侧"} onClick={()=>split(ti)}>{selected?.tier===ti&&textSplit!==null?"按文字光标拆分":"在光标处分割"}</button>
            <button type="button" disabled={busy||!selection} onClick={()=>void update({action:"range_interval",tier:ti,start:selection!.start,end:selection!.end,clock_shift:String(clockShift)})}>选段建区间</button>
          </>:<button type="button" disabled={busy||cursor===null} onClick={()=>void update({action:"add_point",tier:ti,start:String(cursor),clock_shift:String(clockShift)})}>在光标添加点</button>}
          <span>{cursor!==null?"光标 "+stamp(cursor)+" 秒":"在波形上点击定位"}{selection?" · 选段 "+stamp(Number(selection.start))+"–"+stamp(Number(selection.end))+" 秒":""}</span>
        </div>}
        <div className={styles.lane}>
          {layer.items.map((value,ii)=>{
            const a=Number(value.start)+shift,b=value.end===null?a:Number(value.end)+shift;
            if(b<range[0]||a>range[1])return null;
            const label=value.text||(value.end===null?"未命名点":"空白区间");
            return <button type="button" key={ii} className={value.end===null?styles.point:styles.interval}
              aria-label={layer.name+" · "+label+" · "+value.start+(value.end===null?"":"–"+value.end)+" 秒"}
              aria-pressed={selected?.tier===ti&&selected?.item===ii}
              style={{left:Math.max(0,x(a))+"%",...(value.end===null?{}:{width:Math.max(.2,x(Math.min(b,range[1]))-Math.max(0,x(a)))+"%"})}}
              title={label+" · "+value.start+(value.end===null?"":"–"+value.end)+" 秒"}
              onClick={()=>choose(grid,{tier:ti,item:ii})}>{value.end===null?<><i/>{value.text||"未命名点"}</>:label}</button>;
          })}
          {cursor!==null&&cursor>=range[0]&&cursor<=range[1]&&<div className={styles.cursor} style={{left:x(cursor)+"%"}}/>}
        </div>
      </div>)}
      {grid.payload.tiers[activeTier]?.generation&&<details><summary>分段方法</summary>{(()=>{const g=grid.payload.tiers[activeTier].generation!;return <p>Praat {g.praat} 强度分段 · {g.preprocessing?"滤波音轨":"原声音轨"} · 原录音 {stamp(g.original_start)}–{stamp(g.original_end)} 秒 · 低于峰值 {Math.abs(g.parameters.threshold_db)} dB · 最短静音 {g.parameters.minimum_silence} 秒 · 最短有声 {g.parameters.minimum_sounding} 秒 · 最低周期频率 {g.parameters.minimum_pitch} Hz · 帧步长 {g.parameters.time_step*1000} ms · {g.parameters.subtract_mean?"扣除":"不扣除"}局部均值。</p>;})()}</details>}
      <div className={styles.axis}>{[0,.25,.5,.75,1].map(f=><span key={f}>{stamp(range[0]+width*f)} 秒</span>)}</div>
      {selected&&tier&&item&&<div className={styles.editor}>
        <strong>{tier.name} · {item.end===null?"点标注":"区间 "+(selected.item+1)}{Number(grid.payload.offset)!==0?" · 下列时间加 "+grid.payload.offset+" 秒对应原录音":""}</strong>
        <label className={styles.labelText}>标签<textarea rows={2} aria-label="时间标注标签" value={text} onChange={event=>{setText(event.target.value);setTextSplit(null);}} onClick={event=>textCaret(event.currentTarget)} onKeyUp={event=>textCaret(event.currentTarget)}/></label>
        <label>{item.end===null?"时间（秒）":"开始（秒）"}<input type="number" step="any" aria-label="标注开始时间" value={start} onChange={event=>setStart(event.target.value)} readOnly={tier.type==="IntervalTier"&&selected.item===0}/></label>
        {item.end!==null&&<label>结束（秒）<input type="number" step="any" aria-label="标注结束时间" value={end} onChange={event=>setEnd(event.target.value)} readOnly={selected.item===tier.items.length-1}/></label>}
        <button type="button" onClick={()=>onLocate(Number(item.start)+shift,item.end===null?undefined:Number(item.end)+shift,true)}>{item.end===null?"回听附近":"回听此区间"}</button>
        <button type="button" disabled={busy} onClick={()=>void update({tier:selected.tier,item:selected.item,text,start,...(item.end!==null?{end}:{})})}>{busy?"正在保存…":"保存标注"}</button>
        <button type="button" disabled={busy} onClick={()=>choose(grid,selected)}>撤销未保存修改</button>
        {item.end===null?<button type="button" disabled={busy} onClick={()=>void update({action:"delete_point",tier:selected.tier,item:selected.item})}>删除此点</button>:<>
          <button type="button" disabled={busy||selected.item===0} onClick={()=>void update({action:"merge_previous",tier:selected.tier,item:selected.item})}>与前段合并</button>
          <button type="button" disabled={busy||selected.item===tier.items.length-1} onClick={()=>void update({action:"merge_next",tier:selected.tier,item:selected.item})}>与后段合并</button>
        </>}
      </div>}
    </>}
  </section>;
}
