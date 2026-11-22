"use client";

import * as React from "react";
import { ChevronDownIcon } from "lucide-react";
import { createPortal } from "react-dom";

type Option = { value: string; label: string; disabled: boolean; selected: boolean; group: string };

/** Native select-compatible interface: option children and change events remain unchanged. */
export function SearchSelect({ children, className, style, id: controlId, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  const selectRef = React.useRef<HTMLSelectElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);
  const id = React.useId();
  const [options, setOptions] = React.useState<Option[]>([]);
  const optionsSnapshot = React.useRef("");
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const [position, setPosition] = React.useState<React.CSSProperties>({});
  const sync = React.useCallback(() => {
    const element = selectRef.current;
    if (!element) return;
    const next = Array.from(element.options, option => ({ value: option.value, label: option.text, disabled: option.disabled || (option.parentElement instanceof HTMLOptGroupElement && option.parentElement.disabled), selected: option.selected, group: option.parentElement instanceof HTMLOptGroupElement ? option.parentElement.label : "" }));
    const snapshot = JSON.stringify(next);
    if (optionsSnapshot.current !== snapshot) {
      optionsSnapshot.current = snapshot;
      setOptions(next);
    }
  }, []);
  React.useLayoutEffect(sync);
  React.useEffect(() => {
    const element = selectRef.current;
    if (!element) return;
    const observer = new MutationObserver(sync);
    observer.observe(element, { childList: true, subtree: true, attributes: true, characterData: true });
    return () => observer.disconnect();
  }, [sync]);
  React.useEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = inputRef.current?.getBoundingClientRect();
      if (!rect) return;
      const below = window.innerHeight - rect.bottom;
      const menuWidth=Math.min(window.innerWidth-8,Math.max(rect.width,360));
      const height = Math.max(80, Math.min(240, Math.max(below, rect.top) - 8));
      setPosition({ position: "fixed", left: Math.max(4, Math.min(rect.left, window.innerWidth - menuWidth - 4)), width: menuWidth, maxHeight: height, ...(below >= height || below >= rect.top ? { top: rect.bottom + 4 } : { bottom: window.innerHeight - rect.top + 4 }) });
    };
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!inputRef.current?.contains(target) && !menuRef.current?.contains(target)) { setOpen(false); setQuery(""); }
    };
    place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true); document.addEventListener("pointerdown", outside);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); document.removeEventListener("pointerdown", outside); };
  }, [open]);
  const filtered = options.filter(option => option.label.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const enabled = filtered.filter(option => !option.disabled);
  React.useEffect(() => {
    if (open && enabled[active]) document.getElementById(`${id}-${options.indexOf(enabled[active])}`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active, id, options, query]);
  const selected = options.filter(option => option.selected).map(option => option.label).join(", ");
  const choose = (option: Option) => {
    const element = selectRef.current;
    if (!element || option.disabled) return;
    if (element.multiple) {
      const target = Array.from(element.options).find(item => item.value === option.value);
      if (target) target.selected = !target.selected;
    } else element.value = option.value;
    element.dispatchEvent(new Event("change", { bubbles: true }));
    sync();
    if (!element.multiple) { setOpen(false); setQuery(""); inputRef.current?.focus(); }
  };
  return <span className={`sf-search-select ${className ?? ""}`} style={style}>
    <input ref={inputRef} id={controlId} role="combobox" aria-label={props["aria-label"]} aria-labelledby={props["aria-labelledby"]} aria-expanded={open} aria-controls={id} aria-autocomplete="list" aria-activedescendant={open && enabled[active] ? `${id}-${options.indexOf(enabled[active])}` : undefined} disabled={props.disabled} autoComplete="off" title={selected} value={open ? query : selected} placeholder={open ? "搜索选项" : "请选择"}
      onFocus={() => { setOpen(true); setQuery(""); setActive(0); }}
      onClick={() => { if (!open) { setOpen(true); setQuery(""); } }}
      onChange={event => { setQuery(event.target.value); setActive(0); setOpen(true); }}
      onKeyDown={event => {
        if (event.key === "Escape") { setOpen(false); setQuery(""); event.preventDefault(); }
        else if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); if (!open) setOpen(true); else if (enabled.length) setActive(current => (current + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length); }
        else if (event.key === "Enter" && open) { event.preventDefault(); if (enabled[active]) choose(enabled[active]); }
        else if (event.key === "Tab") { setOpen(false); setQuery(""); }
      }} />
    <select {...props} ref={selectRef} tabIndex={-1} aria-hidden="true" style={{ display: "none" }}>{children}</select>
    <ChevronDownIcon className="sf-search-select-arrow" size={16} strokeWidth={1.6} aria-hidden="true" data-open={open} />
    {open && typeof document !== "undefined" && createPortal(<div ref={menuRef} className="sf-search-select-menu" style={position} id={id} role="listbox" aria-multiselectable={props.multiple || undefined}>
      {filtered.length ? filtered.map((option, index) => <React.Fragment key={`${option.value}-${index}`}>
        {option.group && (index === 0 || filtered[index - 1].group !== option.group) && <div className="sf-search-select-group">{option.group}</div>}
        <div id={`${id}-${options.indexOf(option)}`} role="option" aria-selected={option.selected} aria-disabled={option.disabled || undefined} className={`sf-search-select-option ${enabled[active] === option ? "is-active" : ""} ${option.disabled ? "is-disabled" : ""}`} onMouseDown={event => event.preventDefault()} onMouseEnter={() => { const i = enabled.indexOf(option); if (i >= 0) setActive(i); }} onClick={() => choose(option)}><span>{option.label}</span>{option.selected && <span aria-hidden="true">✓</span>}</div>
      </React.Fragment>) : <div className="sf-search-select-empty">无匹配选项</div>}
    </div>, inputRef.current?.closest("dialog[open]") ?? document.body)}
  </span>;
}
