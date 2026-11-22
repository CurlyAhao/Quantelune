type AudioEntry = {
  sample?: {video_id?:string;audio_id?:string};
  segment?: {player_start:number;audio_player_start?:number;first_audio_pts_seconds?:number};
  audio_selection?: "video"|"independent";
  audio_origin_seconds?:number;
  audio_alignment?:{effective_offset_seconds?:number};
};

export function mediaAudioClock(entry?:AudioEntry) {
  const offset=entry?.audio_alignment?.effective_offset_seconds ?? 0;
  const origin=entry?.segment?.first_audio_pts_seconds!=null && entry.audio_origin_seconds!=null
    ? entry.segment.first_audio_pts_seconds-entry.audio_origin_seconds-offset
    : (entry?.segment?.audio_player_start ?? entry?.segment?.player_start ?? 0)-offset;
  const datasetId=entry?.audio_selection==="independent" ? entry.sample?.audio_id : entry?.sample?.video_id||entry?.sample?.audio_id;
  return {origin,annotationSource:datasetId?{datasetId,clockShift:-offset}:undefined};
}
