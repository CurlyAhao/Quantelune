"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftIcon, ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import type { Dataset } from "@/lib/types";

type Page = {
  columns: string[];
  total: number;
  rows: (string | number | boolean | null)[][];
};

const PAGE_ROWS = 20;

export function WorkspaceJsonPreview({
  dataset,
  busy,
  onPrepare,
  download,
  prepareError,
  hasTable = false,
}: {
  dataset: Dataset;
  busy: boolean;
  onPrepare: () => void;
  download: ReactNode;
  prepareError: string;
  hasTable?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const [record, setRecord] = useState<number | null>(null);
  const recordHeading = useRef<HTMLHeadingElement>(null);
  const returnRow = useRef<number | null>(null);
  const tableRegion = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["source-json", dataset.id, dataset.sha256, offset],
    queryFn: ({ signal }) =>
      apiFetch<Page>(
        `/api/v1/datasets/${dataset.id}/source-json?offset=${offset}`,
        { signal },
      ),
    retry: false,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (record !== null) recordHeading.current?.focus();
    else if (returnRow.current !== null) {
      tableRegion.current
        ?.querySelector<HTMLButtonElement>(
          `[data-record="${returnRow.current}"]`,
        )
        ?.focus();
      returnRow.current = null;
    }
  }, [record]);

  const data = query.data;
  const current = record === null ? null : data?.rows[record];
  return (
    <section className="rw-json-records" aria-label="JSON 记录">
      <div className="rw-content-tools rw-json-tools">
        {data && (
          <span>
            {data.total.toLocaleString()} 条记录 ·{" "}
            {data.columns.length.toLocaleString()} 个字段
          </span>
        )}
        <div className="rw-inline-actions">
          {download}
          {data && (
            <button
              type="button"
              className="rw-primary"
              disabled={busy}
              onClick={onPrepare}
            >
              {busy ? (hasTable ? "正在打开…" : "正在生成…") : (hasTable ? "打开分析表" : "生成分析表")}
            </button>
          )}
        </div>
      </div>
      {prepareError && (
        <p role="alert" className="rw-error">
          {prepareError}
        </p>
      )}
      {query.isPending && <p role="status">正在读取 JSON…</p>}
      {query.isError && (
        <p role="alert">
          {query.error.message}{" "}
          <button type="button" onClick={() => void query.refetch()}>
            重试
          </button>
        </p>
      )}
      {data &&
        (current && record !== null ? (
          <>
            <div className="rw-json-record-heading">
              <h3 ref={recordHeading} tabIndex={-1}>
                第 {offset + record + 1} 条记录
              </h3>
              <button
                type="button"
                onClick={() => {
                  returnRow.current = record;
                  setRecord(null);
                }}
              >
                <ArrowLeftIcon size={14} aria-hidden />
                返回表格
              </button>
            </div>
            <dl className="rw-json-fields">
              {data.columns.map((name, index) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>
                    {current[index] === null ? (
                      <span className="rw-muted">—</span>
                    ) : (
                      String(current[index])
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </>
        ) : (
          <>
            <div
              key={offset}
              ref={tableRegion}
              className="rw-table-scroll rw-record-table rw-json-table"
              tabIndex={0}
              role="region"
              aria-label="JSON 表格"
            >
              <table className="rw-data-table">
                <thead>
                  <tr>
                    <th scope="col">记录</th>
                    {data.columns.map((name) => (
                      <th scope="col" key={name} title={name}>
                        {name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, index) => (
                    <tr key={offset + index}>
                      <th scope="row">
                        <button
                          type="button"
                          data-record={index}
                          aria-label={`查看第 ${offset + index + 1} 条记录`}
                          onClick={() => setRecord(index)}
                        >
                          {offset + index + 1}
                        </button>
                      </th>
                      {row.map((value, column) => (
                        <td key={data.columns[column]}>
                          {value === null ? (
                            <span className="rw-muted">—</span>
                          ) : (
                            String(value)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <nav
              className="rw-json-pagination rw-inline-actions"
              aria-label="JSON 记录分页"
            >
              <span>
                {data.rows.length ? offset + 1 : 0}–{offset + data.rows.length}{" "}
                / {data.total.toLocaleString()}
              </span>
              <button
                type="button"
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - PAGE_ROWS))}
              >
                <ChevronLeftIcon size={14} aria-hidden />
                上一页
              </button>
              <button
                type="button"
                disabled={offset + data.rows.length >= data.total}
                onClick={() => setOffset(offset + data.rows.length)}
              >
                下一页
                <ChevronRightIcon size={14} aria-hidden />
              </button>
            </nav>
          </>
        ))}
    </section>
  );
}
