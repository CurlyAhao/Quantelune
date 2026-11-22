"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Popover } from "@base-ui/react/popover";
import { ChevronDown, Maximize, Minus, Plus } from "lucide-react";
import { plotsApi } from "@/lib/plots-api";
import { downloadApiFile, readApiFileBlob } from "@/lib/api-download";
import type { TaskPlotRun } from "@/lib/task-plot-groups";
import { DownloadButton } from "./download-button";

export function PlotMenu({label, children, title, chevron = true}: {chevron?: boolean; label: ReactNode; title: string; children: (close: () => void) => ReactNode}) {
  const [trigger, setTrigger] = useState<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger ref={setTrigger} title={title} aria-label={title}>{label}{chevron && <ChevronDown size={13}/>}</Popover.Trigger>
    <Popover.Portal className="qp-menu-portal" container={trigger?.closest<HTMLElement>(".rw-shell")} keepMounted>
      <Popover.Positioner className="qp-positioner" align="end" sideOffset={6} collisionPadding={10}>
        <Popover.Popup aria-label={title} className="qp-menu">{children(() => setOpen(false))}</Popover.Popup>
      </Popover.Positioner>
    </Popover.Portal>
  </Popover.Root>;
}

export function PlotDownloads({project,run}: {project:string;run:TaskPlotRun}) {
  return <>{(["png", "svg", "pdf"] as const).map(format=><DownloadButton key={format}
    identity={run.id+":"+format} download={signal=>downloadApiFile(plotsApi.file(project,run.id,format),run.payload.title+"."+format,signal)}>
    {format.toUpperCase()}
  </DownloadButton>)}</>;
}

const steps = [50,75,100,125,150,200,300,400];
export function PlotZoom({zoom,change}: {zoom:number;change:(percent:number)=>void}) {
  const i=steps.indexOf(zoom);
  return <div className="qp-zoom" role="group" aria-label="图表预览缩放">
    <button disabled={i===0} aria-label="缩小图表" onClick={()=>change(steps[i-1])}><Minus size={15}/></button>
    <output aria-live="polite" title="相对于适应窗口的倍率">{zoom}%</output>
    <button disabled={i===steps.length-1} aria-label="放大图表" onClick={()=>change(steps[i+1])}><Plus size={15}/></button>
    <button className="qp-fit" aria-pressed={zoom===100} onClick={()=>change(100)}><Maximize size={14}/>适应窗口</button>
  </div>;
}

export function PlotViewport({project,run,zoom}: {project:string;run:TaskPlotRun;zoom:number}) {
  const [image,setImage]=useState<{url:string;width:number;height:number}>();
  const [error,setError]=useState("");
  const scroll=useRef<HTMLDivElement>(null);
  const drag=useRef<{x:number;y:number;left:number;top:number} | null>(null);
  useEffect(()=>{
    const controller=new AbortController();
    let url="";
    if(run.payload.status==="completed") void readApiFileBlob(plotsApi.file(project,run.id,"png"),controller.signal).then(blob=>{
      if(controller.signal.aborted)return;
      url=URL.createObjectURL(blob);
      const pic=new Image();
      pic.onload=()=>{if(!controller.signal.aborted)setImage({url,width:pic.naturalWidth,height:pic.naturalHeight});};
      pic.onerror=()=>{if(!controller.signal.aborted)setError("无法显示这份图形。");};
      pic.src=url;
    }).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:"图形无法读取。");});
    return ()=>{controller.abort();if(url)URL.revokeObjectURL(url);};
  },[project,run.id,run.revision,run.payload.status]);
  const center=useRef({x:.5,y:.5});
  useLayoutEffect(()=>{
    const el=scroll.current;
    if(!el)return;
    const point=zoom===100?{x:.5,y:.5}:center.current;
    el.scrollLeft=point.x*el.scrollWidth-el.clientWidth/2;
    el.scrollTop=point.y*el.scrollHeight-el.clientHeight/2;
  },[zoom,image]);
  const factor=zoom/100;
  const ratio=image?image.width/image.height:1;
  const width=image?`min(calc(${zoom}cqw - ${20*factor}px), calc(${zoom*ratio}cqh - ${20*factor*ratio}px), ${image.width*factor}px)`:undefined;
  return <div className="qp-stage"><div ref={scroll} className="qp-scroll" data-zoomed={zoom>100} tabIndex={0} aria-label="图表预览"
    onScroll={e=>{const el=e.currentTarget;center.current={x:(el.scrollLeft+el.clientWidth/2)/el.scrollWidth,y:(el.scrollTop+el.clientHeight/2)/el.scrollHeight};}}
    onPointerDown={e=>{const el=e.currentTarget;if(e.button!==0||e.pointerType==="touch"||(el.scrollWidth<=el.clientWidth&&el.scrollHeight<=el.clientHeight))return;
      drag.current={x:e.clientX,y:e.clientY,left:el.scrollLeft,top:el.scrollTop};el.setPointerCapture(e.pointerId);el.dataset.dragging="true";}}
    onPointerMove={e=>{if(drag.current){e.currentTarget.scrollLeft=drag.current.left+drag.current.x-e.clientX;e.currentTarget.scrollTop=drag.current.top+drag.current.y-e.clientY;}}}
    onLostPointerCapture={e=>{drag.current=null;delete e.currentTarget.dataset.dragging;}}>
    <div className="qp-sheet">{error||run.payload.error_message?<p role="alert">{error||run.payload.error_message}</p>:image?
      // eslint-disable-next-line @next/next/no-img-element -- Use the authenticated, saved plot artifact.
      <img src={image.url} alt={run.payload.title} draggable={false} style={{width}}/>:<p role="status">{run.payload.status==="completed"?"正在读取图形…":"图形尚未完成"}</p>}</div>
  </div></div>;
}
