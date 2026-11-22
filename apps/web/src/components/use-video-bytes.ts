import { useQuery } from "@tanstack/react-query";
import { materialApi } from "@/lib/material-api";
import type { Material } from "@/lib/material-types";

// Both views read the same local recording. Keep its bytes briefly across tab switches.
export function useVideoBytes(material?: Material) {
  return useQuery({
    queryKey: ["material-video-bytes", material?.id, material?.sha256, material?.version],
    enabled: !!material?.mime_type.startsWith("video/"),
    queryFn: async ({signal}) => {
      const blob = await materialApi.blob(material!.id, signal);
      if (blob.type.split(";")[0] !== material!.mime_type.split(";")[0]) {
        throw new Error("视频格式与材料记录不一致，请重新打开原文件。");
      }
      return blob;
    },
    staleTime: Infinity, gcTime: 15_000, retry: false,
  });
}
