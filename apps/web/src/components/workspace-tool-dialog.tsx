"use client";

import {useEffect, useId, useRef, type ReactNode} from "react";
import {XIcon} from "lucide-react";

export function WorkspaceToolDialog({open, onClose, title, subtitle, children}: {
  open:boolean; onClose:()=>void; title:string; subtitle?:string; children:ReactNode;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const titleId=useId();
  useEffect(()=>{
    const element=dialog.current;
    if(open && element && !element.open) element.showModal();
    else if(!open && element?.open) element.close();
  },[open]);
  return <dialog ref={dialog} className="rw-tool-dialog" aria-labelledby={titleId}
    onCancel={event=>{event.preventDefault();onClose();}}>
    <header><div><h2 id={titleId}>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div>
      <button type="button" aria-label="关闭并返回素材" onClick={onClose}><XIcon size={18}/></button>
    </header>
    <div className="rw-tool-body">{children}</div>
    <footer><button type="button" onClick={onClose}>返回工作台</button></footer>
  </dialog>;
}
