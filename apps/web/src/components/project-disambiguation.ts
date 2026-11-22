type ProjectLabel = {title:string; workspace_path?:string|null};

/** Use the shortest distinct directory suffix when project titles collide. */
export function projectDirectory(project:ProjectLabel, projects:ProjectLabel[]):string {
  const peers=projects.filter(item=>item.title===project.title);
  if (peers.length<2) return "";
  const parts=(project.workspace_path||"").split(/[\\/]+/).filter(Boolean);
  if (!parts.length) return "路径不可用";
  for (let count=1;count<=parts.length;count++) {
    const suffix=parts.slice(-count).join("/");
    if (peers.filter(item=>(item.workspace_path||"").split(/[\\/]+/).filter(Boolean).slice(-count).join("/")===suffix).length===1) return suffix;
  }
  return parts.join("/");
}

export function projectOptionTitle(project:ProjectLabel, projects:ProjectLabel[]):string {
  const directory=projectDirectory(project, projects);
  if (!directory) return project.title;
  if (directory===project.title || directory.startsWith(`${project.title} (`)) return directory;
  return `${project.title} · ${directory}`;
}
