import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { cn } from "@/lib/utils";
import { Loader2Icon } from "lucide-react";

function Spinner({ className, ...props }: React.ComponentProps<"svg">) {
  return (
    <UiAttributes messages={{ "aria-label": { id: "ui.common.loading" } }}>
      <Loader2Icon
        aria-label={"Loading"}
        data-slot="spinner"
        role="status"
        className={cn("size-4 animate-spin", className)}
        {...props}
      />
    </UiAttributes>
  );
}

export { Spinner };
